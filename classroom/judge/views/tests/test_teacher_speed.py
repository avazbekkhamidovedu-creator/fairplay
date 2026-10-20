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
from django.utils import timezone

from judge.models import Class, Contest, Organization
from judge.models.tests.util import create_contest, create_user
from judge.subjects.access import teaching_courses, user_course_subject_keys


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
