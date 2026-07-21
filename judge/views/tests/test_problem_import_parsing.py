import io
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from judge.views.problem_import import _parse_text_test_data, _parse_zip_test_data


class ProblemImportTestDataParsingTestCase(SimpleTestCase):
    def test_parses_multiple_text_test_data_pairs(self):
        cases = _parse_text_test_data(
            'input data 1: 2 3\noutput data 1: 5\ninput data 2: hello\nworld\noutput data 2: ok',
        )
        self.assertEqual(cases, [
            ('case001.in', '2 3', 'case001.out', '5'),
            ('case002.in', 'hello\nworld', 'case002.out', 'ok'),
        ])

    def test_parses_zip_test_data_pairs(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, 'w') as archive:
            archive.writestr('sample.in', '1 2')
            archive.writestr('sample.out', '3')
        uploaded = SimpleUploadedFile('tests.zip', payload.getvalue(), content_type='application/zip')

        archive_payload, cases = _parse_zip_test_data(uploaded)

        self.assertEqual(archive_payload, payload.getvalue())
        self.assertEqual(cases, [('sample.in', b'1 2', 'sample.out', b'3')])

    def test_rejects_unpaired_zip_test_data(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, 'w') as archive:
            archive.writestr('sample.in', '1 2')
        uploaded = SimpleUploadedFile('tests.zip', payload.getvalue(), content_type='application/zip')

        with self.assertRaisesMessage(ValueError, 'matching .in and .out'):
            _parse_zip_test_data(uploaded)
