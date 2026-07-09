import io
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape


MAIN_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG_REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'


def _excel_column_name(index):
    result = ''
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _sheet_xml(rows, shared_string_lookup):
    sheet_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row):
            if value is None or value == '':
                continue
            text = str(value)
            string_index = shared_string_lookup[text]
            ref = '{}{}'.format(_excel_column_name(col_index), row_index)
            cells.append('<c r="{ref}" t="s"><v>{idx}</v></c>'.format(ref=ref, idx=string_index))
        sheet_rows.append('<row r="{idx}">{cells}</row>'.format(idx=row_index, cells=''.join(cells)))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="{main}">'
        '<sheetData>{rows}</sheetData>'
        '</worksheet>'
    ).format(main=MAIN_NS, rows=''.join(sheet_rows))


def build_xlsx_workbook(sheets):
    shared_strings = []
    shared_string_lookup = {}
    total_string_count = 0

    for _, rows in sheets:
        for row in rows:
            for value in row:
                if value is None or value == '':
                    continue
                text = str(value)
                total_string_count += 1
                if text not in shared_string_lookup:
                    shared_string_lookup[text] = len(shared_strings)
                    shared_strings.append(text)

    shared_strings_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="{main}" count="{count}" uniqueCount="{unique}">{items}</sst>'
    ).format(
        main=MAIN_NS,
        count=total_string_count,
        unique=len(shared_strings),
        items=''.join('<si><t xml:space="preserve">{}</t></si>'.format(escape(text)) for text in shared_strings),
    )

    workbook_sheets = []
    workbook_rels = []
    content_type_overrides = [
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
        '<Override PartName="/xl/sharedStrings.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>',
    ]

    for index, (sheet_name, _) in enumerate(sheets, start=1):
        workbook_sheets.append(
            '<sheet name="{name}" sheetId="{idx}" r:id="rId{idx}"/>'.format(
                name=escape(sheet_name),
                idx=index,
            )
        )
        workbook_rels.append(
            '<Relationship Id="rId{idx}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet{idx}.xml"/>'.format(idx=index)
        )
        content_type_overrides.append(
            '<Override PartName="/xl/worksheets/sheet{idx}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'.format(
                idx=index,
            )
        )

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>{sheets}</sheets></workbook>'
    ).format(main=MAIN_NS, rel=REL_NS, sheets=''.join(workbook_sheets))

    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="{pkg_rel}">{sheet_rels}'
        '<Relationship Id="rId{styles}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        '<Relationship Id="rId{strings}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
        'Target="sharedStrings.xml"/>'
        '</Relationships>'
    ).format(
        pkg_rel=PKG_REL_NS,
        sheet_rels=''.join(workbook_rels),
        styles=len(sheets) + 1,
        strings=len(sheets) + 2,
    )

    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '{overrides}'
        '</Types>'
    ).format(overrides=''.join(content_type_overrides))

    root_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="{pkg_rel}">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        '</Relationships>'
    ).format(pkg_rel=PKG_REL_NS)

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="{main}">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
        '<cellXfs count="1"><xf xfId="0"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    ).format(main=MAIN_NS)

    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', content_types_xml)
        archive.writestr('_rels/.rels', root_rels_xml)
        archive.writestr('xl/workbook.xml', workbook_xml)
        archive.writestr('xl/_rels/workbook.xml.rels', workbook_rels_xml)
        archive.writestr('xl/styles.xml', styles_xml)
        archive.writestr('xl/sharedStrings.xml', shared_strings_xml)
        for index, (_, rows) in enumerate(sheets, start=1):
            archive.writestr('xl/worksheets/sheet{}.xml'.format(index), _sheet_xml(rows, shared_string_lookup))
    return output.getvalue()


def _load_shared_strings(archive):
    try:
        payload = archive.read('xl/sharedStrings.xml')
    except KeyError:
        return []

    root = ET.fromstring(payload)
    namespace = {'x': MAIN_NS}
    result = []
    for item in root.findall('x:si', namespace):
        text = ''.join(node.text or '' for node in item.iterfind('.//x:t', namespace))
        result.append(text)
    return result


def _find_first_sheet_path(archive):
    workbook = ET.fromstring(archive.read('xl/workbook.xml'))
    rels = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
    namespace = {'x': MAIN_NS, 'r': REL_NS, 'pr': PKG_REL_NS}
    first_sheet = workbook.find('x:sheets/x:sheet', namespace)
    if first_sheet is None:
        raise ValueError('Workbook does not contain any sheets.')

    rel_id = first_sheet.attrib.get('{%s}id' % REL_NS)
    for relation in rels.findall('pr:Relationship', namespace):
        if relation.attrib.get('Id') == rel_id:
            target = relation.attrib.get('Target', '')
            if target.startswith('/'):
                return target.lstrip('/')
            return 'xl/{}'.format(target.lstrip('/'))
    raise ValueError('Workbook sheet relationship is missing.')


def _cell_column_index(cell_ref):
    letters = ''.join(ch for ch in cell_ref if ch.isalpha())
    value = 0
    for ch in letters:
        value = value * 26 + (ord(ch.upper()) - 64)
    return value - 1


def _cell_value(cell, shared_strings, namespace):
    cell_type = cell.attrib.get('t')
    if cell_type == 'inlineStr':
        return ''.join(node.text or '' for node in cell.iterfind('.//x:t', namespace))

    raw_value = cell.findtext('x:v', default='', namespaces=namespace)
    if cell_type == 's':
        if raw_value == '':
            return ''
        return shared_strings[int(raw_value)]
    if cell_type == 'b':
        return 'TRUE' if raw_value == '1' else 'FALSE'
    return raw_value or ''


def read_xlsx_rows(file_obj):
    with zipfile.ZipFile(file_obj) as archive:
        shared_strings = _load_shared_strings(archive)
        sheet_path = _find_first_sheet_path(archive)
        sheet = ET.fromstring(archive.read(sheet_path))

    namespace = {'x': MAIN_NS}
    rows = []
    for row in sheet.findall('x:sheetData/x:row', namespace):
        values = {}
        max_col = -1
        for cell in row.findall('x:c', namespace):
            cell_ref = cell.attrib.get('r', '')
            if not cell_ref:
                continue
            col_index = _cell_column_index(cell_ref)
            values[col_index] = _cell_value(cell, shared_strings, namespace)
            max_col = max(max_col, col_index)
        if max_col < 0:
            rows.append([])
            continue
        rows.append([values.get(index, '') for index in range(max_col + 1)])
    return rows
