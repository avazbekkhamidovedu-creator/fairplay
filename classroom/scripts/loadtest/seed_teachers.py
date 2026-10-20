#!/usr/bin/env python
"""Seed a university-scale Classroom for the teacher-side speed audit (docs/classroom/reports/teacher-speed.md).

Creates, with bulk inserts (a few minutes on a small box), in the database named by CLASSROOM_DB_*:

* 500 instructors ``tprof001`` .. ``tprof500`` (Instructors group) and 3,000 students
  ``tstu0001`` .. ``tstu3000``, password ``loadtest-pw``. ``tprof001`` is the "typical instructor"
  the measurements log in as (two courses, like most of them).
* 150 courses ``TS001`` .. ``TS150`` (slug ``ts001`` ..), each with 2 classes, 3-6 instructors (all
  course admins) and ~60 students; every student is in 3 courses and one class of each.
* 6,000 problems ``ts_00001`` .. (40 per course): programming, multiple choice, fill in the blank,
  short answer and file upload, with their configs/options/blanks, authors spread over the
  course's instructors, 60 knowledge tags plus easy/medium/hard, a third of them public.
* 1,500 assignments ``tsa00001`` .. (10 per course: homework, quiz and exam via contest tags),
  spread from 120 days ago to 30 days ahead, each linked to one or both classes, 6 questions each.
* ~150,000 submissions with sources and contest submissions (plus some practice submissions),
  ~40,000 participations with scores, manual grades on some short answers, assignment drafts on
  the running assignments and Excel-import drafts for a fifth of the instructors.

Run from classroom/ with the app's virtualenv:

    python scripts/loadtest/seed_teachers.py [--reset] [--scale 1.0] [--submissions 150000]

``--reset`` deletes a previous run first (everything named ``ts*``/``tprof*``/``tstu*``).
Without it, an existing seed is an error. It refuses a database whose name does not contain
``lt``, ``load``, ``_p`` or ``scale`` unless ``--force`` is given.
"""
import argparse
import json
import os
import random
import sys
import time
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
from django.core.cache import cache  # noqa: E402
from django.db import connection, transaction  # noqa: E402
from django.db.models import Max  # noqa: E402
from django.utils import timezone  # noqa: E402

from judge.models import (AssignmentDraft, Class, Contest, ContestParticipation, ContestProblem,  # noqa: E402
                          ContestSubmission, ContestTag, Language, Organization, Problem, ProblemBlank,
                          ProblemCodeConfig, ProblemFileConfig, ProblemFillBlankConfig, ProblemGroup,
                          ProblemImportDraft, ProblemImportItem, ProblemMCQConfig, ProblemMCQOption,
                          ProblemShortAnswerConfig, ProblemType, Profile, Submission, SubmissionManualGrade,
                          SubmissionSource)
from judge.models.notification import ensure_instructors_group  # noqa: E402

PASSWORD = 'loadtest-pw'
N_PROF, N_STUD, N_COURSE = 500, 3000, 150
PROBLEMS_PER_COURSE, ASSIGNMENTS_PER_COURSE, QUESTIONS_PER_ASSIGNMENT = 40, 10, 6
COURSES_PER_STUDENT = 3
# Per course: 12 code, 12 MCQ, 6 blank, 6 short answer, 4 file upload.
TYPE_PATTERN = 'CCCMMMBSCMMCMFCMBSCMMCBSMFCMBSCMCFMBSCMF'
KNOWLEDGE_TAGS = ['ts-%s' % t for t in (
    'loops', 'recursion', 'arrays', 'strings', 'sorting', 'searching', 'graphs', 'trees', 'dp', 'greedy',
    'math', 'geometry', 'probability', 'matrices', 'calculus', 'sets', 'logic', 'proofs', 'hashing', 'heaps',
    'stacks', 'queues', 'linked-lists', 'bits', 'io', 'functions', 'classes', 'files', 'regex', 'sql',
    'networks', 'os', 'memory', 'pointers', 'complexity', 'dfs', 'bfs', 'shortest', 'mst', 'flows',
    'strings2', 'parsing', 'simulation', 'implementation', 'brute', 'binary', 'two-pointers', 'prefix',
    'stats', 'algebra', 'vectors', 'series', 'limits', 'integrals', 'ode', 'econ', 'bio', 'chem', 'phys',
    'writing')]
WORDS = ('sum', 'pairs', 'path', 'grid', 'tree', 'array', 'string', 'graph', 'matrix', 'prime', 'loop',
         'list', 'tuple', 'stack', 'queue', 'heap', 'set', 'map', 'sort', 'search')


def chunked_create(model, objs, batch=5000):
    for i in range(0, len(objs), batch):
        model.objects.bulk_create(objs[i:i + batch], batch_size=batch)


def next_id(model):
    return (model.objects.aggregate(m=Max('id'))['m'] or 0) + 1


def reset():
    t = time.time()
    problems = Problem.objects.filter(code__startswith='ts_')
    subs = Submission.objects.filter(problem__in=problems)
    contests = Contest.objects.filter(key__startswith='tsa')
    users = Profile.objects.filter(user__username__regex=r'^(tprof|tstu)\d+$')
    Profile.objects.filter(current_contest__contest__in=contests).update(current_contest=None)
    # Leaf tables first with raw deletes: the ORM's cascade collector is far too slow for 150k rows.
    for qs in (SubmissionSource.objects.filter(submission__in=subs),
               ContestSubmission.objects.filter(submission__in=subs),
               SubmissionManualGrade.objects.filter(submission__in=subs),
               AssignmentDraft.objects.filter(problem__in=problems)):
        qs._raw_delete(qs.db)
    with connection.cursor() as c:
        c.execute('DELETE s FROM judge_submissiontestcase s JOIN judge_submission u ON u.id = s.submission_id '
                  'JOIN judge_problem p ON p.id = u.problem_id WHERE p.code LIKE %s', ['ts\\_%'])
        c.execute('DELETE u FROM judge_submission u JOIN judge_problem p ON p.id = u.problem_id '
                  'WHERE p.code LIKE %s', ['ts\\_%'])
    parts = ContestParticipation.objects.filter(contest__in=contests)
    parts._raw_delete(parts.db)
    contests.delete()
    problems.delete()
    ProblemImportDraft.objects.filter(owner__in=users).delete()
    Class.objects.filter(slug__regex=r'^ts\d+-\d+$').delete()
    Organization.objects.filter(slug__regex=r'^ts\d+$').delete()
    User.objects.filter(username__regex=r'^(tprof|tstu)\d+$').delete()
    ProblemType.objects.filter(name__startswith='ts-').delete()
    print('reset: %.1fs' % (time.time() - t))


def make_users(language):
    pw = make_password(PASSWORD)
    uid, pid = next_id(User), next_id(Profile)
    names = ['tprof%03d' % i for i in range(1, N_PROF + 1)] + ['tstu%04d' % i for i in range(1, N_STUD + 1)]
    users = [User(id=uid + i, username=n, email='%s@example.edu' % n, password=pw, is_active=True,
                  first_name=('Prof' if n.startswith('tprof') else 'Student'), last_name=n[-4:])
             for i, n in enumerate(names)]
    chunked_create(User, users)
    profiles = [Profile(id=pid + i, user_id=u.id, language=language) for i, u in enumerate(users)]
    chunked_create(Profile, profiles)
    group = ensure_instructors_group()
    User.groups.through.objects.bulk_create(
        [User.groups.through(user_id=u.id, group_id=group.id) for u in users[:N_PROF]])
    return profiles[:N_PROF], profiles[N_PROF:]


def seed(n_subs, rng):
    t0 = time.time()
    now = timezone.now()
    language = Language.get_default_language()
    answer_lang = Language.get_answer_language()
    code_langs = list(Language.objects.filter(key__in=('PY3', 'CPP17', 'C', 'JAVA8')))
    group, _ = ProblemGroup.objects.get_or_create(name='course', defaults={'full_name': 'Course work'})
    tag_rows = [ProblemType(name=n, full_name=n[3:].replace('-', ' ').title()) for n in KNOWLEDGE_TAGS]
    ProblemType.objects.bulk_create(tag_rows, ignore_conflicts=True)
    knowledge = list(ProblemType.objects.filter(name__in=KNOWLEDGE_TAGS))
    difficulty = [ProblemType.objects.get_or_create(name=n, defaults={'full_name': n.title()})[0]
                  for n in ('easy', 'medium', 'hard')]
    ctags = {n: ContestTag.objects.get_or_create(name=n, defaults={'color': c})[0]
             for n, c in (('homework', '#2980B9'), ('quiz', '#27AE60'), ('exam', '#E74C3C'))}

    profs, studs = make_users(language)
    print('users: %.1fs' % (time.time() - t0))

    # Courses and classes.
    oid, cid = next_id(Organization), next_id(Class)
    courses = [Organization(id=oid + i, name='TS%03d Course %d' % (i + 1, i + 1), slug='ts%03d' % (i + 1),
                            short_name='TS%03d' % (i + 1), subject=('cs' if i % 3 else 'math'), is_open=False,
                            about='Teacher-scale course %d.' % (i + 1))
               for i in range(N_COURSE)]
    chunked_create(Organization, courses)
    classes = [Class(id=cid + 2 * i + k, organization_id=c.id, name='TS%03d-%02d' % (i + 1, k + 1),
                     slug='ts%03d-%02d' % (i + 1, k + 1), academic_year='2026-2027', term_session='Fall 2026')
               for i, c in enumerate(courses) for k in range(2)]
    chunked_create(Class, classes)
    course_profs = {i: [] for i in range(N_COURSE)}
    for j, p in enumerate(profs):
        course_profs[j % N_COURSE].append(p)
        if j % 3 == 0:
            course_profs[(j + 3) % N_COURSE].append(p)
    OrgAdmins, OrgInstr = Organization.admins.through, Organization.instructors.through
    OrgAdmins.objects.bulk_create([OrgAdmins(organization_id=courses[i].id, profile_id=p.id)
                                   for i, ps in course_profs.items() for p in ps])
    OrgInstr.objects.bulk_create([OrgInstr(organization_id=courses[i].id, profile_id=p.id)
                                  for i, ps in course_profs.items() for p in ps])
    course_studs = {i: [] for i in range(N_COURSE)}
    class_studs = {c.id: [] for c in classes}
    member_rows, class_rows = [], []
    for s, p in enumerate(studs):
        for k in range(COURSES_PER_STUDENT):
            ci = (s * 7 + k * 53) % N_COURSE
            course_studs[ci].append(p)
            section = classes[2 * ci + (s // N_COURSE) % 2]
            class_studs[section.id].append(p)
            member_rows.append(Profile.organizations.through(profile_id=p.id, organization_id=courses[ci].id,
                                                             sort_value=k + 1))
            class_rows.append(Class.members.through(class_id=section.id, profile_id=p.id))
    chunked_create(Profile.organizations.through, member_rows)
    chunked_create(Class.members.through, class_rows)
    print('courses: %.1fs' % (time.time() - t0))

    # Problems.
    pid = next_id(Problem)
    problems, authors, ptypes, porgs, plangs = [], [], [], [], []
    mcq_cfg, mcq_opt, blank_cfg, blanks, short_cfg, file_cfg, code_cfg = [], [], [], [], [], [], []
    course_problems = {i: [] for i in range(N_COURSE)}
    for ci, course in enumerate(courses):
        for k in range(PROBLEMS_PER_COURSE):
            n = ci * PROBLEMS_PER_COURSE + k
            kind = TYPE_PATTERN[k]
            words = ' '.join(rng.sample(WORDS, 3))
            p = Problem(id=pid + n, code='ts_%05d' % (n + 1), name='%s %s %d' % (course.short_name, words, k + 1),
                        description='Problem %d of %s: %s.\n\nExplain or compute the %s.' % (
                            k + 1, course.short_name, words, words),
                        points=rng.choice((5, 10, 10, 20)), partial=True, time_limit=2, memory_limit=262144,
                        group_id=group.id, submission_type=kind, is_public=(k % 3 == 0),
                        is_organization_private=True, date=now - timedelta(days=rng.randint(0, 400)),
                        user_count=rng.randint(0, 60), ac_rate=rng.random() * 100)
            problems.append(p)
            course_problems[ci].append(p)
            owners = course_profs[ci]
            authors.append(Problem.authors.through(problem_id=p.id, profile_id=owners[k % len(owners)].id))
            for tag in rng.sample(knowledge, 2) + [difficulty[k % 3]]:
                ptypes.append(Problem.types.through(problem_id=p.id, problemtype_id=tag.id))
            porgs.append(Problem.organizations.through(problem_id=p.id, organization_id=course.id))
            if kind == 'C':
                for lang in code_langs:
                    plangs.append(Problem.allowed_languages.through(problem_id=p.id, language_id=lang.id))
                if k % 4 == 0:
                    code_cfg.append(ProblemCodeConfig(problem_id=p.id))
            elif kind == 'M':
                mcq_cfg.append(ProblemMCQConfig(problem_id=p.id, multiple_select=(k % 5 == 0)))
                for o in range(4):
                    mcq_opt.append(ProblemMCQOption(problem_id=p.id, order=o, text='Option %d' % (o + 1),
                                                    is_correct=(o == k % 4)))
            elif kind == 'B':
                blank_cfg.append(ProblemFillBlankConfig(problem_id=p.id))
                for o in range(2):
                    blanks.append(ProblemBlank(problem_id=p.id, order=o, label='', answers=WORDS[(k + o) % 20]))
            elif kind == 'S':
                short_cfg.append(ProblemShortAnswerConfig(problem_id=p.id, reference_answer='Reference %d.' % n))
            elif kind == 'F':
                file_cfg.append(ProblemFileConfig(problem_id=p.id, allowed_extensions='pdf'))
    chunked_create(Problem, problems)
    for model, rows in ((Problem.authors.through, authors), (Problem.types.through, ptypes),
                        (Problem.organizations.through, porgs), (Problem.allowed_languages.through, plangs),
                        (ProblemMCQConfig, mcq_cfg), (ProblemMCQOption, mcq_opt), (ProblemFillBlankConfig, blank_cfg),
                        (ProblemBlank, blanks), (ProblemShortAnswerConfig, short_cfg), (ProblemFileConfig, file_cfg),
                        (ProblemCodeConfig, code_cfg)):
        chunked_create(model, rows)
    print('problems: %.1fs' % (time.time() - t0))

    # Assignments.
    kid, cpid = next_id(Contest), next_id(ContestProblem)
    contests, cauthors, ccurators, corgs, cclasses, ctag_rows, cproblems = [], [], [], [], [], [], []
    contest_info = []  # (contest, [ContestProblem], [student profiles])
    for ci, course in enumerate(courses):
        for k in range(ASSIGNMENTS_PER_COURSE):
            n = ci * ASSIGNMENTS_PER_COURSE + k
            tag = 'homework' if k < 6 else ('quiz' if k < 8 else 'exam')
            # Homework 1-6 every 18 days (the last one is running), a past and an upcoming quiz, a past
            # (finalized) exam and an upcoming one; in every tenth course that exam is running now.
            if k < 6:
                start = now - timedelta(days=18 * (5 - k) + rng.randint(0, 3), hours=rng.randint(0, 23))
            elif k == 9 and ci % 10 == 0:
                start = now - timedelta(minutes=30)
            else:
                start = now + timedelta(days=(-10, 5, -40, 14)[k - 6], hours=rng.randint(0, 23))
            length = timedelta(days=7) if tag == 'homework' else timedelta(hours=1 if tag == 'quiz' else 2)
            fmt = 'default' if tag == 'homework' else 'exam'
            c = Contest(id=kid + n, key='tsa%05d' % (n + 1), name='%s %s %d' % (course.short_name, tag.title(), k + 1),
                        description='Assignment %d for %s.' % (k + 1, course.short_name), start_time=start,
                        end_time=start + length, is_visible=True, is_organization_private=True,
                        format_name=fmt, scoreboard_visibility=Contest.SCOREBOARD_AFTER_CONTEST,
                        results_finalized=(fmt == 'exam' and start + length < now))
            contests.append(c)
            owners = course_profs[ci]
            cauthors.append(Contest.authors.through(contest_id=c.id, profile_id=owners[k % len(owners)].id))
            for o in owners:
                if o.id != owners[k % len(owners)].id:
                    ccurators.append(Contest.curators.through(contest_id=c.id, profile_id=o.id))
            corgs.append(Contest.organizations.through(contest_id=c.id, organization_id=course.id))
            sections = [classes[2 * ci + k % 2]] if k % 3 else [classes[2 * ci], classes[2 * ci + 1]]
            for s in sections:
                cclasses.append(Contest.classes.through(contest_id=c.id, class_id=s.id))
            ctag_rows.append(Contest.tags.through(contest_id=c.id, contesttag_id=ctags[tag].id))
            rows = []
            for order, prob in enumerate(rng.sample(course_problems[ci], QUESTIONS_PER_ASSIGNMENT), start=1):
                cp = ContestProblem(id=cpid + len(cproblems), contest_id=c.id, problem_id=prob.id,
                                    points=int(prob.points), order=order, partial=True)
                cp.problem_obj = prob
                cproblems.append(cp)
                rows.append(cp)
            members = [p for s in sections for p in class_studs[s.id]]
            contest_info.append((c, rows, members))
    chunked_create(Contest, contests)
    for model, rows in ((Contest.authors.through, cauthors), (Contest.curators.through, ccurators),
                        (Contest.organizations.through, corgs), (Contest.classes.through, cclasses),
                        (Contest.tags.through, ctag_rows), (ContestProblem, cproblems)):
        chunked_create(model, rows)
    print('assignments: %.1fs' % (time.time() - t0))

    # Participations and submissions: a participant answers each question with probability q,
    # 1-3 times, so that the total lands near n_subs.
    started = [(c, rows, members) for c, rows, members in contest_info if c.start_time < now]
    pairs = sum(len(members) * len(rows) for _, rows, members in started) * 0.85
    # 1.75 attempts on average, and a tenth more as practice submissions below.
    q = min(1.0, n_subs / 1.1 / (pairs * 1.75)) if pairs else 0
    partid, subid = next_id(ContestParticipation), next_id(Submission)
    parts, subs, sources, csubs, grades, drafts = [], [], [], [], [], []
    date_field = Submission._meta.get_field('date')
    date_field.auto_now_add = False  # keep the generated dates
    for c, rows, members in started:
        running = c.end_time > now
        for member in members:
            if rng.random() > 0.85:
                continue
            part = ContestParticipation(id=partid + len(parts), contest_id=c.id, user_id=member.id,
                                        real_start=c.start_time + timedelta(minutes=rng.randint(0, 30)))
            parts.append(part)
            total = 0
            for cp in rows:
                prob = cp.problem_obj
                if running and rng.random() < 0.4:
                    drafts.append(AssignmentDraft(participation_id=part.id, problem_id=prob.id,
                                                  content='{"text": "draft"}'))
                if rng.random() > q:
                    continue
                best = 0
                for attempt in range(rng.choice((1, 1, 2, 3))):
                    ok = rng.random() < 0.6
                    pts = float(cp.points if ok else (cp.points / 2 if rng.random() < 0.3 else 0))
                    best = max(best, pts)
                    kind = prob.submission_type
                    s = Submission(id=subid + len(subs), user_id=member.id, problem_id=prob.id,
                                   language=(code_langs[attempt % len(code_langs)] if kind == 'C' else answer_lang),
                                   date=min(now, part.real_start + timedelta(minutes=5 + 7 * attempt)),
                                   status='D', result='AC' if ok else 'WA', points=pts, case_points=pts,
                                   case_total=cp.points, time=0.01, memory=1024, contest_object_id=c.id)
                    subs.append(s)
                    src = ('print(sum(map(int, input().split())))' if kind == 'C' else
                           json.dumps({'selected': [attempt]} if kind == 'M' else
                                      {'answers': ['x']} if kind == 'B' else {'text': 'answer %d' % s.id}))
                    sources.append(SubmissionSource(submission_id=s.id, source=src))
                    csubs.append(ContestSubmission(submission_id=s.id, problem_id=cp.id, participation_id=part.id,
                                                   points=pts))
                    if kind in 'SF' and not running and rng.random() < 0.5:
                        grades.append(SubmissionManualGrade(submission_id=s.id, points=pts, machine_points=0,
                                                            released=c.results_finalized))
                total += best
            part.score = total
    chunked_create(ContestParticipation, parts)
    print('participations %d: %.1fs' % (len(parts), time.time() - t0))
    # A tenth more as practice submissions outside any assignment.
    for i in range(len(subs) // 10):
        prob = rng.choice(problems)
        member = rng.choice(studs)
        s = Submission(id=subid + len(subs), user_id=member.id, problem_id=prob.id,
                       language=code_langs[0] if prob.submission_type == 'C' else answer_lang,
                       date=now - timedelta(minutes=rng.randint(1, 200000)), status='D',
                       result=rng.choice(('AC', 'WA')), points=0.0, case_points=0, case_total=prob.points,
                       time=0.01, memory=1024)
        subs.append(s)
        sources.append(SubmissionSource(submission_id=s.id, source='{"text": "practice"}'))
    chunked_create(Submission, subs)
    date_field.auto_now_add = True
    print('submissions %d: %.1fs' % (len(subs), time.time() - t0))
    chunked_create(SubmissionSource, sources)
    chunked_create(ContestSubmission, csubs)
    chunked_create(SubmissionManualGrade, grades)
    chunked_create(AssignmentDraft, drafts)
    print('sources/drafts: %.1fs' % (time.time() - t0))

    # Excel-import drafts for a fifth of the instructors.
    did = next_id(ProblemImportDraft)
    idrafts = [ProblemImportDraft(id=did + i, owner_id=p.id) for i, p in enumerate(profs[::5])]
    chunked_create(ProblemImportDraft, idrafts)
    chunked_create(ProblemImportItem, [
        ProblemImportItem(draft_id=d.id, row_number=r + 2, name='Imported %d' % r, submission_type='S',
                          description='Imported question.', answer='x')
        for d in idrafts for r in range(5)])
    return {'profs': len(profs), 'students': len(studs), 'courses': len(courses), 'classes': len(classes),
            'problems': len(problems), 'assignments': len(contests), 'participations': len(parts),
            'submissions': len(subs), 'drafts': len(drafts)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--reset', action='store_true', help='delete a previous teacher-scale seed first')
    parser.add_argument('--submissions', type=int, default=150000)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--force', action='store_true', help='allow any database name')
    args = parser.parse_args()
    name = settings.DATABASES['default']['NAME']
    if not args.force and not any(s in name for s in ('lt', 'load', '_p', 'scale')):
        sys.exit('Refusing to seed database %r (pass --force).' % name)
    t = time.time()
    if args.reset:
        with transaction.atomic():
            reset()
    elif User.objects.filter(username='tprof001').exists():
        sys.exit('Already seeded (tprof001 exists). Use --reset to recreate.')
    with transaction.atomic():
        counts = seed(args.submissions, random.Random(args.seed))
    cache.clear()
    with connection.cursor() as c:
        for table in ('judge_submission', 'judge_problem', 'judge_contest', 'judge_contestparticipation',
                      'judge_contestsubmission', 'judge_profile', 'auth_user'):
            c.execute('ANALYZE TABLE %s' % table)
    print('Seeded in %.0fs into %s: %s; password %r.' % (time.time() - t, name, counts, PASSWORD))


if __name__ == '__main__':
    main()
