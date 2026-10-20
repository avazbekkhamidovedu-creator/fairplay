"""Per-request cost guards for the teacher-facing pages (docs/classroom/reports/teacher-speed.md).

At university scale (500 instructors, 150 courses, 6,000 problems, 1,500 assignments, 150,000
submissions; scripts/loadtest/seed_teachers.py) these pages must not get slower as the data grows.
Each test pins one fix: either the SQL shape that MariaDB cannot run fast (joins of every role
table), or a query count / response size that must not grow with the number of rows.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from judge.models import (Class, Contest, ContestParticipation, ContestProblem, ContestSubmission, Language,
                          Organization, ProblemMCQConfig, ProblemType, Profile, Submission, SubmissionSource)
from judge.models.notification import ensure_instructors_group
from judge.models.tests.util import create_contest, create_problem, create_user
from judge.subjects.access import teaching_courses, user_course_subject_keys
from judge.utils.contest_grade import practice_grade_queryset


def role_joins(sql):
    """Role tables joined in the outer query (not inside an EXISTS / IN subquery)."""
    tables = ('judge_profile_organizations', 'judge_organization_admins', 'judge_organization_instructors',
              'judge_class_admins', 'judge_class_members', 'judge_contest_authors', 'judge_contest_curators',
              'judge_contest_testers', 'judge_contest_spectators', 'judge_contest_private_contestants',
              'judge_contest_view_contest_scoreboard', 'judge_contest_organizations', 'judge_contest_classes')
    return [t for t in tables if 'JOIN `%s`' % t in sql]


class CourseRoleQueryShapeTest(TestCase):
    """The course-role lookups that run on every page must be indexed subqueries, not joins.

    The old shape (``Q(member=p) | Q(admins=p) | Q(class__members=p) ...``) LEFT JOINed every role
    table at once; on the seeded database that one query took 2.4 s and ran on every page an
    instructor or student opened (judge.subjects.access.site_features, via the navigation bar).
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = create_user(username='shape_teacher')
        cls.course = Organization.objects.create(name='Shape course', slug='shape-course', short_name='SC',
                                                 is_open=False, subject='math')
        cls.course.admins.add(cls.user.profile)

    def test_subject_keys_use_subqueries(self):
        with CaptureQueriesContext(connection) as ctx:
            keys = user_course_subject_keys(self.user)
        self.assertEqual(keys, frozenset({'math'}))
        self.assertEqual(len(ctx), 1)
        self.assertEqual(role_joins(ctx.captured_queries[0]['sql']), [])

    def test_teaching_courses_use_subqueries(self):
        with CaptureQueriesContext(connection) as ctx:
            courses = teaching_courses(self.user)
        self.assertEqual(courses, [self.course])
        self.assertEqual(role_joins(ctx.captured_queries[0]['sql']), [])

    def test_visible_contests_use_subqueries(self):
        sql = str(Contest.get_visible_contests(self.user).query)
        self.assertEqual(role_joins(sql), [])


class VisibleContestsMatrixTest(TestCase):
    """``Contest.get_visible_contests`` answers stay the same after the rewrite to EXISTS subqueries.

    One assignment per visibility rule, checked for every kind of user.
    """

    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        names = ('vm_member', 'vm_classmate', 'vm_admin', 'vm_instructor', 'vm_class_admin', 'vm_author',
                 'vm_curator', 'vm_tester', 'vm_spectator', 'vm_private', 'vm_scoreboard', 'vm_outsider')
        cls.users = {n: create_user(username=n) for n in names}
        p = {n: u.profile for n, u in cls.users.items()}
        cls.course = Organization.objects.create(name='VM course', slug='vm-course', short_name='VM', is_open=False)
        cls.other = Organization.objects.create(name='VM other', slug='vm-other', short_name='VO', is_open=False)
        cls.course.members.add(p['vm_member'], p['vm_classmate'], p['vm_private'])
        cls.course.admins.add(p['vm_admin'])
        cls.course.instructors.add(p['vm_instructor'])
        section = Class.objects.create(organization=cls.course, name='VM-01', slug='vm-01')
        section.members.add(p['vm_classmate'])
        section.admins.add(p['vm_class_admin'])
        window = dict(start_time=now - timedelta(days=1), end_time=now + timedelta(days=1))

        def contest(key, **kw):
            kw = dict(window, **kw)
            return create_contest(key=key, **kw)

        cls.contests = {
            'public': contest('vmpublic', is_visible=True),
            'hidden': contest('vmhidden', is_visible=False),
            'org': contest('vmorg', is_visible=True, is_organization_private=True, organizations=('VM course',)),
            'org_hidden': contest('vmorghid', is_visible=False, is_organization_private=True,
                                  organizations=('VM course',)),
            'class': contest('vmclass', is_visible=True, is_organization_private=True, organizations=('VM course',)),
            'class_hidden': contest('vmclhid', is_visible=False, is_organization_private=True,
                                    organizations=('VM course',)),
            'private': contest('vmpriv', is_visible=True, is_private=True),
            'org_private': contest('vmorgpriv', is_visible=True, is_private=True, is_organization_private=True,
                                   organizations=('VM course',)),
            'other_org': contest('vmother', is_visible=True, is_organization_private=True,
                                 organizations=('VM other',)),
            'roles': contest('vmroles', is_visible=False, is_organization_private=True,
                             organizations=('VM other',)),
            'scoreboard': contest('vmscore', is_visible=True, is_organization_private=True,
                                  organizations=('VM other',)),
        }
        cls.contests['class'].classes.add(section)
        cls.contests['class_hidden'].classes.add(section)
        for key in ('private', 'org_private'):
            cls.contests[key].private_contestants.add(p['vm_private'])
        roles = cls.contests['roles']
        roles.authors.add(p['vm_author'])
        roles.curators.add(p['vm_curator'])
        roles.testers.add(p['vm_tester'])
        roles.spectators.add(p['vm_spectator'])
        cls.contests['scoreboard'].view_contest_scoreboard.add(p['vm_scoreboard'])

    def visible(self, name):
        user = User.objects.get(username=name)
        keys = set(Contest.get_visible_contests(user).values_list('key', flat=True))
        return {k for k, c in self.contests.items() if c.key in keys}

    def test_matrix(self):
        expected = {
            'vm_member': {'public', 'org'},
            'vm_classmate': {'public', 'org', 'class'},
            'vm_private': {'public', 'org', 'private', 'org_private'},
            'vm_admin': {'public', 'org', 'org_hidden', 'class', 'class_hidden', 'org_private'},
            'vm_instructor': {'public', 'org', 'class', 'org_private'},
            'vm_class_admin': {'public', 'class', 'org', 'org_private'},
            'vm_author': {'public', 'roles'},
            'vm_curator': {'public', 'roles'},
            'vm_tester': {'public', 'roles'},
            'vm_spectator': {'public', 'roles'},
            'vm_scoreboard': {'public', 'scoreboard'},
            'vm_outsider': {'public'},
        }
        for name, keys in expected.items():
            with self.subTest(user=name):
                self.assertEqual(self.visible(name), keys)


class AssignmentEditorLibraryTest(TestCase):
    """The editor's "Add from library" loads a page at a time instead of the whole bank.

    Before: every attachable problem was rendered into /contests/create/ (11.8 MB for an
    administrator on the seed). After: the page size and query count do not depend on the number
    of problems, and ContestProblemLibraryView serves the same problems with the same filters.
    """

    @classmethod
    def setUpTestData(cls):
        cls.teacher = create_user(username='lib_teacher')
        cls.teacher.groups.add(ensure_instructors_group())
        cls.other = create_user(username='lib_other')
        cls.student = create_user(username='lib_student')
        cls.course = Organization.objects.create(name='Lib course', slug='lib-course', short_name='LC', is_open=False)
        cls.other_course = Organization.objects.create(name='Lib other', slug='lib-other', short_name='LO',
                                                       is_open=False)
        cls.course.admins.add(cls.teacher.profile)
        cls.course.members.add(cls.student.profile)
        cls.other_course.admins.add(cls.other.profile)
        cls.easy = ProblemType.objects.create(name='easy', full_name='Easy')
        cls.hard = ProblemType.objects.create(name='hard', full_name='Hard')
        cls.tag = ProblemType.objects.create(name='lib-loops', full_name='Loops')

        def problem(code, kind='S', course=None, public=False, types=(), ac=0.0):
            p = create_problem(code, name=code.replace('-', ' ').title(), submission_type=kind, is_public=public,
                               is_organization_private=course is not None, ac_rate=ac,
                               organizations=(course.name,) if course else ())
            p.types.add(*types)
            return p

        cls.mine_easy = problem('lib-easy', 'C', cls.course, types=(cls.easy, cls.hard, cls.tag), ac=40)
        cls.mine_hard = problem('lib-hard', 'M', cls.course, types=(cls.hard,), ac=90)
        ProblemMCQConfig.objects.create(problem=cls.mine_hard, multiple_select=True)
        cls.mine_plain = problem('lib-plain', 'B', cls.course, ac=10)
        cls.public = problem('lib-public', 'S', None, public=True, ac=55)
        cls.hidden = problem('lib-secret', 'S', cls.other_course)

    def setUp(self):
        self.client.force_login(self.teacher)

    def library(self, **params):
        response = self.client.get(reverse('contest_problem_library'), params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def codes(self, **params):
        return [r['code'] for r in self.library(page_size=50, **params)['rows']]

    def test_editor_page_does_not_grow_with_the_bank(self):
        def measure():
            with CaptureQueriesContext(connection) as ctx:
                html = self.client.get(reverse('contest_create')).content
            return len(html), len(ctx)

        measure()  # first request warms per-process caches
        size, queries = measure()
        for i in range(30):
            create_problem('lib-more-%02d' % i, name='More problem %d with a long enough name' % i,
                           is_public=True)
        size_after, queries_after = measure()
        self.assertEqual(queries_after, queries)
        self.assertLess(size_after - size, 200)

    def test_library_query_count_is_flat(self):
        with CaptureQueriesContext(connection) as ctx:
            self.library()
        before = len(ctx)
        for i in range(30):
            p = create_problem('lib-flat-%02d' % i, is_public=True)
            p.types.add(self.tag)
        with CaptureQueriesContext(connection) as ctx:
            data = self.library()
        self.assertEqual(len(data['rows']), 10)
        self.assertEqual(len(ctx), before)

    def test_same_problems_as_the_editor_may_attach(self):
        self.assertEqual(sorted(self.codes()), ['lib-easy', 'lib-hard', 'lib-plain', 'lib-public'])
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse('contest_problem_library')).status_code, 403)
        admin = create_user(username='lib_admin', is_superuser=True)
        self.client.force_login(admin)
        self.assertIn('lib-secret', self.codes())

    def test_filters_match_the_old_client_side_rules(self):
        self.assertEqual(self.codes(q='HARD'), ['lib-hard'])
        self.assertEqual(self.codes(format='M'), ['lib-hard'])
        self.assertEqual(self.codes(format='multiple'), ['lib-hard'])
        self.assertEqual(self.codes(format='single'), [])
        # A problem tagged easy and hard is easy (Problem.difficulty takes the easiest).
        self.assertEqual(self.codes(diff='easy'), ['lib-easy'])
        self.assertEqual(self.codes(diff='hard'), ['lib-hard'])
        self.assertEqual(sorted(self.codes(diff='none')), ['lib-plain', 'lib-public'])
        self.assertEqual(sorted(self.codes(course=self.course.id)), ['lib-easy', 'lib-hard', 'lib-plain'])
        self.assertEqual(self.codes(tag=self.tag.id), ['lib-easy'])
        self.assertEqual(sorted(self.codes(ac_min=40, ac_max=60)), ['lib-easy', 'lib-public'])
        self.assertEqual(self.codes(sort='ac', desc='1')[:2], ['lib-hard', 'lib-public'])
        self.assertEqual(self.codes(sort='diff')[:2], ['lib-easy', 'lib-hard'])
        row = {r['code']: r for r in self.library(page_size=50)['rows']}['lib-easy']
        self.assertEqual((row['diff'], row['knowledge'], row['org_ids']), ('easy', [self.tag.id], [self.course.id]))
        self.assertEqual(row['status'], 'unsolved')

    def test_paging(self):
        data = self.library(page_size=3, page=1)
        self.assertEqual((data['total'], data['pages'], data['page'], len(data['rows'])), (4, 2, 1, 1))
        self.assertEqual(self.library(page_size=3, page=99)['page'], 1)


class TeacherListQueryCountTest(TestCase):
    """Lists a teacher opens cost the same number of queries for 3 rows as for 15.

    Before: submission lists ran ~2 queries per row (assignment editors, deferred ai_points) plus
    one translation per question; the problem list one editor query per row; the assignment list
    one editor query per row; the leaderboard two per row (deferred first/last name).
    """
    fixtures = ['language_small.json']

    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.teacher = create_user(username='qc_teacher')
        cls.teacher.groups.add(ensure_instructors_group())
        cls.course = Organization.objects.create(name='QC course', slug='qc-course', short_name='QC', is_open=False)
        cls.course.admins.add(cls.teacher.profile)
        cls.contest = create_contest(key='qcassign', name='QC assignment', is_visible=True,
                                     start_time=now - timedelta(days=2), end_time=now - timedelta(days=1),
                                     authors=('qc_teacher',), organizations=('QC course',))
        cls.language = Language.get_answer_language()
        cls.n = 0

    def add_rows(self, count):
        for _ in range(count):
            n = type(self).n = type(self).n + 1
            problem = create_problem('qc-p%02d' % n, name='QC problem %d' % n, submission_type='S', is_public=True,
                                     points=5, authors=('qc_teacher',))
            cp = ContestProblem.objects.create(contest=self.contest, problem=problem, points=5, order=n)
            student = create_user(username='qc_s%02d' % n, first_name='First', last_name='Last%d' % n)
            participation = ContestParticipation.objects.create(contest=self.contest, user=student.profile)
            sub = Submission.objects.create(user=student.profile, problem=problem, language=self.language,
                                            status='D', result='AC', points=5, case_points=5, case_total=5,
                                            contest_object=self.contest)
            SubmissionSource.objects.create(submission=sub, source='{"text": "x"}')
            ContestSubmission.objects.create(submission=sub, problem=cp, participation=participation, points=5)
            create_contest(key='qcextra%02d' % n, is_visible=True, authors=('qc_teacher',),
                           start_time=timezone.now() - timedelta(days=3), end_time=timezone.now() - timedelta(days=2))
            Profile.objects.filter(id=student.profile.id).update(performance_points=n)

    def queries(self, url):
        self.client.get(url)
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return len(ctx)

    def assert_flat(self, url):
        self.add_rows(3)
        before = self.queries(url)
        self.add_rows(12)
        self.assertEqual(self.queries(url), before, url)

    def setUp(self):
        self.client.force_login(self.teacher)

    def test_assignment_submissions(self):
        self.assert_flat(reverse('all_submissions') + '?contest=qcassign')

    def test_all_submissions(self):
        self.assert_flat(reverse('all_submissions'))

    def test_problem_list(self):
        self.assert_flat(reverse('problem_list'))

    def test_assignment_list(self):
        self.assert_flat(reverse('contest_list'))

    def test_leaderboard(self):
        self.assert_flat(reverse('user_list'))

    def test_contest_stats(self):
        self.assert_flat(reverse('contest_stats', args=['qcassign']))


class PracticeGradeScopeTest(TestCase):
    """The practice grade desk finds a teacher's rows through the problem index.

    Before: the editable problems were a subquery, and MariaDB walked every submission by date
    probing each one (200+ ms at 150,000 submissions). Now they are a literal id list; the rows
    in scope are the same.
    """
    fixtures = ['language_small.json']

    @classmethod
    def setUpTestData(cls):
        cls.teacher = create_user(username='pg_teacher')
        cls.teacher.groups.add(ensure_instructors_group())
        cls.admin = create_user(username='pg_admin', is_superuser=True)
        cls.student = create_user(username='pg_student')
        language = Language.get_answer_language()
        mine = create_problem('pg-mine', is_public=True, authors=('pg_teacher',))
        curated = create_problem('pg-curated', is_public=True, curators=('pg_teacher',))
        other = create_problem('pg-other', is_public=True)
        contest = create_contest(key='pgcontest')
        cls.subs = {}
        for name, problem, in_contest in (('mine', mine, False), ('curated', curated, False),
                                          ('other', other, False), ('assignment', mine, True)):
            cls.subs[name] = Submission.objects.create(
                user=cls.student.profile, problem=problem, language=language, status='D', result='AC',
                contest_object=contest if in_contest else None)

    def ids(self, user):
        return {s.id for s in practice_grade_queryset(user)}

    def test_scope(self):
        self.assertEqual(self.ids(self.teacher), {self.subs['mine'].id, self.subs['curated'].id})
        self.assertEqual(self.ids(self.admin), {self.subs['mine'].id, self.subs['curated'].id, self.subs['other'].id})
        self.assertEqual(self.ids(self.student), set())

    def test_problems_are_literal_ids(self):
        sql = str(practice_grade_queryset(self.teacher).query)
        self.assertNotIn('judge_problem_authors', sql)
        self.assertNotIn('judge_problem_curators', sql)


class CoursePageCatalogTest(TestCase):
    """The Apply-as-instructor catalog (every course and class) is fetched, not inlined.

    Before: each course, class and course-list page carried it, half of a course page on the
    teacher-scale seed (30 KB at 150 courses) and growing with every course.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = create_user(username='cat_student')
        cls.course = Organization.objects.create(name='Cat course', slug='cat-course', short_name='CAT',
                                                 is_open=True)
        cls.course.members.add(cls.user.profile)

    def test_course_page_does_not_grow_with_other_courses(self):
        self.client.force_login(self.user)
        url = self.course.get_absolute_url()
        self.client.get(url)
        size = len(self.client.get(url).content)
        for i in range(25):
            org = Organization.objects.create(name='Other course number %d' % i, slug='other-%d' % i,
                                              short_name='OC%d' % i, is_open=True)
            Class.objects.create(organization=org, name='Other class %d' % i, slug='other-class-%d' % i)
        self.assertLess(len(self.client.get(url).content) - size, 200)
        data = self.client.get(reverse('organization_instructor_apply_catalog')).json()
        self.assertEqual(len(data['courses']), 26)
        other = [c for c in data['courses'] if c['short_name'] == 'OC3'][0]
        self.assertEqual([k['name'] for k in other['classes']], ['Other class 3'])

    def test_catalog_needs_login(self):
        response = self.client.get(reverse('organization_instructor_apply_catalog'))
        self.assertEqual(response.status_code, 302)
