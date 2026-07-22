import io
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from judge.views.problem_import import _parse_fill_blanks, _parse_text_test_data, _parse_zip_test_data, \
    _fill_blanks_from_signs, _multiple_choice_answer_indices, _remove_multiple_choice_option_block, _serialize_fill_blanks


class ProblemImportTestDataParsingTestCase(SimpleTestCase):
    def test_maps_multiple_choice_excel_answers_without_llm_correctness(self):
        self.assertEqual(_multiple_choice_answer_indices('B|3', ['one', 'two', 'three']), [2, 3])

    def test_maps_blank_signs_to_pipe_separated_answers(self):
        description, blanks = _fill_blanks_from_signs(
            'The capital is [[blank]] and 2 + 2 = [[blank]].', 'Paris|4',
        )
        self.assertEqual(description, 'The capital is **[Blank 1]** and 2 + 2 = **[Blank 2]**.')
        self.assertEqual(blanks, [('Blank 1', 'Paris'), ('Blank 2', '4')])

    def test_rejects_mismatched_blank_sign_answers(self):
        with self.assertRaisesMessage(ValueError, 'separated by |'):
            _fill_blanks_from_signs('A [[blank]] B [[blank]]', 'one')

    def test_removes_only_the_exact_multiple_choice_option_block(self):
        description = 'Which number is prime?\nA. 4\nB. 5\nExplain your answer.'
        self.assertEqual(
            _remove_multiple_choice_option_block(description, 'A. 4\nB. 5'),
            'Which number is prime?\nExplain your answer.',
        )

    def test_keeps_description_when_option_block_is_not_exact(self):
        description = 'Which number is prime?\nA. 4\nB. 5'
        self.assertEqual(_remove_multiple_choice_option_block(description, 'A. four\nB. five'), description)

    def test_parses_labeled_fill_blanks_with_multiple_answers(self):
        blanks = _parse_fill_blanks(
            '[blank 1]\nlabel: Country\nanswers:\nFrance\nFrench Republic\n---\n'
            '[blank 2]\nlabel: Capital\nanswers:\nParis',
        )
        self.assertEqual(blanks, [('Country', 'France\nFrench Republic'), ('Capital', 'Paris')])

    def test_serializes_fill_blanks_in_the_preview_format(self):
        value = _serialize_fill_blanks([
            {'label': 'Country', 'answers': ['France', 'French Republic']},
            {'label': '', 'answers': ['Paris']},
        ])
        self.assertEqual(_parse_fill_blanks(value), [('Country', 'France\nFrench Republic'), ('', 'Paris')])

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
