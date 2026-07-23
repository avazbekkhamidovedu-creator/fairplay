import io
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from judge.utils.excel_embedded_documents import read_xlsx_rows_with_embedded_files
from judge.utils.simple_xlsx import build_xlsx_workbook
from judge.views.problem_import import (
    _extract_labeled_multiple_choice_options, _fill_blanks_from_signs, _header_name, _multiple_choice_answer_indices,
    _parse_fill_blanks, _parse_text_test_data,
    _parse_zip_test_data, _remove_multiple_choice_option_block, _serialize_fill_blanks,
)


class ProblemImportTestDataParsingTestCase(SimpleTestCase):
    def test_reads_the_problems_sheet_when_instructions_come_first(self):
        workbook = build_xlsx_workbook([
            ('Instructions', [['Import Instructions']]),
            ('Problems', [['name*', 'problem_type*', 'description*'], ['Example', 'code', 'Body']]),
        ])

        rows, attachments = read_xlsx_rows_with_embedded_files(io.BytesIO(workbook))

        self.assertEqual(rows[0], ['name*', 'problem_type*', 'description*'])
        self.assertEqual(rows[1], ['Example', 'code', 'Body'])
        self.assertEqual(attachments, [])

    def test_normalizes_new_template_column_headers(self):
        self.assertEqual(_header_name('problem_type*'), 'question_type')
        self.assertEqual(_header_name('tag（less than 20 characters, spaced with commas)'), 'tags')
        self.assertEqual(_header_name('difficulty\n(easy:0; medium:1; difficult:2)'), 'difficulty')
        self.assertEqual(_header_name('explanation'), 'solution')

    def test_maps_multiple_choice_excel_answers_without_llm_correctness(self):
        self.assertEqual(_multiple_choice_answer_indices('B|3', ['one', 'two', 'three']), [2, 3])

    def test_extracts_consecutive_lettered_multiple_choice_options(self):
        options, block = _extract_labeled_multiple_choice_options(
            'Which number is prime?\nA. 4\nB. 5\nC. 6\nExplain your answer.',
        )

        self.assertEqual(options, ['4', '5', '6'])
        self.assertEqual(block, 'A. 4\nB. 5\nC. 6')

    def test_extracts_unlabeled_options_when_answer_uses_letter_indexes(self):
        options, block = _extract_labeled_multiple_choice_options(
            'What is 1 + 2?\n\n2\n\n3\n\n4\n\n5\n\nHere is a figure:',
            'B|C',
        )

        self.assertEqual(options, ['2', '3', '4', '5'])
        self.assertEqual(block, '2\n\n3\n\n4\n\n5')

    def test_does_not_extract_incomplete_lettered_multiple_choice_options(self):
        self.assertEqual(
            _extract_labeled_multiple_choice_options('Question\nA. one\nC. three'),
            ([], ''),
        )

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
