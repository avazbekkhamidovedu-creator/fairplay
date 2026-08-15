"""Tests for AI tag suggestion filtering (task 1) and the shared AI-grading helpers (task 3).

Two related but distinct concerns live here:

1. **Tag suggestion hygiene** — the AI tag endpoint must never propose answer-format
   (``fill_in_blank``, ``Programming`` …) or difficulty tags; it may only return knowledge tags
   already in the DB plus the curated algorithm vocabulary.

2. **Shared LLM-grading plumbing** — ``judge.question_types.ai_grading`` centralises the
   retry/fence-stripping/calibration logic shared by short-answer, fill-in-the-blank and the new
   supplementary code rubric scoring.
"""
import json
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from judge.models import Language, Problem, ProblemCodeConfig, Submission, SubmissionSource
from judge.models.tests.util import CommonDataMixin, create_problem, create_problem_type, create_user
from judge.question_types.ai_grading import call_llm, parse_calibrated_scores, sub_score_cases
from judge.utils.problem_create import default_problem_group
from judge.views.problem_create_v2 import (
    ProblemCreateV2AIProblemTypeView,
    _knowledge_type_labels,
    _normalize_tag_label,
    _tag_suggestion_candidates,
)


class TagLabelFilterTestCase(TestCase):
    """Unit-level checks on the candidate set and label normalisation."""

    def setUp(self):
        # A format-type row and a difficulty row must never survive filtering.
        self.format_type = create_problem_type('fill_in_blank')
        self.format_type.full_name = 'Fill in the blank'
        self.format_type.save()
        self.diff_type = create_problem_type('easy')
        self.diff_type.full_name = 'Easy'
        self.diff_type.save()
        self.knowledge_type = create_problem_type('graph')
        self.knowledge_type.full_name = 'Graph Theory'
        self.knowledge_type.save()

    def test_knowledge_type_labels_excludes_format_and_difficulty(self):
        labels = _knowledge_type_labels()
        self.assertIn('Graph Theory', labels)
        self.assertNotIn('Fill in the blank', labels)
        self.assertNotIn('Easy', labels)

    def test_candidates_include_vocabulary_and_knowledge_tags(self):
        candidates = _tag_suggestion_candidates()
        self.assertIn('dp', candidates)
        self.assertIn('Graph Theory', candidates)
        self.assertNotIn('Fill in the blank', candidates)

    def test_normalize_rejects_format_tags(self):
        for bad in ('fill_in_blank', 'Fill in the blank', 'Programming', 'Easy', 'easy'):
            self.assertIsNone(_normalize_tag_label(bad), bad)

    def test_normalize_rejects_invented_labels(self):
        self.assertIsNone(_normalize_tag_label('MadeUpAlgorithmXYZ'))

    def test_normalize_accepts_vocabulary(self):
        self.assertEqual(_normalize_tag_label('dp'), 'dp')
        self.assertEqual(_normalize_tag_label(' DP.'), 'dp')
        self.assertEqual(_normalize_tag_label('BFS'), 'bfs')

    def test_normalize_accepts_existing_knowledge_tag_case_insensitively(self):
        self.assertEqual(_normalize_tag_label('graph theory'), 'Graph Theory')


class AISuggestTestCase(TestCase):
    """End-to-end ``_suggest`` behavior with the LLM mocked."""

    def setUp(self):
        self.knowledge = create_problem_type('math')
        self.knowledge.full_name = 'Simple Math'
        self.knowledge.save()
        self.view = ProblemCreateV2AIProblemTypeView()

    def test_llm_garbage_is_filtered_to_valid_tags(self):
        """The model returns format tags + an invented tag + one valid tag: only the valid one
        survives the hard post-filter."""
        class FakeResult:
            text = 'fill_in_blank, Programming, dp, MadeUpTag'

        with mock.patch('judge.llm.is_configured', return_value=True), \
                mock.patch('judge.llm.chat', return_value=FakeResult()):
            suggestions = self.view._suggest('some problem')
        names = {s['full'] for s in suggestions}
        self.assertEqual(names, {'dp'})
        self.assertNotIn('fill_in_blank', names)
        self.assertNotIn('Programming', names)

    def test_llm_duplicate_labels_collapse(self):
        class FakeResult:
            text = 'dp, DP, dp'

        with mock.patch('judge.llm.is_configured', return_value=True), \
                mock.patch('judge.llm.chat', return_value=FakeResult()):
            suggestions = self.view._suggest('some problem')
        self.assertEqual(len(suggestions), 1)

    def test_fallback_matches_knowledge_tags_only(self):
        with mock.patch('judge.llm.is_configured', return_value=False):
            suggestions = self.view._suggest('Compute the answer with Simple Math.')
        self.assertEqual([s['full'] for s in suggestions], ['Simple Math'])

    def test_fallback_returns_general_when_no_match(self):
        with mock.patch('judge.llm.is_configured', return_value=False):
            suggestions = self.view._suggest('Write an essay about history.')
        self.assertEqual(suggestions, [{'name': 'general', 'full': 'General', 'existing': False}])


class AISuggestEndpointTestCase(CommonDataMixin, TestCase):
    """The HTTP endpoint never leaks format/difficulty tags in its response."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.users['creator'] = create_user(
            username='tag_creator', user_permissions=('create_private_problem', 'edit_own_problem'))

    def setUp(self):
        self.client.force_login(self.users['creator'])

    def test_endpoint_200_with_llm_disabled(self):
        with mock.patch('judge.llm.is_configured', return_value=False):
            response = self.client.post(
                reverse('problem_create_v2_ai_problem_type'),
                data=json.dumps({'description': 'Solve a problem with Simple Math.'}),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['suggestions'])


class SharedAIGradingHelpersTestCase(TestCase):
    """Shared ``judge.question_types.ai_grading`` helpers used by all three AI-graded types."""

    def test_parse_and_calibrate_basic(self):
        sq = [{'name': 'Correctness', 'max_points': 10.0, 'strictness': 'normal'}]
        raw = json.dumps({'sub_scores': [{'name': 'Correctness', 'score': 8, 'feedback': 'ok'}]})
        calibrated = parse_calibrated_scores(raw, sq)
        self.assertEqual(len(calibrated), 1)
        self.assertEqual(calibrated[0]['name'], 'Correctness')
        self.assertEqual(calibrated[0]['max_points'], 10.0)
        self.assertGreater(calibrated[0]['score'], 0)

    def test_parse_returns_none_for_garbage(self):
        self.assertIsNone(parse_calibrated_scores('not json', []))

    def test_parse_returns_none_for_empty_subscores(self):
        self.assertIsNone(parse_calibrated_scores(json.dumps({'sub_scores': []}), []))

    def test_parse_matches_by_index_not_name(self):
        """The model may rephrase labels; calibration matches by index so the max_points /
        strictness come from the sub-question table, not from the model's text."""
        sq = [{'name': 'Overall', 'max_points': 20.0, 'strictness': 'strict'}]
        raw = json.dumps({'sub_scores': [{'name': 'renamed by model', 'score': 20, 'feedback': 'x'}]})
        calibrated = parse_calibrated_scores(raw, sq)
        self.assertEqual(calibrated[0]['max_points'], 20.0)

    def test_parse_extra_subscores_default_to_one_point(self):
        sq = [{'name': 'A', 'max_points': 5.0, 'strictness': 'normal'}]
        raw = json.dumps({'sub_scores': [
            {'name': 'A', 'score': 5},
            {'name': 'B', 'score': 3},
        ]})
        calibrated = parse_calibrated_scores(raw, sq)
        self.assertEqual(calibrated[1]['max_points'], 1.0)

    def test_sub_score_cases_shape(self):
        calibrated = [{'name': 'A', 'max_points': 5.0, 'score': 5.0, 'feedback': 'ok'},
                      {'name': 'B', 'max_points': 5.0, 'score': 2.0, 'feedback': 'no'}]
        cases = sub_score_cases(calibrated)
        self.assertEqual(cases[0]['status'], 'AC')
        self.assertEqual(cases[1]['status'], 'WA')
        self.assertEqual(cases[0]['case'], 1)
        self.assertEqual(cases[1]['case'], 2)

    def test_call_llm_strips_fences(self):
        class FakeLLM:
            def chat(self, messages, **kwargs):
                return type('R', (), {'text': '```\n{"sub_scores": []}\n```'})()
        self.assertEqual(call_llm(FakeLLM(), []), '{"sub_scores": []}')


class CodeSupplementaryAIGradeTestCase(CommonDataMixin, TestCase):
    """Supplementary code rubric scoring: only runs when enabled + configured, and never changes
    the judge verdict (it only produces extra informational test-case rows)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.problem = create_problem('code-ai-prob', points=10)
        cls.submission = Submission.objects.create(
            user=cls.users['normal'].profile, problem=cls.problem,
            language=Language.objects.get(key='PY3'),
            result='AC', status='D', points=10.0, case_points=10, case_total=10,
        )
        SubmissionSource.objects.create(submission=cls.submission, source='def f(): return 1')

    def test_no_config_returns_none(self):
        from judge.question_types.code import supplementary_ai_grade
        self.assertIsNone(supplementary_ai_grade(self.submission))

    def test_disabled_config_returns_none(self):
        from judge.question_types.code import supplementary_ai_grade
        ProblemCodeConfig.objects.create(problem=self.problem, ai_enabled=False)
        self.assertIsNone(supplementary_ai_grade(self.submission))

    def test_enabled_but_no_llm_returns_none(self):
        from judge.question_types.code import supplementary_ai_grade
        ProblemCodeConfig.objects.create(
            problem=self.problem, ai_enabled=True, ai_rubric='Clarity matters')
        with mock.patch('judge.llm.is_configured', return_value=False):
            self.assertIsNone(supplementary_ai_grade(self.submission))

    def test_enabled_with_llm_returns_extra_rows(self):
        from judge.question_types.code import supplementary_ai_grade
        ProblemCodeConfig.objects.create(
            problem=self.problem, ai_enabled=True, ai_rubric='Clarity matters',
            grading_table=[{'name': 'Clarity', 'max_points': 5.0, 'strictness': 'normal'}])

        class FakeResult:
            text = json.dumps({'sub_scores': [
                {'name': 'Clarity', 'score': 4, 'feedback': 'readable'}],
                'total_score': 4})

        with mock.patch('judge.llm.is_configured', return_value=True), \
                mock.patch('judge.llm.chat', return_value=FakeResult()):
            rows = supplementary_ai_grade(self.submission)

        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 1)
        # Case numbers are offset past any real test cases (0 here).
        self.assertEqual(rows[0]['case'], 1)
        self.assertEqual(rows[0]['batch'], -1)
        self.assertTrue(rows[0]['feedback'].startswith('[AI]'))
        # The judge verdict is untouched by the supplementary score.
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.result, 'AC')
        self.assertEqual(self.submission.points, 10.0)


class CodeConfigRoundTripTestCase(CommonDataMixin, TestCase):
    """Code problems persist + reload their supplementary AI config via create_v2."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.group = default_problem_group()
        cls.users['staff_adder'] = create_user(
            username='code_cfg_creator', is_staff=True,
            user_permissions=('add_problem', 'create_private_problem',
                              'change_public_visibility', 'edit_own_problem'))

    def setUp(self):
        self.client.force_login(self.users['staff_adder'])

    def test_create_code_with_ai_config(self):
        payload = {
            'type': 'programming',
            'name': 'AI-graded code problem',
            'description': 'Write a function.',
            'points': 10,
            'group_id': self.group.id,
            'time_limit': 1,
            'memory_limit': 256,
            'ai': True,
            'rubric': 'Correctness and clarity.',
            'gradingTable': [{'name': 'Correctness', 'max_points': 6, 'strictness': 'normal'},
                             {'name': 'Clarity', 'max_points': 4, 'strictness': 'lenient'}],
        }
        response = self.client.post(
            reverse('problem_create_v2'),
            data=json.dumps(payload), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        code = response.json()['code']
        problem = Problem.objects.get(code=code)
        self.assertTrue(problem.code_config.ai_enabled)
        self.assertEqual(problem.code_config.ai_rubric, 'Correctness and clarity.')
        self.assertEqual(len(problem.code_config.grading_table), 2)

    def test_edit_data_round_trip_exposes_ai_config(self):
        problem = create_problem('code-ai-roundtrip', points=10, authors=('code_cfg_creator',))
        ProblemCodeConfig.objects.create(
            problem=problem, ai_enabled=True, ai_rubric='Rubric text',
            grading_table=[{'name': 'Overall', 'max_points': 10, 'strictness': 'normal'}])
        self.client.force_login(self.users['staff_adder'])
        response = self.client.get(reverse('problem_edit_v2', args=[problem.code]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('"ai": true', html)
        self.assertIn('Rubric text', html)
