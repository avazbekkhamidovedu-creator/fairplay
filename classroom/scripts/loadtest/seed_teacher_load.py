#!/usr/bin/env python
"""Seed many teachers for the teacher load test (docs/classroom/reports/scaling.md).

Creates, in the database named by the usual CLASSROOM_DB_* settings:

* teachers ``ltt001`` .. ``lttNNN`` (instructors), password ``loadtest-pw``;
* for each teacher a course ``ltt-cNNN`` (teacher is admin and instructor), a class of
  ``--students`` students (``ltsNNN_MM``), 4 questions (3 multiple choice, 1 short answer) and a
  running homework ``lttNNNhw`` with every student's answer to each question, so the grading
  page has real rows;
* a JSON map ``scripts/loadtest/teacher_load.json`` (teacher -> course id, homework key, tag)
  that locust_teachers.py reads.

    python scripts/loadtest/seed_teacher_load.py [--teachers 500] [--students 10] [--reset]

``--reset`` deletes everything a previous run created first. Refuses a database whose name lacks
``lt`` or ``load`` unless ``--force``.
"""
import argparse
import json
import os
import sys
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dmoj.settings')

try:
    import MySQLdb  # noqa: F401
except ImportError:
    import pymysql
    pymysql.install_as_MySQLdb()

import django  # noqa: E402
django.setup()  # noqa: I202
from django.conf import settings  # noqa: E402
from django.contrib.auth.hashers import make_password  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from django.db import transaction  # noqa: E402
from django.utils import timezone  # noqa: E402

from judge.models import (Class, Contest, ContestParticipation, ContestProblem, ContestSubmission,  # noqa: E402
                          Language, Organization, Problem, ProblemGroup, ProblemMCQConfig, ProblemMCQOption,
                          ProblemShortAnswerConfig, ProblemType, Profile, Submission, SubmissionSource)
from judge.models.notification import ensure_instructors_group  # noqa: E402

PASSWORD = 'loadtest-pw'
MAP_PATH = os.path.join(HERE, 'teacher_load.json')
TAG = 'exercise'


def reset():
    Contest.objects.filter(key__regex=r'^ltt\d+hw$').delete()
    Problem.objects.filter(code__startswith='ltt').delete()
    Class.objects.filter(slug__startswith='ltt-c').delete()
    Organization.objects.filter(slug__startswith='ltt-c').delete()
    User.objects.filter(username__regex=r'^(ltt\d+|lts\d+_\d+)$').delete()


def seed(teachers, students_per):
    now = timezone.now()
    language = Language.get_default_language()
    answer_language = Language.get_answer_language()
    password = make_password(PASSWORD)
    group, _ = ProblemGroup.objects.get_or_create(name='course', defaults={'full_name': 'Course work'})
    ptype, _ = ProblemType.objects.get_or_create(name=TAG, defaults={'full_name': 'Exercise'})
    instructors = ensure_instructors_group()

    names = ['ltt%03d' % t for t in range(1, teachers + 1)]
    names += ['lts%03d_%02d' % (t, s) for t in range(1, teachers + 1) for s in range(1, students_per + 1)]
    User.objects.bulk_create([User(username=n, email=n + '@example.edu', first_name=n, password=password,
                                   is_active=True) for n in names], batch_size=1000)
    users = {u.username: u for u in User.objects.filter(username__in=names)}
    Profile.objects.bulk_create([Profile(user=u, language=language) for u in users.values()], batch_size=1000)
    profiles = {p.user.username: p for p in Profile.objects.filter(user__in=users.values()).select_related('user')}
    instructors.user_set.add(*[users['ltt%03d' % t] for t in range(1, teachers + 1)])

    mapping = {}
    for t in range(1, teachers + 1):
        tname = 'ltt%03d' % t
        prof = profiles[tname]
        studs = [profiles['lts%03d_%02d' % (t, s)] for s in range(1, students_per + 1)]
        course = Organization.objects.create(name='LTT course %03d' % t, slug='ltt-c%03d' % t,
                                             short_name='LTT%03d' % t, subject='cs', is_open=False, about='Load.')
        course.admins.add(prof)
        course.instructors.add(prof)
        course.members.add(*studs)
        section = Class.objects.create(organization=course, name='LTT%03d-01' % t, slug='ltt-c%03d-01' % t,
                                       description='Students.', academic_year='2026-2027', term_session='Fall 2026')
        section.members.add(*studs)
        contest = Contest.objects.create(
            key='ltt%03dhw' % t, name='Homework %03d' % t, format_name='default',
            start_time=now - timedelta(hours=1), end_time=now + timedelta(days=5), is_visible=True,
            is_organization_private=True, scoreboard_visibility=Contest.SCOREBOARD_AFTER_CONTEST)
        contest.authors.add(prof)
        contest.organizations.add(course)
        contest.classes.add(section)
        rows = []
        for q in range(1, 5):
            short = q == 4
            problem = Problem.objects.create(
                code='ltt%03dq%d' % (t, q), name='Question %d of course %03d' % (q, t),
                description='Load test question %d.' % q, points=5, partial=True, time_limit=2,
                memory_limit=262144, group=group, submission_type='S' if short else 'M', is_public=False,
                is_organization_private=True)
            problem.types.add(ptype)
            problem.authors.add(prof)
            problem.organizations.add(course)
            if short:
                ProblemShortAnswerConfig.objects.create(problem=problem, reference_answer='A short answer.')
                options = None
            else:
                ProblemMCQConfig.objects.create(problem=problem, multiple_select=False)
                options = ProblemMCQOption.objects.bulk_create([
                    ProblemMCQOption(problem=problem, order=i, text='Option %d' % i, is_correct=(i == 1))
                    for i in range(3)])
                options = list(ProblemMCQOption.objects.filter(problem=problem).order_by('order'))
            row = ContestProblem.objects.create(contest=contest, problem=problem, points=5, order=q, partial=True)
            rows.append((problem, row, options))
        for i, student in enumerate(studs):
            part = ContestParticipation.objects.create(contest=contest, user=student,
                                                       real_start=now - timedelta(minutes=30))
            for problem, row, options in rows:
                if options:
                    option = options[i % len(options)]
                    source, result, points = {'selected': [option.id]}, ('AC' if option.is_correct else 'WA'), \
                        (5 if option.is_correct else 0)
                else:
                    source, result, points = {'text': 'Answer from %s.' % student.user.username}, 'AC', 0
                sub = Submission.objects.create(
                    user=student, problem=problem, language=answer_language, status='D', result=result,
                    points=points, case_points=points, case_total=5, time=0.0, memory=0, contest_object=contest)
                SubmissionSource.objects.create(submission=sub, source=json.dumps(source))
                ContestSubmission.objects.create(submission=sub, problem=row, participation=part, points=points)
        mapping[tname] = {'course': course.id, 'homework': contest.key,
                          'question': rows[0][0].code, 'short': rows[3][0].code}
    return mapping


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--teachers', type=int, default=500)
    parser.add_argument('--students', type=int, default=10)
    parser.add_argument('--reset', action='store_true')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    name = settings.DATABASES['default']['NAME']
    if not args.force and 'lt' not in name and 'load' not in name:
        sys.exit('Refusing to seed database %r (name lacks "lt"/"load"; pass --force).' % name)
    with transaction.atomic():
        if args.reset:
            reset()
        elif User.objects.filter(username='ltt001').exists():
            sys.exit('Already seeded (ltt001 exists). Use --reset to recreate.')
        mapping = seed(args.teachers, args.students)
    with open(MAP_PATH, 'w') as f:
        json.dump({'password': PASSWORD, 'tag': TAG, 'teachers': mapping}, f)
    print('Seeded %d teachers x %d students in %s; map in %s.' % (args.teachers, args.students, name, MAP_PATH))


if __name__ == '__main__':
    main()
