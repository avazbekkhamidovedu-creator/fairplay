"""Shared fixtures for the FairPlay bridge tests."""
import tempfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.files.storage import FileSystemStorage
from django.test import override_settings
from django.utils import timezone

from judge.models import Class, Contest, EditorAttachment, Language, ProblemBlank, ProblemFileConfig, \
    ProblemFillBlankConfig, ProblemHarness, ProblemMCQConfig, ProblemMCQOption, ProblemShortAnswerConfig, \
    ProblemTemplate, ProblemTestCase, SubmissionFile
from judge.models.tests.util import create_contest as _create_contest, create_contest_problem, \
    create_organization, create_problem, create_user
from judge.storage.resilient import ResilientStorage
from judge.utils.fairplay import set_fairplay_exam

TOKEN = 'fp-test-token-0123456789abcdef0123456789'
INSTALLATION_ID = 'eaab2f6e-7072-4c86-968c-a9c624cc676d'
FAIRPLAY_ON = override_settings(FAIRPLAY_TOKEN=TOKEN, FAIRPLAY_URL='https://exam.example.edu',
                                FAIRPLAY_INSTALLATION_ID=INSTALLATION_ID)
FAIRPLAY_OFF = override_settings(FAIRPLAY_TOKEN='', FAIRPLAY_URL='')

# Strings that exist only in answer keys / hidden data; none may ever reach FairPlay.
SECRETS = ('SECRET-BLANK-ANSWER', 'SECRET-REFERENCE-ANSWER', 'SECRET-HARNESS', 'SECRET-RUBRIC', 'secret.in')


def create_contest(key, fairplay_required=False, **kwargs):
    """``create_contest`` of the model tests; ``fairplay_required=True`` tags it ``fairplay``."""
    contest = _create_contest(key, **kwargs)
    if fairplay_required:
        set_fairplay_exam(contest, True)
    return contest


def set_fairplay(contest, on):
    """Make an assignment a FairPlay exam, or not (the ``fairplay`` tag)."""
    set_fairplay_exam(Contest.objects.get(pk=contest.pk), on)


def grant(user, *codenames):
    user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
    return user


class FairPlayDataMixin:
    """A running FairPlay exam with one problem of every type, its staff and its students."""
    fixtures = ['language_all.json']

    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.teacher = create_user(username='fp_teacher', first_name='Shuai', last_name='Dong',
                                  email='fp_teacher@example.edu')
        grant(cls.teacher, 'edit_own_contest', 'add_contest', 'contest_seb_requirement', 'create_private_contest',
              'change_contest_visibility')
        cls.tester = create_user(username='fp_tester')
        cls.course_admin = create_user(username='fp_course_admin')
        cls.student = create_user(username='fp_student', first_name='Avazbek', last_name='Khamidov',
                                  email='fp_student@example.edu')
        cls.classmate = create_user(username='fp_classmate')
        cls.outsider = create_user(username='fp_outsider')
        cls.staff = grant(create_user(username='fp_staff', is_staff=True),
                          'see_private_contest', 'see_private_problem', 'edit_all_contest')

        cls.org = create_organization(name='fporg', admins=('fp_course_admin',))
        cls.klass = Class.objects.create(organization=cls.org, name='FP Section A', slug='fp-a')
        for user in (cls.student, cls.classmate):
            user.profile.organizations.add(cls.org)
            cls.klass.members.add(user.profile)
        cls.outsider.profile.organizations.add(cls.org)

        cls.code = create_problem('fpcode', name='Reverse a list', is_public=True, points=30, partial=True,
                                  allowed_languages=('PY3', 'CPP14'),
                                  description='Write it.\n\n## Sample Input\n\n```\n1 2 3\n```\n\n'
                                              '## Sample Output\n\n```\n3 2 1\n```\n')
        ProblemTemplate.objects.create(problem=cls.code, language=Language.objects.get(key='PY3'),
                                       code='def reverse(head):\n    pass\n')
        ProblemHarness.objects.create(problem=cls.code, language=Language.objects.get(key='PY3'),
                                      harness_code='SECRET-HARNESS')
        ProblemTestCase.objects.create(dataset=cls.code, order=1, input_file='secret.in', output_file='secret.out',
                                       is_pretest=False)

        cls.mcq = create_problem('fpmcq', name='Hash maps', submission_type='M', points=10,
                                 description='Which structure gives O(1) average lookup?')
        ProblemMCQConfig.objects.create(problem=cls.mcq, multiple_select=False, shuffle_options=True)
        cls.wrong = ProblemMCQOption.objects.create(problem=cls.mcq, order=1, text='Array', is_correct=False)
        cls.right = ProblemMCQOption.objects.create(problem=cls.mcq, order=2, text='Hash table', is_correct=True)

        cls.blank = create_problem('fpblank', name='Fill', submission_type='B', points=6,
                                   description='A ___ maps keys to values.')
        ProblemFillBlankConfig.objects.create(problem=cls.blank, case_sensitive=False)
        ProblemBlank.objects.create(problem=cls.blank, order=1, answers='SECRET-BLANK-ANSWER', points=3)
        ProblemBlank.objects.create(problem=cls.blank, order=2, answers='SECRET-BLANK-ANSWER', points=3)

        cls.short = create_problem('fpshort', name='Explain', submission_type='S', points=10,
                                   description='Explain hashing.')
        ProblemShortAnswerConfig.objects.create(problem=cls.short, reference_answer='SECRET-REFERENCE-ANSWER',
                                                ai_rubric='SECRET-RUBRIC')

        cls.file = create_problem('fpfile', name='Report', submission_type='F', points=10,
                                  description='Upload your report.')
        ProblemFileConfig.objects.create(problem=cls.file, allowed_extensions='pdf,png', max_file_size=1024)

        cls.exam = create_contest(
            'fpexam', name='CS 101 Midterm', authors=('fp_teacher',), testers=('fp_tester',),
            organizations=('fporg',), start_time=now - timedelta(hours=1), end_time=now + timedelta(hours=2),
            time_limit=timedelta(minutes=90), is_visible=True, is_organization_private=True,
            fairplay_required=True, format_name='exam',
        )
        cls.exam.classes.add(cls.klass)
        for order, problem in enumerate((cls.mcq, cls.blank, cls.short, cls.file, cls.code), 1):
            create_contest_problem(contest=cls.exam, problem=problem, points=problem.points, order=order,
                                   partial=True)
        from judge.utils.fairplay_editor import apply_fairplay_editor, parse_fairplay_payload
        apply_fairplay_editor(cls.exam, parse_fairplay_payload(cls.exam, {
            'fairplay_required': True, 'fairplay_audience_confirm': True,
            'fairplay_audience_mode': 'classes'}, set()))

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        storage = ResilientStorage(primary=FileSystemStorage(location=cls._tmp.name), backup=None)
        cls.storage = storage
        cls._patches = [
            mock.patch.object(SubmissionFile._meta.get_field('file'), 'storage', storage),
            mock.patch.object(EditorAttachment._meta.get_field('file'), 'storage', storage),
            mock.patch('judge.storage._submission_storage', storage),
        ]
        for patch in cls._patches:
            patch.start()

    @classmethod
    def tearDownClass(cls):
        for patch in cls._patches:
            patch.stop()
        cls._tmp.cleanup()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        # Hand-in records, change tracking and markers live in the (shared, per-test-run) cache.
        cache.clear()

    def reload_exam(self):
        return Contest.objects.get(pk=self.exam.pk)


def installation_path(path):
    from urllib.parse import parse_qsl, urlencode, urlsplit
    from judge.utils.fairplay_identity import installation_id
    parsed = urlsplit(path)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if not any(name == 'installation_id' for name, _value in query):
        query.append(('installation_id', installation_id()))
    return parsed._replace(query=urlencode(query)).geturl()


def fp_signed(method, path, body=b'', token=TOKEN, timestamp=None, nonce=None, acting_user=None):
    """Test-client META for a request signed by FairPlay."""
    from urllib.parse import urlsplit
    from judge.utils.fairplay_signing import DIRECTION_FP_TO_OJ, signed_headers
    path = installation_path(path)
    headers = signed_headers(method, path, body, acting_user=acting_user, timestamp=timestamp, nonce=nonce,
                             token=token, direction=DIRECTION_FP_TO_OJ)
    return dict({'HTTP_' + key.upper().replace('-', '_'): value for key, value in headers.items()}, QUERY_STRING=urlsplit(path).query)
