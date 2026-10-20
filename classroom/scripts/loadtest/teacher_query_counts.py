#!/usr/bin/env python
"""Per-request cost (SQL queries, wall time, response size) of every teacher-facing page and API.

    CLASSROOM_DEBUG=0 CLASSROOM_ALLOWED_HOSTS=testserver \
        python scripts/loadtest/teacher_query_counts.py [--as tprof001] [--as admin] [--verbose] [--json out.json]
        [--only SUBSTRING]

Runs against the teacher-scale seed (scripts/loadtest/seed_teachers.py) with Django's test client
in-process (view code only: no app server, no network). Each URL is requested once to warm caches
and compiled templates, then measured ``--repeat`` times (the median time is reported). With
--verbose it prints the queries that repeat with different parameters (N+1 suspects). ``--json``
writes the rows so two runs can be compared (``--compare before.json``).
"""
import argparse
import json
import os
import re
import statistics
import sys
import time
from collections import Counter

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
from django.contrib.auth.models import User  # noqa: E402
from django.db import connection  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import CaptureQueriesContext  # noqa: E402
from django.utils import timezone  # noqa: E402

from judge.models import Class, Contest, Organization, Problem, Profile, Submission  # noqa: E402

NUM = re.compile(r"\b\d+\b|'[^']*'")


class Runner:
    def __init__(self, args):
        self.args = args
        self.rows = []

    def measure(self, client, who, label, method, url, **kw):
        if self.args.only and self.args.only not in label and self.args.only not in url:
            return None
        getattr(client, method)(url, **kw)  # warm caches, templates, compressor
        times, response, ctx = [], None, None
        for _ in range(self.args.repeat):
            with CaptureQueriesContext(connection) as ctx:
                start = time.perf_counter()
                response = getattr(client, method)(url, **kw)
                body = b''.join(response.streaming_content) if response.streaming else response.content
                times.append((time.perf_counter() - start) * 1000)
        shapes = Counter(NUM.sub('?', q['sql']) for q in ctx.captured_queries)
        repeated = [(n, s) for s, n in shapes.most_common() if n > 2]
        sql_ms = sum(float(q['time']) for q in ctx.captured_queries) * 1000
        row = {'who': who, 'label': label, 'method': method.upper(), 'url': url, 'status': response.status_code,
               'queries': len(ctx), 'ms': round(statistics.median(times)), 'sql_ms': round(sql_ms),
               'kb': round(len(body) / 1024, 1), 'max_repeat': repeated[0][0] if repeated else 0}
        self.rows.append(row)
        print('%-8s %-34s %3d %5d q %6d ms (sql %5d) %8.1f KB%s' % (
            who, label[:34], row['status'], row['queries'], row['ms'], row['sql_ms'], row['kb'],
            '  (max repeat %d)' % row['max_repeat'] if row['max_repeat'] else ''))
        if self.args.explain:
            self.explain(ctx.captured_queries)
        if self.args.verbose:
            for n, s in repeated[:6]:
                print('          x%-4d %s' % (n, s[:240]))
            slow = sorted(ctx.captured_queries, key=lambda q: -float(q['time']))[:3]
            for q in slow:
                if float(q['time']) > 0.02:
                    print('          %.0f ms  %s' % (float(q['time']) * 1000, q['sql'][:300]))
        return response

    def explain(self, queries):
        """Print full table/index scans of more than ``--scan-rows`` rows (missing-index suspects)."""
        seen = set()
        for q in queries:
            sql = q['sql']
            if not sql.lstrip(' (').upper().startswith('SELECT') or NUM.sub('?', sql) in seen:
                continue
            seen.add(NUM.sub('?', sql))
            with connection.cursor() as cursor:
                cursor.execute('EXPLAIN ' + sql)
                cols = [d[0] for d in cursor.description]
                for row in cursor.fetchall():
                    row = dict(zip(cols, row))
                    if row['type'] in ('ALL', 'index') and int(row['rows'] or 0) > self.args.scan_rows:
                        print('          SCAN %s %s rows=%s key=%s  %s' % (
                            row['table'], row['type'], row['rows'], row['key'], sql[:160]))


def pick_fixtures(username):
    profile = Profile.objects.get(user__username='tprof001')
    courses = list(Organization.objects.filter(admins=profile).order_by('id'))
    course = courses[0]
    section = Class.objects.filter(organization=course).order_by('id').first()
    now = timezone.now()
    in_course = Contest.objects.filter(organizations=course)
    ended_hw = in_course.filter(end_time__lt=now, format_name='default').order_by('-end_time').first()
    running_hw = in_course.filter(start_time__lt=now, end_time__gt=now, format_name='default').first()
    exam = (in_course.filter(format_name='exam', start_time__lt=now).order_by('-end_time').first())
    problems = {}
    for kind in 'CMBSF':
        problems[kind] = Problem.objects.filter(authors=profile, submission_type=kind).order_by('id').first() or \
            Problem.objects.filter(organizations=course, submission_type=kind).order_by('id').first()
    sub = Submission.objects.filter(contest_object=ended_hw).order_by('-id').first()
    student = sub.user.user.username
    return locals()


def run_for(runner, username):
    f = pick_fixtures(username)
    client = Client()
    client.force_login(User.objects.get(username=username))
    who = username
    m = lambda label, url, method='get', **kw: runner.measure(client, who, label, method, url, **kw)  # noqa: E731
    course, section, hw, rhw, exam = f['course'], f['section'], f['ended_hw'], f['running_hw'], f['exam']
    code = f['problems']['C'].code
    course_url = '/organization/%d-%s' % (course.id, course.slug)
    ajax = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}

    m('home', '/')
    m('problems', '/problems/')
    m('problems page 5', '/problems/?page=5')
    m('problems search', '/problems/?search=tree&filtered=1&search_full=1')
    m('problems sort ac_rate', '/problems/?order=-ac_rate')
    m('problems sort date', '/problems/?order=-date')
    m('problems filter course', '/problems/?course=%d' % course.id)
    m('problems filter format MCQ', '/problems/?format=M')
    m('problems filter type', '/problems/?type=%d' % f['problems']['C'].types.first().id)
    m('problems filter author', '/problems/?author=%d' % f['profile'].id)
    m('contests', '/contests/')
    m('contests search', '/contests/?search=Quiz')
    now = timezone.now()
    m('contests calendar', '/contests/%d/%d/' % (now.year, now.month))
    m('course page', course_url)
    m('course users', course_url + '/users')
    m('class page', '%s/class/%d-%s' % (course_url, section.id, section.slug))
    m('assignment create', '/contests/create/')
    m('assignment edit', '/contests/%s/edit/' % hw.key)
    m('assignment edit (running exam)', '/contests/%s/edit/' % exam.key)
    m('check key', '/api/v2/contest/check-key/?key=newkey1')
    m('select2 profile', '/judge-select2/profile/?term=tprof', **ajax)
    m('select2 organization', '/judge-select2/organization/?term=TS0', **ajax)
    m('select2 class', '/judge-select2/class/?term=TS0', **ajax)
    m('select2 problem', '/judge-select2/problem/?term=ts_', **ajax)
    m('select2 contest', '/judge-select2/contest/?term=tsa', **ajax)
    m('select2 author filter', '/judge-select2/author-filter/?term=tprof', **ajax)
    m('preview contest', '/widgets/preview/contest', method='post', data={'content': '**hello** ~x~'})
    m('preview problem', '/widgets/preview/problem', method='post', data={'content': '**hello** ~x~'})
    m('problem create', '/problems/new/')
    for kind in 'CMBSF':
        p = f['problems'][kind]
        if p is not None:
            m('problem edit %s' % kind, '/problems/edit/%s/' % p.code)
    m('problem detail', '/problem/%s' % code)
    m('problem detail MCQ', '/problem/%s' % f['problems']['M'].code)
    m('submissions all', '/submissions/')
    m('submissions all stats', '/submissions/?results=1')
    m('submissions mine', '/submissions/user/%s/' % username)
    m('submissions of student', '/submissions/user/%s/' % f['student'])
    m('submissions problem', '/problem/%s/submissions/' % code)
    m('submissions problem stats', '/problem/%s/submissions/?results=1' % code)
    m('submissions assignment', '/submissions/?contest=%s' % hw.key)
    m('submissions assignment stats', '/submissions/?contest=%s&results=1' % hw.key)
    m('contest submissions user', '/contest/%s/submissions/%s/' % (hw.key, f['student']))
    m('assignment page', '/contest/%s' % hw.key)
    m('assignment page (running)', '/contest/%s' % rhw.key)
    m('grade page', '/contest/%s/grade' % hw.key)
    m('grade page exam', '/contest/%s/grade' % exam.key)
    m('grade status', '/contest/%s/grade/status' % hw.key)
    m('practice grade', '/submissions/grade')
    m('analytics', '/contest/%s/analytics' % hw.key)
    m('ranking', '/contest/%s/ranking/' % hw.key)
    m('ranking (running)', '/contest/%s/ranking/' % rhw.key)
    m('participations', '/contest/%s/participations' % hw.key)
    m('contest stats', '/contest/%s/stats' % hw.key)
    m('user profile', '/user/%s' % username)
    m('student profile', '/user/%s' % f['student'])
    m('user list', '/users/')
    m('admin problems', '/admin/judge/problem/')
    m('admin contests', '/admin/judge/contest/')
    m('admin submissions', '/admin/judge/submission/')
    m('admin organizations', '/admin/judge/organization/')
    m('admin problem change', '/admin/judge/problem/%d/change/' % f['problems']['C'].id)
    m('admin contest change', '/admin/judge/contest/%d/change/' % hw.id)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--as', dest='users', action='append')
    ap.add_argument('--repeat', type=int, default=1)
    ap.add_argument('--verbose', action='store_true')
    ap.add_argument('--explain', action='store_true', help='EXPLAIN every query, print large scans')
    ap.add_argument('--scan-rows', type=int, default=2000)
    ap.add_argument('--only')
    ap.add_argument('--json')
    ap.add_argument('--compare')
    args = ap.parse_args()
    runner = Runner(args)
    for username in args.users or ['tprof001', 'admin']:
        run_for(runner, username)
    if args.json:
        with open(args.json, 'w') as fh:
            json.dump(runner.rows, fh, indent=1)
    if args.compare:
        with open(args.compare) as fh:
            before = {(r['who'], r['label']): r for r in json.load(fh)}
        print('\n| who | page | queries before → after | ms before → after | KB before → after |')
        print('|---|---|---|---|---|')
        for r in runner.rows:
            b = before.get((r['who'], r['label']))
            if b:
                print('| %s | %s | %d → %d | %d → %d | %.0f → %.0f |' % (
                    r['who'], r['label'], b['queries'], r['queries'], b['ms'], r['ms'], b['kb'], r['kb']))


if __name__ == '__main__':
    main()
