"""Read teacher-authored descriptions embedded in an Excel workbook.

Excel stores ``Insert -> Object -> Create from file`` attachments as OLE packages
inside the XLSX zip. The worksheet's VML anchor tells us which spreadsheet row
owns each attachment, so an embedded document can safely augment that row's
``description`` field.
"""
import io
import posixpath
import re
import struct
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from judge.utils import simple_xlsx


@dataclass
class EmbeddedDescription:
    row_number: int
    filename: str
    text: str


def read_xlsx_rows_with_embedded_descriptions(file_obj):
    """Return first-sheet rows and text extracted from row-anchored attachments."""
    rows = simple_xlsx.read_xlsx_rows(file_obj)
    file_obj.seek(0)
    with zipfile.ZipFile(file_obj) as archive:
        simple_xlsx._validate_archive_members(archive)
        sheet_path = simple_xlsx._find_first_sheet_path(archive)
        sheet = ET.fromstring(simple_xlsx._safe_archive_read(archive, sheet_path))
        rels = _relationship_map(archive, sheet_path)
        anchors = _shape_rows(archive, sheet, rels)
        attachments = []
        namespace = {'x': simple_xlsx.MAIN_NS, 'r': simple_xlsx.REL_NS}
        for ole in sheet.findall('x:oleObjects/x:oleObject', namespace):
            relation_id = ole.attrib.get('{%s}id' % simple_xlsx.REL_NS)
            shape_id = ole.attrib.get('shapeId', '')
            target = rels.get(relation_id)
            row_number = anchors.get(shape_id)
            if not target or row_number is None:
                raise ValueError('An embedded description file is not attached to a worksheet row.')
            payload = simple_xlsx._safe_archive_read(archive, target)
            filename, content = _extract_embedded_file(payload)
            attachments.append(EmbeddedDescription(row_number=row_number, filename=filename,
                                                    text=_description_text(filename, content)))
    return rows, attachments


def _relationship_map(archive, source_path):
    directory, filename = posixpath.split(source_path)
    rel_path = posixpath.join(directory, '_rels', filename + '.rels')
    try:
        root = ET.fromstring(simple_xlsx._safe_archive_read(archive, rel_path))
    except KeyError:
        return {}
    namespace = {'pr': simple_xlsx.PKG_REL_NS}
    result = {}
    for relation in root.findall('pr:Relationship', namespace):
        target = relation.attrib.get('Target', '')
        if target and not target.startswith('/'):
            target = posixpath.normpath(posixpath.join(directory, target))
        result[relation.attrib.get('Id')] = target.lstrip('/')
    return result


def _shape_rows(archive, sheet, relationships):
    namespace = {'x': simple_xlsx.MAIN_NS, 'r': simple_xlsx.REL_NS}
    legacy = sheet.find('x:legacyDrawing', namespace)
    if legacy is None:
        return {}
    vml_path = relationships.get(legacy.attrib.get('{%s}id' % simple_xlsx.REL_NS))
    if not vml_path:
        return {}
    root = ET.fromstring(simple_xlsx._safe_archive_read(archive, vml_path))
    result = {}
    for shape in root.iter():
        shape_id = shape.attrib.get('id', '')
        match = re.search(r'(\d+)$', shape_id)
        if not match:
            continue
        anchor = next((node.text for node in shape.iter() if node.tag.endswith('}Anchor')), None)
        if not anchor:
            continue
        parts = [part.strip() for part in anchor.split(',')]
        if len(parts) >= 3 and parts[2].isdigit():
            result[match.group(1)] = int(parts[2]) + 1  # Excel anchors are zero-based.
    return result


def _extract_embedded_file(payload):
    """Return the filename and body of a direct or OLE-packaged office attachment."""
    if payload.startswith(b'PK\x03\x04'):
        return 'embedded.docx', payload
    try:
        import olefile
    except ImportError as error:
        raise ValueError('Embedded Office files require the olefile dependency.') from error

    try:
        container = olefile.OleFileIO(io.BytesIO(payload))
    except Exception as error:
        raise ValueError('The embedded description file is invalid.') from error
    try:
        streams = container.listdir(streams=True, storages=False)
        candidates = [stream for stream in streams if stream[-1].lower() in ('\x01ole10native', 'package')]
        if not candidates:
            candidates = streams
        for stream in candidates:
            body = container.openstream(stream).read()
            filename = _embedded_filename(body)
            if filename:
                return filename, _native_payload(body, filename)
    finally:
        container.close()
    raise ValueError('The embedded file must be a DOCX or Markdown document.')


def _embedded_filename(payload):
    for match in re.finditer(rb'([^\x00]{1,255}\.(?:docx|md|markdown))\x00', payload, re.IGNORECASE):
        return match.group(1).decode('utf-8', 'replace').split('\\')[-1].split('/')[-1]
    return ''


def _native_payload(payload, filename):
    if filename.lower().endswith('.docx'):
        start = payload.find(b'PK\x03\x04')
        if start < 0:
            raise ValueError('The embedded DOCX file is invalid.')
        return payload[start:]

    # Ole10Native writes a 32-bit payload length immediately before the final
    # attachment bytes. Searching from the end avoids depending on version-specific
    # metadata fields while still requiring an exact stream boundary.
    for offset in range(len(payload) - 4, -1, -1):
        size = struct.unpack_from('<I', payload, offset)[0]
        if offset + 4 + size == len(payload):
            return payload[offset + 4:]
    raise ValueError('The embedded Markdown file is invalid.')


def _description_text(filename, payload):
    if filename.lower().endswith('.docx'):
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                root = ET.fromstring(archive.read('word/document.xml'))
        except (KeyError, ET.ParseError, zipfile.BadZipFile) as error:
            raise ValueError('The embedded DOCX file is invalid.') from error
        paragraphs = []
        for paragraph in root.iter():
            if paragraph.tag.endswith('}p'):
                text = ''.join(node.text or '' for node in paragraph.iter() if node.tag.endswith('}t')).strip()
                if text:
                    paragraphs.append(text)
        return '\n\n'.join(paragraphs)
    try:
        return payload.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
    except UnicodeDecodeError as error:
        raise ValueError('Embedded Markdown must use UTF-8 encoding.') from error
