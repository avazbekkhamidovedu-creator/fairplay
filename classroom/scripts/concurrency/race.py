"""Concurrent teacher-write race harness (docs/classroom/reports/teacher-concurrency.md).

usage: RACE_BASE=http://127.0.0.1:8011 RACE_DB=classroom_t N=16 python race.py [scenario ...]

Needs a running multi-threaded server (gunicorn gthread) on a seeded dev database
(seed_dev_data plus instructors t01..t30, see the report) and the ``mariadb`` CLI
for the data checks. Each scenario fires N requests from separately logged-in
teachers at the same instant (threading.Barrier) and prints the status-code
histogram plus a data-consistency check. A 5xx or a failed check is a hazard.
"""
import collections
import json
import os
import re
import sys
import threading
import time
import uuid

import requests

BASE = os.environ.get('RACE_BASE', 'http://127.0.0.1:8011')
PASSWORD = 'fairplay-dev'
N = int(os.environ.get('N', '12'))
_sessions = {}


def login(username):
    if username in _sessions:
        return _sessions[username]
    s = requests.Session()
    r = s.get(BASE + '/accounts/login/')
    token = s.cookies.get('csrftoken')
    r = s.post(BASE + '/accounts/login/',
               data={'username': username, 'password': PASSWORD, 'csrfmiddlewaretoken': token},
               headers={'Referer': BASE + '/accounts/login/'}, allow_redirects=False)
    assert r.status_code in (302, 303), (username, r.status_code, r.text[:300])
    s.headers['X-CSRFToken'] = s.cookies.get('csrftoken')
    s.headers['Referer'] = BASE + '/'
    _sessions[username] = s
    return s


def teachers(n=N):
    return ['t%02d' % (i + 1) for i in range(n)]


def fire(fns):
    """Run callables simultaneously; return list of (status, body-or-exc)."""
    barrier = threading.Barrier(len(fns))
    results = [None] * len(fns)

    def run(i, fn):
        barrier.wait()
        try:
            try:
                r = fn()
            except requests.ConnectionError:
                # A keep-alive connection the server already closed (gunicorn keepalive=2s)
                # while the other teachers were logging in: a browser just reconnects.
                r = fn()
            body = r.text
            try:
                body = r.json()
            except ValueError:
                pass
            results[i] = (r.status_code, body)
        except Exception as e:  # noqa: BLE001
            results[i] = ('EXC', repr(e))

    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def report(name, results, extra_ok=True, note=''):
    hist = collections.Counter(r[0] for r in results)
    bad = [r for r in results if r[0] == 'EXC' or (isinstance(r[0], int) and r[0] >= 500)]
    errors = collections.Counter(
        (r[1].get('error') if isinstance(r[1], dict) else 'non-JSON body') for r in results
        if isinstance(r[0], int) and 400 <= r[0] < 500)
    verdict = 'OK' if not bad and extra_ok else 'HAZARD'
    print('%-28s %-6s %s %s' % (name, verdict, dict(hist), note))
    for msg, count in errors.items():
        print('    %dx %s' % (count, (msg or '')[:160]))
    for status, body in bad[:2]:
        text = body if isinstance(body, str) else json.dumps(body)
        m = re.search(r'<title>(.*?)</title>', text, re.S)
        exc = re.search(r'exception_value">(.*?)<', text, re.S)
        print('    sample %s: %s | %s' % (
            status, m.group(1).strip() if m else text[:200], exc.group(1).strip()[:200] if exc else ''))
    return verdict


def mcq_payload(name, **extra):
    data = {
        'action': 'save', 'type': 'single', 'name': name, 'points': '1', 'is_public': False,
        'description': 'Race question', 'difficulty': 'easy', 'problemTypes': ['Loops'],
        'options': [{'text': 'A', 'correct': True}, {'text': 'B', 'correct': False}],
    }
    data.update(extra)
    return data


def suggested_code(s):
    return re.search(r'id="f-code" value="([a-z0-9]*)"', s.get(BASE + '/problems/new/').text).group(1)


# ----------------------------------------------------------------------------- scenarios
def s_problem_same_code():
    """Teachers who opened the add page at the same time share the suggested code."""
    users = teachers()
    sessions = [login(u) for u in users]
    code = suggested_code(sessions[0])
    tag = uuid.uuid4().hex[:6]
    fns = [lambda s=s, i=i: s.post(BASE + '/problems/new/', data=json.dumps(
        mcq_payload('race %s %d' % (tag, i), code=code, code_auto=True)), headers={'Content-Type': 'application/json'})
        for i, s in enumerate(sessions)]
    res = fire(fns)
    created = [r for r in res if r[0] == 200 and isinstance(r[1], dict) and r[1].get('ok')]
    codes = [r[1]['code'] for r in created]
    return report('problem create same code', res, extra_ok=len(set(codes)) == len(users),
                  note='created=%d distinct=%d' % (len(created), len(set(codes))))


def s_problem_no_code():
    """API clients that send no code: the server picks the first free p###### for all of them."""
    sessions = [login(u) for u in teachers()]
    tag = uuid.uuid4().hex[:6]
    fns = [lambda s=s, i=i: s.post(BASE + '/problems/new/', data=json.dumps(mcq_payload('nocode %s %d' % (tag, i))),
                                   headers={'Content-Type': 'application/json'}) for i, s in enumerate(sessions)]
    res = fire(fns)
    ok = [r for r in res if r[0] == 200]
    return report('problem create no code', res, extra_ok=len(ok) == len(sessions))


def s_problem_new_tag():
    """Everyone tags their new question with the same brand-new label."""
    sessions = [login(u) for u in teachers()]
    tag = 'Race Tag %s' % uuid.uuid4().hex[:6]
    fns = [lambda s=s, i=i: s.post(BASE + '/problems/new/', data=json.dumps(
        mcq_payload('tag %s %d' % (tag, i), problemTypes=[tag], code='', code_auto=True)),
        headers={'Content-Type': 'application/json'}) for i, s in enumerate(sessions)]
    res = fire(fns)
    return report('problem create new tag', res, extra_ok=all(r[0] == 200 for r in res))


def s_problem_type_view():
    sessions = [login(u) for u in teachers()]
    name = 'rt%s' % uuid.uuid4().hex[:6]
    fns = [lambda s=s: s.post(BASE + '/problems/new/problem-type/', data=json.dumps({'name': name, 'full_name': name}),
                              headers={'Content-Type': 'application/json'}) for s in sessions]
    res = fire(fns)
    return report('add tag (type view)', res)


def s_problem_group_view():
    sessions = [login(u) for u in teachers()]
    name = 'Group %s' % uuid.uuid4().hex[:6]
    fns = [lambda s=s: s.post(BASE + '/problems/new/problem-group/', data=json.dumps({'full_name': name}),
                              headers={'Content-Type': 'application/json'}) for s in sessions]
    res = fire(fns)
    return report('add group (group view)', res)


def _create_problem(s, name):
    r = s.post(BASE + '/problems/new/', data=json.dumps(mcq_payload(name, code='', code_auto=True)),
               headers={'Content-Type': 'application/json'})
    assert r.status_code == 200, r.text[:300]
    return r.json()['code']


def s_problem_edit_same():
    """Two tabs/teachers save the same question at the same time."""
    users = teachers()
    sessions = [login(u) for u in users]
    code = _create_problem(sessions[0], 'edit race %s' % uuid.uuid4().hex[:6])
    # Make every teacher an author so all may edit.
    fns = [lambda s=s, i=i: s.post(BASE + '/problems/edit/%s/' % code, data=json.dumps(
        mcq_payload('edited by %d' % i, edit_code=code, options=[
            {'text': 'opt %d-%d' % (i, k), 'correct': k == 0} for k in range(2 + i % 3)])),
        headers={'Content-Type': 'application/json'}) for i, s in enumerate(sessions[:1] * N)]
    res = fire(fns)
    return report('problem edit same (1 user)', res)


def _contest_payload(name, **extra):
    data = {'name': name, 'key_auto': True, 'key': '', 'format_name': 'default',
            'start_time': '2026-10-01T00:00:00Z', 'end_time': '2026-12-31T00:00:00Z'}
    data.update(extra)
    return data


def s_contest_create():
    sessions = [login(u) for u in teachers()]
    tag = uuid.uuid4().hex[:6]
    fns = [lambda s=s, i=i: s.post(BASE + '/contests/create/', data=json.dumps(_contest_payload('c %s %d' % (tag, i))),
                                   headers={'Content-Type': 'application/json'}) for i, s in enumerate(sessions)]
    res = fire(fns)
    return report('contest create auto key', res, extra_ok=all(r[0] == 200 for r in res))


def _create_contest(s, name, **extra):
    r = s.post(BASE + '/contests/create/', data=json.dumps(_contest_payload(name, **extra)),
               headers={'Content-Type': 'application/json'})
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    return body.get('key') or re.search(r'/contest/([a-z0-9]+)', body.get('redirect', '')).group(1)


def s_contest_edit_same():
    """Two tabs of one teacher save the same assignment, both adding questions."""
    s = login('t01')
    key = _create_contest(s, 'edit race %s' % uuid.uuid4().hex[:6])
    pids = [_problem_id(s, _create_problem(s, 'cp %d' % i)) for i in range(3)]
    fns = [lambda i=i: s.post(BASE + '/contests/%s/edit/' % key, data=json.dumps(
        _contest_payload('edit race tab %d' % i, key=key, key_auto=False,
                         problems=[{'id': pid, 'points': 1} for pid in pids[: 1 + i % 3]])),
        headers={'Content-Type': 'application/json'}) for i in range(N)]
    res = fire(fns)
    return report('contest edit same', res, extra_ok=all(r[0] == 200 for r in res))


def _problem_id(s, code):
    return int(sql("select id from judge_problem where code='%s'" % code)[0][0])


def s_class_create():
    """Teachers add a section for the same term at once; the generated class name is the same."""
    sessions = [login(u) for u in teachers()]
    who = 'Prof %s' % uuid.uuid4().hex[:6]
    url = BASE + '/organization/2-cs101/class/create'

    def go(s):
        return s.post(url, data={'name': '', 'academic_year': '2026-2027', 'term_session': 'Fall - Session 1',
                                 'instructor_names': who, 'description': '', 'is_active': 'on',
                                 'csrfmiddlewaretoken': s.cookies.get('csrftoken')},
                      headers={'Referer': url}, allow_redirects=False)
    res = fire([lambda s=s: go(s) for s in sessions])
    return report('class create same name', res, extra_ok=all(r[0] == 302 for r in res))


def sql(query):
    import subprocess
    out = subprocess.run(['mariadb', '-uroot', os.environ.get('RACE_DB', 'classroom_t'), '-N', '-B', '-e', query],
                         capture_output=True, text=True, check=True).stdout
    return [line.split('\t') for line in out.strip().splitlines() if line]


def s_grade_same_sub():
    """Prof and TA (or a double click) save a grade for the same ungraded submission at once."""
    s = login('prof_li')
    sub = sql("select s.id from judge_submission s join judge_contest c on c.id=s.contest_object_id "
              "left join judge_submissionmanualgrade g on g.submission_id=s.id "
              "where c.`key`='cs101quiz1' and g.id is null limit 1")
    if not sub:
        sql("delete g from judge_submissionmanualgrade g join judge_submission s on s.id=g.submission_id "
            "join judge_contest c on c.id=s.contest_object_id where c.`key`='cs101quiz1'")
        return s_grade_same_sub()
    sid = sub[0][0]
    url = BASE + '/contest/cs101quiz1/grade/save'
    res = fire([lambda i=i: s.post(url, data={'submission': sid, 'score': str(i % 3)}) for i in range(N)])
    rows = sql('select count(*) from judge_submissionmanualgrade where submission_id=%s' % sid)
    return report('grade save same submission', res, extra_ok=rows[0][0] == '1', note='grade rows=%s' % rows[0][0])


def s_grade_vs_release():
    """Teacher keeps grading while a colleague clicks Release All."""
    s = login('prof_li')
    subs = [r[0] for r in sql("select s.id from judge_submission s join judge_contest c on c.id=s.contest_object_id "
                              "join judge_contestsubmission cs on cs.submission_id=s.id "
                              "where c.`key`='cs101quiz1'")]
    save = BASE + '/contest/cs101quiz1/grade/save'
    release = BASE + '/contest/cs101quiz1/grade/release'
    bad_total = 0
    all_res = []
    for rnd in range(int(os.environ.get('ROUNDS', '8'))):
        # fresh, unreleased grades each round
        sql("update judge_submissionmanualgrade g join judge_submission s on s.id=g.submission_id "
            "join judge_contest c on c.id=s.contest_object_id set g.released=0 where c.`key`='cs101quiz1'")
        fns = [lambda sid=sid, rnd=rnd: s.post(save, data={'submission': sid,
                                                           'score': str((rnd + int(sid)) % 4 + 0.5)})
               for sid in subs]
        fns.insert(len(fns) // 2, lambda: s.post(release))
        res = fire(fns)
        all_res += res
        rows = sql("select g.submission_id, g.points, cs.points from judge_submissionmanualgrade g "
                   "join judge_contestsubmission cs on cs.submission_id=g.submission_id "
                   "join judge_submission s on s.id=g.submission_id join judge_contest c on c.id=s.contest_object_id "
                   "where c.`key`='cs101quiz1' and g.released=1")
        bad = [r for r in rows if abs(float(r[1]) - float(r[2])) > 1e-6]
        bad_total += len(bad)
    return report('grade save vs release all', all_res, extra_ok=bad_total == 0,
                  note='released grades whose ranking score disagrees (all rounds)=%d' % bad_total)


def _form_value(html, field):
    m = (re.search(r'name="%s"[^>]*value="([a-z0-9]+)"' % field, html)
         or re.search(r'value="([a-z0-9]+)"[^>]*name="%s"' % field, html))
    return m.group(1) if m else None


def s_contest_clone():
    s0 = login('admin')
    key = 'cs101hw1'
    url = BASE + '/contest/%s/clone' % key
    suggested = _form_value(s0.get(url).text, 'key')

    def go():
        return s0.post(url, data={'key': suggested, 'csrfmiddlewaretoken': s0.cookies.get('csrftoken')},
                       headers={'Referer': url}, allow_redirects=False)
    res = fire([go for _ in range(N)])
    return report('contest clone same key', res, note='suggested=%s' % suggested)


def s_problem_clone():
    s0 = login('admin')
    code = _create_problem(s0, 'clone src %s' % uuid.uuid4().hex[:6])
    url = BASE + '/problem/%s/clone' % code
    suggested = _form_value(s0.get(url).text, 'code')

    def go():
        return s0.post(url, data={'code': suggested, 'csrfmiddlewaretoken': s0.cookies.get('csrftoken')},
                       headers={'Referer': url}, allow_redirects=False)
    res = fire([go for _ in range(N)])
    return report('problem clone same code', res, note='suggested=%s' % suggested)


def s_join_double():
    """A student double-clicks Join (two tabs)."""
    s = login('ab105')
    sql("update judge_profile pr join auth_user u on u.id=pr.user_id set pr.current_contest_id=NULL "
        "where u.username='ab105'")
    sql("delete p from judge_contestparticipation p join judge_contest c on c.id=p.contest_id "
        "join judge_profile pr on pr.id=p.user_id join auth_user u on u.id=pr.user_id "
        "where c.`key`='cs101hw1' and u.username='ab105'")
    url = BASE + '/contest/cs101hw1/join'
    res = fire([lambda: s.post(url, data={'csrfmiddlewaretoken': s.cookies.get('csrftoken')},
                               headers={'Referer': url}, allow_redirects=False) for _ in range(N)])
    return report('contest join double click', res)


SCENARIOS = {name[2:]: fn for name, fn in globals().items() if name.startswith('s_')}

if __name__ == '__main__':
    names = sys.argv[1:] or list(SCENARIOS)
    for name in names:
        t = time.time()
        try:
            SCENARIOS[name]()
        except Exception as e:  # noqa: BLE001
            print('%-28s ERROR %r' % (name, e))
        print('    (%.1fs)' % (time.time() - t))
