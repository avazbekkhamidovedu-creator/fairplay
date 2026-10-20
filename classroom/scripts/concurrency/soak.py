"""Sustained mixed teacher load (no barrier): every teacher loops over the authoring flow.

usage: RACE_BASE=http://127.0.0.1:8011 SECONDS_=60 USERS=30 python soak.py   (same setup as race.py)
Per loop: open add page (suggested code), create a question of a random type, open its editor and
save it with edit_base, create an assignment with it, open the assignment editor and save it,
create a class section. Counts every status; any 5xx/exception or unexpected 4xx is a failure.
"""
import collections
import json
import os
import random
import re
import threading
import time
import uuid

import race
import requests

DURATION = float(os.environ.get('SECONDS_', '60'))
USERS = int(os.environ.get('USERS', '30'))
stats = collections.Counter()
failures = []
lock = threading.Lock()

TYPES = {
    'single': {'options': [{'text': 'A', 'correct': True}, {'text': 'B', 'correct': False}]},
    'multiple': {'options': [{'text': 'A', 'correct': True}, {'text': 'B', 'correct': True},
                             {'text': 'C', 'correct': False}]},
    'truefalse': {'tfAnswer': 'true'},
    'blank': {'blanks': [{'answers': 'print', 'points': 1}], 'description': 'Use ___ to output.'},
    'short': {'shortAnswer': 'Because.'},
    'file': {'fileExtensions': 'pdf', 'fileMaxSizeMb': 5},
}


def note(step, r, ok=(200, 302)):
    key = '%s %s' % (step, r.status_code if hasattr(r, 'status_code') else r)
    with lock:
        stats[key] += 1
        if not hasattr(r, 'status_code') or r.status_code not in ok:
            body = r.text[:300] if hasattr(r, 'text') else str(r)
            failures.append((step, getattr(r, 'status_code', 'EXC'), body))


def call(step, fn, ok=(200, 302)):
    for attempt in range(2):
        try:
            r = fn()
            note(step, r, ok)
            return r
        except requests.ConnectionError as e:
            if attempt:
                note(step, 'EXC %r' % e)
    return None


def teacher_loop(username, deadline):
    s = race.login(username)
    json_headers = {'Content-Type': 'application/json'}
    while time.time() < deadline:
        tag = uuid.uuid4().hex[:6]
        page = call('GET add page', lambda: s.get(race.BASE + '/problems/new/'))
        if page is None:
            continue
        m = re.search(r'id="f-code" value="([a-z0-9]*)"', page.text)
        qtype = random.choice(list(TYPES))
        payload = race.mcq_payload('soak %s' % tag, type=qtype, code=m.group(1) if m else '', code_auto=True,
                                   create_token='soak%s%s' % (tag, uuid.uuid4().hex[:8]),
                                   problemTypes=[random.choice(['Loops', 'Soak Tag %d' % random.randrange(5)])])
        payload.update(TYPES[qtype])
        r = call('POST create %s' % qtype, lambda: s.post(race.BASE + '/problems/new/', data=json.dumps(payload),
                                                          headers=json_headers))
        if r is None or r.status_code != 200:
            continue
        code, pid = r.json()['code'], r.json()['id']
        edit_url = race.BASE + '/problems/edit/%s/' % code
        page = call('GET edit page', lambda: s.get(edit_url))
        base = re.search(r"const EDIT_BASE = '([0-9a-f]*)';", page.text).group(1) if page is not None else ''
        payload.update({'name': 'soak edited %s' % tag, 'edit_code': code, 'edit_base': base})
        payload.pop('create_token', None)
        call('POST edit question', lambda: s.post(edit_url, data=json.dumps(payload), headers=json_headers))
        cpayload = race._contest_payload('soak %s' % tag, problems=[{'id': pid, 'points': 5}])
        r = call('POST create assignment', lambda: s.post(race.BASE + '/contests/create/', data=json.dumps(cpayload),
                                                          headers=json_headers))
        if r is None or r.status_code != 200:
            continue
        key = r.json()['key']
        edit = race.BASE + '/contests/%s/edit/' % key
        page = call('GET assignment editor', lambda: s.get(edit))
        m = re.search(r'window.DMOJ_EDIT_BASE = "([0-9a-f]*)";', page.text) if page is not None else None
        cpayload.update({'name': 'soak edited %s' % tag, 'key': key, 'key_auto': False,
                         'edit_base': m.group(1) if m else ''})
        call('POST edit assignment', lambda: s.post(edit, data=json.dumps(cpayload), headers=json_headers))
        if random.random() < 0.3:
            url = race.BASE + '/organization/2-cs101/class/create'
            form = {'name': '', 'academic_year': '2026-2027', 'term_session': 'Spring - Session 1',
                    'instructor_names': 'Soak Prof', 'is_active': 'on',
                    'csrfmiddlewaretoken': s.cookies.get('csrftoken')}
            call('POST create class',
                 lambda: s.post(url, data=form, headers={'Referer': url}, allow_redirects=False), ok=(302,))


if __name__ == '__main__':
    users = race.teachers(USERS)
    for u in users:
        race.login(u)
    deadline = time.time() + DURATION
    threads = [threading.Thread(target=teacher_loop, args=(u, deadline)) for u in users]
    started = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total = sum(stats.values())
    print('%d requests in %.0fs from %d teachers' % (total, time.time() - started, USERS))
    for key in sorted(stats):
        print('  %-36s %d' % (key, stats[key]))
    print('failures: %d' % len(failures))
    for f in failures[:10]:
        print('  ', f[0], f[1], re.sub(r'\s+', ' ', f[2])[:200])
