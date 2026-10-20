"""Locust scenario: hundreds of teachers working at once (docs/classroom/reports/scaling.md).

Seed first: ``python scripts/loadtest/seed_teacher_load.py --teachers N --reset`` (writes
teacher_load.json next to this file). Each simulated teacher logs in as one of ltt001..lttNNN and
loops over a teacher's day, with think time between steps:

* browse: assignments list, question bank (with a search), own course page, homework page,
  submissions of the homework, grading page (+ save a grade), analytics;
* author: create a multiple-choice question in the editor, edit it twice ("Save and continue"),
  create an assignment with it (editor JSON), edit the assignment;

Every response is checked: a 5xx, a non-JSON or ``ok: false`` answer to a save is a failure.
Static files are not requested (nginx serves them in production).

Env: LT_TEACHER_MAP (default ./teacher_load.json), LT_THINK_MIN/LT_THINK_MAX seconds (default 2/6).
"""
import itertools
import json
import os
import random
import re
import threading
import time

from locust import FastHttpUser, between, task

HERE = os.path.dirname(os.path.abspath(__file__))
MAP = json.load(open(os.environ.get('LT_TEACHER_MAP', os.path.join(HERE, 'teacher_load.json'))))
PASSWORD = MAP['password']
TAG = MAP['tag']
TEACHERS = sorted(MAP['teachers'])
THINK_MIN = float(os.environ.get('LT_THINK_MIN', '2'))
THINK_MAX = float(os.environ.get('LT_THINK_MAX', '6'))

_ids = itertools.count()
_lock = threading.Lock()
CSRF_RE = re.compile(r"name=['\"]csrfmiddlewaretoken['\"] value=['\"]([^'\"]+)")
SUBS_RE = re.compile(r'var submissions = (\[.*?\]);\n')
ID_RE = re.compile(r'"id": (\d+)')


def next_teacher():
    with _lock:
        return TEACHERS[next(_ids) % len(TEACHERS)]


class Teacher(FastHttpUser):
    wait_time = between(THINK_MIN, THINK_MAX)

    def csrf(self):
        for cookie in self.client.cookiejar:
            if cookie.name == 'csrftoken':
                return cookie.value
        return ''

    def post(self, url, data, name, json_body=False, **kw):
        headers = {'X-CSRFToken': self.csrf(), 'Referer': self.host + url}
        if json_body:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(data)
        return self.client.post(url, data=data, headers=headers, name=name, catch_response=True, **kw)

    def get(self, url, name=None):
        with self.client.get(url, name=name or url, catch_response=True) as r:
            if r.status_code != 200:
                r.failure('HTTP %s' % r.status_code)
            return r

    def on_start(self):
        self.username = next_teacher()
        self.info = MAP['teachers'][self.username]
        self.my_questions = []
        self.my_assignments = []
        self.client.cookiejar.clear()
        r = self.client.get('/accounts/login/', name='GET /accounts/login/')
        token = CSRF_RE.search(r.text or '').group(1)
        with self.post('/accounts/login/', {'csrfmiddlewaretoken': token, 'username': self.username,
                                            'password': PASSWORD},
                       name='POST /accounts/login/', allow_redirects=False) as r:
            if r.status_code != 302:
                r.failure('login failed: HTTP %s' % r.status_code)

    def check_json(self, r, ok_statuses=(200,)):
        if r.status_code not in ok_statuses:
            r.failure('HTTP %s %s' % (r.status_code, (r.text or '')[:200]))
            return None
        try:
            data = json.loads(r.text)
        except ValueError:
            r.failure('not JSON')
            return None
        if r.status_code == 200 and data.get('ok') is False:
            r.failure('ok=false %s' % (r.text or '')[:200])
        return data

    # ------------------------------------------------------------------ browsing
    @task(6)
    def browse(self):
        hw = self.info['homework']
        self.get('/contests/', 'GET /contests/')
        self.get('/problems/?search=Question', 'GET /problems/?search')
        self.get('/organization/%d' % self.info['course'], 'GET /organization/[id]')
        self.get('/contest/%s' % hw, 'GET /contest/[key]')
        self.get('/submissions/?contest=%s' % hw, 'GET /submissions/?contest')

    @task(3)
    def grade(self):
        hw = self.info['homework']
        r = self.get('/contest/%s/grade' % hw, 'GET /contest/[key]/grade')
        m = SUBS_RE.search(r.text or '')
        ids = ID_RE.findall(m.group(1)) if m else []
        if ids:
            with self.post('/contest/%s/grade/save' % hw, {'submission': random.choice(ids),
                                                           'score': random.randint(0, 5), 'note': 'ok'},
                           name='POST grade/save') as resp:
                self.check_json(resp)

    @task(1)
    def analytics(self):
        self.get('/contest/%s/analytics' % self.info['homework'], 'GET /contest/[key]/analytics')

    # ------------------------------------------------------------------ authoring
    def question_payload(self, name, code=None):
        payload = {'type': 'single', 'name': name, 'description': 'Which option is right? (%s)' % name,
                   'points': 5, 'options': [{'text': 'Right', 'correct': True}, {'text': 'Wrong'},
                                            {'text': 'Also wrong'}],
                   'organizations': [str(self.info['course'])], 'problemTypes': [TAG], 'difficulty': 'easy'}
        if code:
            payload['edit_code'] = code
        return payload

    @task(4)
    def author_question(self):
        self.get('/problems/new/', 'GET /problems/new/')
        name = 'LT %s %d' % (self.username, int(time.time() * 1000) % 10 ** 9)
        with self.post('/problems/new/', self.question_payload(name), 'POST /problems/new/ (create)',
                       json_body=True) as r:
            data = self.check_json(r)
        if not data or not data.get('code'):
            return
        code = data['code']
        self.my_questions.append((code, data.get('id')))
        for i in range(2):
            self._wait()
            with self.post('/problems/edit/%s/' % code, self.question_payload(name + ' v%d' % (i + 2), code),
                           'POST /problems/edit/[code]/ (save)', json_body=True) as r:
                self.check_json(r, ok_statuses=(200, 409))

    @task(2)
    def author_assignment(self):
        if not self.my_questions:
            return
        self.get('/contests/create/', 'GET /contests/create/')
        problem_ids = [str(pid) for _code, pid in self.my_questions[-3:] if pid]
        if not problem_ids:
            return
        now = time.time()
        body = {
            'name': 'LT assignment %s' % self.username, 'validity_type': 'range',
            'start_time': time.strftime('%Y-%m-%dT%H:%M:00.000Z', time.gmtime(now + 86400)),
            'end_time': time.strftime('%Y-%m-%dT%H:%M:00.000Z', time.gmtime(now + 3 * 86400)),
            'format_name': 'default', 'vis_mode': 'org', 'organizations': [str(self.info['course'])],
            'description': '', 'key': '', 'key_auto': True, 'scoreboard_visibility': 'H',
            'points_precision': '3', 'action': 'continue', 'problem_ids': ','.join(problem_ids),
            'problems': [{'id': pid, 'points': '5', 'partial': True, 'is_pretested': False,
                          'max_submissions': None, 'hide_results': None, 'output_prefix': None,
                          'display_name': ''} for pid in problem_ids],
            'classes': [], 'tags': [], 'curators': [], 'testers': [], 'spectators': [], 'authors': [],
            'private_contestants': [], 'banned_users': [], 'join_organizations': [],
            'view_contest_scoreboard': [], 'view_contest_submissions': [],
        }
        with self.post('/contests/create/', body, 'POST /contests/create/', json_body=True) as r:
            data = self.check_json(r)
        if not data or not data.get('key'):
            return
        key = data['key']
        self.my_assignments.append(key)
        self._wait()
        body.update({'key': key, 'name': body['name'] + ' (edited)'})
        body.pop('key_auto', None)
        with self.post('/contests/%s/edit/' % key, body, 'POST /contests/[key]/edit/', json_body=True) as r:
            self.check_json(r, ok_statuses=(200, 409))
        self.get('/contests/%s/edit/' % key, 'GET /contests/[key]/edit/')

    def _wait(self):
        time.sleep(random.uniform(THINK_MIN / 2, THINK_MAX / 2))
