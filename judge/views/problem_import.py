"""Review-first Excel import for code, multiple-choice, and fill-blank problems."""
import errno
import io
import json
import os
import re
import zipfile
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View

from judge import llm
from judge.llm import LLMError
from judge.models import Language, Problem, ProblemBlank, ProblemData, ProblemFillBlankConfig, ProblemImportDraft, \
    ProblemImportItem, ProblemMCQConfig, ProblemMCQOption, ProblemSubmissionType, ProblemTestCase, \
    Solution, problem_data_storage
from judge.utils.excel_embedded_documents import read_xlsx_rows_with_embedded_files
from judge.utils.language_matching import matching_languages
from judge.utils.problem_create import apply_simple_problem_defaults, default_problem_type
from judge.utils.problem_data import ProblemDataCompiler
from judge.utils.simple_xlsx import validate_xlsx_upload


QUESTION_TYPE_MAP = {
    'code': ProblemSubmissionType.CODE,
    'c': ProblemSubmissionType.CODE,
    'multiple_choice': ProblemSubmissionType.MCQ,
    'multiple choice': ProblemSubmissionType.MCQ,
    'mcq': ProblemSubmissionType.MCQ,
    'm': ProblemSubmissionType.MCQ,
    'fill_blank': ProblemSubmissionType.FILL_BLANK,
    'fill in the blank': ProblemSubmissionType.FILL_BLANK,
    'fill-in-the-blank': ProblemSubmissionType.FILL_BLANK,
    'b': ProblemSubmissionType.FILL_BLANK,
}


def _require_creator(request):
    if not request.user.has_perm('judge.create_private_problem'):
        raise PermissionDenied


def _draft_for(request, draft_id):
    _require_creator(request)
    return get_object_or_404(ProblemImportDraft, pk=draft_id, owner=request.user.profile)


def _normalise_type(value):
    key = re.sub(r'\s+', ' ', str(value or '').strip().lower().replace('-', '_'))
    try:
        return QUESTION_TYPE_MAP[key]
    except KeyError:
        raise ValueError('question_type must be code, multiple_choice, or fill_blank.')


def _value(row, headers, name):
    index = headers.get(name)
    return str(row[index]).strip() if index is not None and index < len(row) and row[index] is not None else ''


def _serialize_fill_blanks(blanks):
    """Store labeled blanks in a reviewable text format without a schema migration."""
    blocks = []
    for index, blank in enumerate(blanks, start=1):
        answers = [str(answer).strip() for answer in blank.get('answers', []) if str(answer).strip()]
        if not answers:
            continue
        lines = ['[blank {}]'.format(index)]
        label = str(blank.get('label', '')).strip()
        if label:
            lines.append('label: {}'.format(label))
        lines.append('answers:')
        lines.extend(answers)
        blocks.append('\n'.join(lines))
    return '\n---\n'.join(blocks)


def _parse_fill_blanks(value):
    """Return (label, accepted_answers) pairs from the preview's blank format.

    Legacy blocks that contain only answer lines are still accepted and have no label.
    """
    result = []
    for raw_block in re.split(r'\n\s*---\s*\n', value or ''):
        lines = [line.strip() for line in raw_block.splitlines() if line.strip()]
        if not lines:
            continue
        if not re.match(r'^\[blank(?:\s+\d+)?\]$', lines[0], re.IGNORECASE):
            result.append(('', '\n'.join(lines)))
            continue
        label = ''
        answers = []
        reading_answers = False
        for line in lines[1:]:
            if not reading_answers and line.lower().startswith('label:'):
                label = line.split(':', 1)[1].strip()
            elif line.lower().startswith('answers:'):
                reading_answers = True
                first_answer = line.split(':', 1)[1].strip()
                if first_answer:
                    answers.append(first_answer)
            elif reading_answers:
                answers.append(line)
            else:
                raise ValueError('Each formatted blank needs an "answers:" line.')
        if not answers:
            raise ValueError('Each blank needs at least one accepted answer.')
        result.append((label, '\n'.join(answers)))
    return result


class ProblemImportUploadView(LoginRequiredMixin, View):
    """Convert a workbook into an editable, server-side preview."""
    def post(self, request):
        _require_creator(request)
        uploaded = request.FILES.get('excel_file')
        if uploaded is None or not (uploaded.name or '').lower().endswith('.xlsx'):
            return JsonResponse({'ok': False, 'message': _('Please choose an .xlsx Excel file.')}, status=400)
        try:
            validate_xlsx_upload(uploaded)
            rows, embedded = read_xlsx_rows_with_embedded_files(uploaded)
        except zipfile.BadZipFile:
            return JsonResponse({'ok': False, 'message': _('The Excel file is invalid.')}, status=400)
        except (ET.ParseError, KeyError, ValueError) as error:
            return JsonResponse({'ok': False, 'message': str(error)}, status=400)

        if not rows:
            return JsonResponse({'ok': False, 'message': _('The Excel file is empty.')}, status=400)
        headers = {str(value).strip().lower(): index for index, value in enumerate(rows[0])}
        missing = [name for name in ('name', 'question_type') if name not in headers]
        if missing:
            return JsonResponse({'ok': False, 'message': _('Missing required columns: %(columns)s.') % {
                'columns': ', '.join(missing),
            }}, status=400)

        files_by_row = {}
        for document in embedded:
            files_by_row.setdefault(document.row_number, []).append(document)
        try:
            with transaction.atomic():
                draft = ProblemImportDraft.objects.create(owner=request.user.profile)
                count = 0
                for row_number, row in enumerate(rows[1:], start=2):
                    name = _value(row, headers, 'name')
                    type_value = _value(row, headers, 'question_type')
                    description = _value(row, headers, 'description')
                    attachments = files_by_row.get(row_number, [])
                    description_files = [document for document in attachments if document.text]
                    zip_files = [document for document in attachments if document.filename.lower().endswith('.zip')]
                    if not any((name, type_value, description, attachments)):
                        continue
                    if not name:
                        raise ValueError('Row {} is missing "name".'.format(row_number))
                    question_type = _normalise_type(type_value)
                    parts = [description] if description else []
                    parts.extend(document.text for document in description_files)
                    if not parts:
                        raise ValueError('Row {} must contain description text or an embedded DOCX/Markdown file.'.format(
                            row_number))
                    if zip_files and question_type != ProblemSubmissionType.CODE:
                        raise ValueError('Row {} has an embedded ZIP, but only code problems can use test data ZIPs.'.format(
                            row_number))
                    if len(zip_files) > 1:
                        raise ValueError('Row {} has more than one embedded test data ZIP.'.format(row_number))
                    test_data_zip = None
                    if zip_files:
                        zip_attachment = zip_files[0]
                        # Validate the archive before storing it in the review draft.
                        _parse_zip_test_data(ContentFile(zip_attachment.content, name=zip_attachment.filename))
                        test_data_zip = ContentFile(zip_attachment.content, name=zip_attachment.filename)
                    answer = _value(row, headers, 'answer')
                    blanks = _value(row, headers, 'blanks')
                    ProblemImportItem.objects.create(
                        draft=draft,
                        row_number=row_number,
                        name=name,
                        submission_type=question_type,
                        description='\n\n'.join(parts),
                        answer=answer,
                        solution=_value(row, headers, 'solution'),
                        allowed_languages=_value(row, headers, 'allowed_languages'),
                        test_data=_value(row, headers, 'test_data'),
                        options=_value(row, headers, 'options'),
                        correct_options=_value(row, headers, 'correct_options'),
                        # A fill-blank row can use its answer cell when no separate blanks column exists.
                        blanks=blanks or (answer if question_type == ProblemSubmissionType.FILL_BLANK else ''),
                        test_data_zip=test_data_zip,
                        source_label=', '.join(document.filename for document in attachments),
                    )
                    count += 1
        except ValueError as error:
            return JsonResponse({'ok': False, 'message': str(error)}, status=400)
        if not count:
            draft.delete()
            return JsonResponse({'ok': False, 'message': _('The Excel file does not contain any problem rows.')}, status=400)
        preview_url = reverse('problem_import_preview', args=(draft.pk,))
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'ok': True, 'preview_url': preview_url})
        return redirect(preview_url)


class ProblemImportPreviewView(LoginRequiredMixin, View):
    template_name = 'problem/import_preview.html'

    def get(self, request, draft):
        draft = _draft_for(request, draft)
        return render(request, self.template_name, {
            'draft': draft,
            'items': draft.items.select_related('created_problem'),
            'type_choices': ProblemImportItem.TYPE_CHOICES,
            'title': _('Excel import preview'),
            'content_title': _('Excel import preview'),
            'feedback': request.GET.get('feedback', ''),
            'feedback_error': request.GET.get('feedback_error') == '1',
        })


class ProblemImportItemActionView(LoginRequiredMixin, View):
    """Save, format, or create one preview item without exposing draft ownership."""
    def post(self, request, draft, item, action):
        draft = _draft_for(request, draft)
        item = get_object_or_404(draft.items, pk=item)
        if action not in ('save', 'format', 'create'):
            raise Http404()
        try:
            self._save_item(item, request.POST, request.FILES)
        except ValueError as error:
            return self._error_response(request, draft, item, str(error))
        if action == 'save':
            return self._success_response(request, draft, item, _('Draft saved.'))
        if action == 'format':
            if not llm.is_configured():
                return self._error_response(request, draft, item, _('LiteLLM is not configured.'), status=503)
            try:
                payload = self._format(item)
            except (LLMError, ValueError) as error:
                return self._error_response(request, draft, item, str(error))
            return self._success_response(request, draft, item, _('Question formatted.'), payload)
        try:
            with transaction.atomic():
                problem = _create_problem(request, item)
                item.created_problem = problem
                item.save(update_fields=['created_problem', 'updated_at'])
        except ValueError as error:
            return self._error_response(request, draft, item, str(error))
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.ENOENT, errno.EROFS):
                raise
            return self._error_response(
                request, draft, item,
                _('Cannot write code-problem test data. An administrator must create and grant write access to '
                  '%(path)s for the web service.') % {'path': '/mnt/problems'},
            )
        return self._success_response(
            request, draft, item, _('Created %(name)s.') % {'name': problem.name},
            edit_url=_edit_url(problem),
        )

    @staticmethod
    def _wants_json(request):
        """Keep JSON for API calls while allowing normal browser form submissions."""
        return request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('native_submit') != '1'

    def _preview_redirect(self, draft, item, message, error=False):
        query = urlencode({'feedback': message, 'feedback_error': '1' if error else '0'})
        return '{}?{}#import-item-{}'.format(
            reverse('problem_import_preview', args=(draft.pk,)), query, item.pk)

    def _success_response(self, request, draft, item, message, payload=None, edit_url=None):
        if not self._wants_json(request):
            return redirect(self._preview_redirect(draft, item, message))
        response = {'ok': True, 'message': message}
        if payload is not None:
            response['item'] = _item_payload(item)
        if edit_url:
            response['edit_url'] = edit_url
        return JsonResponse(response)

    def _error_response(self, request, draft, item, message, status=400):
        item.formatting_error = message
        item.save(update_fields=['formatting_error', 'updated_at'])
        if not self._wants_json(request):
            return redirect(self._preview_redirect(draft, item, message, error=True))
        return JsonResponse({'ok': False, 'message': message}, status=status)

    def _save_item(self, item, data, files):
        try:
            item.submission_type = _normalise_type(data.get('question_type', item.submission_type))
        except ValueError as error:
            raise ValueError(str(error))
        for field in ('name', 'description', 'answer', 'solution', 'allowed_languages', 'test_data', 'options', 'correct_options', 'blanks'):
            setattr(item, field, (data.get(field, '') or '').strip())
        if files.get('test_data_zip'):
            raise ValueError('Test data ZIP files must be embedded as an Excel object.')
        item.formatting_error = ''
        item.save()

    def _format(self, item):
        payload = _format_with_llm(item)
        item.description = payload.get('description', item.description).strip()
        if item.submission_type == ProblemSubmissionType.MCQ:
            options = payload.get('options', [])
            item.options = '\n'.join(option['text'].strip() for option in options if option.get('text', '').strip())
            item.correct_options = '\n'.join(
                str(index + 1) for index, option in enumerate(options) if option.get('correct'))
        elif item.submission_type == ProblemSubmissionType.FILL_BLANK:
            item.blanks = _serialize_fill_blanks(payload.get('blanks', []))
        item.formatting_error = ''
        item.save()
        return _item_payload(item)


def _format_with_llm(item):
    type_name = dict(ProblemImportItem.TYPE_CHOICES)[item.submission_type]
    system = (
        'You format educational questions. Return JSON only. Keep all facts in the source; do not invent '
        'answers. For multiple choice return {"description": string, "options": [{"text": string, '
        '"correct": boolean}]}. For fill-in-the-blank return {"description": string, "blanks": '
        '[{"label": string, "answers": [string]}]}; label is the short text shown beside that input. '
        'For code return {"description": string}. '
        'If an answer cannot be determined, return an empty correct/answers collection.'
    )
    user = 'Question type: {}\n\nDescription:\n{}\n\nAnswer key, if supplied:\n{}'.format(
        type_name, item.description, item.answer)
    result = llm.chat([{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                      profile='question_import')
    text = result.text.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as error:
        raise ValueError('The LLM did not return valid JSON.') from error
    if not isinstance(payload, dict) or not isinstance(payload.get('description', ''), str):
        raise ValueError('The LLM returned an invalid question format.')
    if item.submission_type == ProblemSubmissionType.MCQ:
        options = payload.get('options', [])
        if not isinstance(options, list) or any(not isinstance(option, dict) for option in options):
            raise ValueError('The LLM returned invalid multiple-choice options.')
    if item.submission_type == ProblemSubmissionType.FILL_BLANK:
        blanks = payload.get('blanks', [])
        if not isinstance(blanks, list) or any(not isinstance(blank, dict) for blank in blanks):
            raise ValueError('The LLM returned invalid blanks.')
    return payload


def _item_payload(item):
    return {field: getattr(item, field) for field in ('description', 'options', 'correct_options', 'blanks')}


def _split_lines(value):
    return [part.strip() for part in re.split(r'[\n,;|]+', value or '') if part.strip()]


def _resolve_allowed_languages(raw_value):
    requested = _split_lines(raw_value)
    queryset = list(Language.objects.exclude(key='ANSWER'))
    if not requested:
        return queryset
    resolved = []
    seen = set()
    for token in requested:
        matches = matching_languages(queryset, token)
        if not matches:
            raise ValueError('Language "{}" was not found.'.format(token))
        if len(matches) > 1:
            raise ValueError('Language "{}" matched multiple languages.'.format(token))
        match = matches[0]
        if match.pk not in seen:
            resolved.append(match)
            seen.add(match.pk)
    return resolved


def _parse_text_test_data(value):
    """Parse input/output blocks while allowing multi-line input and output values."""
    cases = {}
    current = None
    pattern = re.compile(r'^(input|output)\s+data\s+(\d+)\s*:\s?(.*)$', re.IGNORECASE)
    for line in (value or '').replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        match = pattern.match(line)
        if match:
            kind, number, first_line = match.groups()
            key = int(number)
            if key < 1 or (key, kind.lower()) in cases:
                raise ValueError('Each input data/output data number must appear only once.')
            cases[key, kind.lower()] = [first_line]
            current = cases[key, kind.lower()]
        elif current is not None:
            current.append(line)
        elif line.strip():
            raise ValueError('Test data must start with "input data 1:" or "output data 1:".')
    numbers = sorted({number for number, _kind in cases})
    result = []
    for number in numbers:
        input_lines = cases.get((number, 'input'))
        output_lines = cases.get((number, 'output'))
        if input_lines is None or output_lines is None:
            raise ValueError('Test data {} needs both input and output.'.format(number))
        result.append(('case{:03d}.in'.format(number), '\n'.join(input_lines),
                       'case{:03d}.out'.format(number), '\n'.join(output_lines)))
    return result


def _parse_zip_test_data(uploaded):
    payload = uploaded.read()
    if len(payload) > 16 * 1024 * 1024:
        raise ValueError('The test data ZIP is too large.')
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = [info for info in archive.infolist() if not info.is_dir()]
            if sum(info.file_size for info in entries) > 64 * 1024 * 1024:
                raise ValueError('The test data ZIP is too large.')
            files = {}
            for info in entries:
                name = info.filename.replace('\\', '/')
                if name.startswith('/') or '/../' in '/{}/'.format(name):
                    raise ValueError('The test data ZIP contains an unsafe path.')
                root, extension = os.path.splitext(name)
                if extension.lower() in ('.in', '.out'):
                    files.setdefault(root, {})[extension.lower()] = (name, archive.read(info))
    except zipfile.BadZipFile as error:
        raise ValueError('The test data ZIP is invalid.') from error
    cases = []
    for root in sorted(files):
        pair = files[root]
        if '.in' not in pair or '.out' not in pair:
            raise ValueError('ZIP test data files must have matching .in and .out names.')
        cases.append((pair['.in'][0], pair['.in'][1], pair['.out'][0], pair['.out'][1]))
    if not cases:
        raise ValueError('The test data ZIP does not contain any .in/.out test pairs.')
    return payload, cases


def _add_test_cases(problem, item):
    if item.test_data_zip:
        item.test_data_zip.open('rb')
        payload, cases = _parse_zip_test_data(item.test_data_zip)
        data = ProblemData.objects.create(problem=problem)
        data.zipfile.save('test_data.zip', ContentFile(payload), save=True)
    elif item.test_data.strip():
        cases = _parse_text_test_data(item.test_data)
        data = ProblemData.objects.create(problem=problem)
        for input_name, input_data, output_name, output_data in cases:
            problem_data_storage.save('{}/{}'.format(problem.code, input_name), ContentFile(input_data))
            problem_data_storage.save('{}/{}'.format(problem.code, output_name), ContentFile(output_data))
    else:
        return
    created_cases = []
    for order, (input_name, _input_data, output_name, _output_data) in enumerate(cases, start=1):
        created_cases.append(ProblemTestCase.objects.create(
            dataset=problem, order=order, input_file=input_name, output_file=output_name,
            points=1, is_pretest=False,
        ))
    valid_files = [filename for case in cases for filename in (case[0], case[2])]
    ProblemDataCompiler.generate(problem, data, created_cases, valid_files)


def _create_problem(request, item):
    if item.created_problem_id:
        return item.created_problem
    if not item.name or not item.description:
        raise ValueError('A name and description are required.')
    problem = Problem(name=item.name, description=item.description, submission_type=item.submission_type, points=10)
    apply_simple_problem_defaults(problem)
    problem.save()
    problem.types.add(default_problem_type())
    problem.authors.add(request.user.profile)
    if item.solution.strip():
        solution = Solution.objects.create(
            problem=problem,
            content=item.solution,
            is_public=False,
            publish_on=timezone.now(),
        )
        solution.authors.add(request.user.profile)
    if item.submission_type == ProblemSubmissionType.CODE:
        languages = _resolve_allowed_languages(item.allowed_languages)
        if not languages:
            raise ValueError('At least one programming language is required for a code problem.')
        problem.allowed_languages.set(languages)
        _add_test_cases(problem, item)
    elif item.submission_type == ProblemSubmissionType.MCQ:
        options = _split_lines(item.options)
        correct = {int(value) for value in _split_lines(item.correct_options) if value.isdigit()}
        if len(options) < 2:
            raise ValueError('Multiple-choice questions need at least two options.')
        if not correct or any(index < 1 or index > len(options) for index in correct):
            raise ValueError('Mark at least one valid correct option.')
        if len(correct) > 1:
            multiple_select = True
        else:
            multiple_select = False
        ProblemMCQConfig.objects.create(problem=problem, multiple_select=multiple_select)
        for index, text in enumerate(options, start=1):
            ProblemMCQOption.objects.create(problem=problem, order=index - 1, text=text, is_correct=index in correct)
    elif item.submission_type == ProblemSubmissionType.FILL_BLANK:
        blanks = _parse_fill_blanks(item.blanks or item.answer)
        if not blanks:
            raise ValueError('Fill-in-the-blank questions need at least one blank with an accepted answer.')
        ProblemFillBlankConfig.objects.create(problem=problem)
        for index, (label, answers) in enumerate(blanks):
            ProblemBlank.objects.create(problem=problem, order=index, label=label, answers=answers)
    return problem


def _edit_url(problem):
    if problem.submission_type == ProblemSubmissionType.MCQ:
        return reverse('problem_mcq_edit', args=(problem.code,))
    if problem.submission_type == ProblemSubmissionType.FILL_BLANK:
        return reverse('problem_fillblank_edit', args=(problem.code,))
    return reverse('admin:judge_problem_change', args=(problem.pk,))
