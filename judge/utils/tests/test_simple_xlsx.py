import io
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from judge.utils.simple_xlsx import (
    MAX_XLSX_UPLOAD_BYTES,
    MAX_XLSX_XML_PART_BYTES,
    build_xlsx_workbook,
    read_xlsx_rows,
    validate_xlsx_upload,
)


class SimpleXlsxTestCase(SimpleTestCase):
    def test_build_and_read_round_trip(self):
        payload = build_xlsx_workbook([
            ('Problems', [['name', 'description'], ['A', 'B']]),
        ])

        rows = read_xlsx_rows(io.BytesIO(payload))

        self.assertEqual(rows, [['name', 'description'], ['A', 'B']])

    def test_validate_xlsx_upload_rejects_oversized_file(self):
        uploaded = SimpleUploadedFile(
            'big.xlsx',
            b'x' * (MAX_XLSX_UPLOAD_BYTES + 1),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

        with self.assertRaisesMessage(ValueError, 'The Excel file is too large.'):
            validate_xlsx_upload(uploaded)

    def test_read_xlsx_rows_rejects_oversized_xml_part(self):
        oversized_xml = b'a' * (MAX_XLSX_XML_PART_BYTES + 1)
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('[Content_Types].xml', oversized_xml)

        with self.assertRaisesMessage(ValueError, 'The Excel file is too large.'):
            read_xlsx_rows(io.BytesIO(payload.getvalue()))
