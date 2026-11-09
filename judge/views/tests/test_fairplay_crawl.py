"""FairPlay hiding rule over every GET route of the site, so a new route that forgets it fails."""
import re

from django.test import Client, TestCase
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from judge.models import ContestParticipation, ContestSubmission, Language, Problem, Submission, SubmissionSource
from judge.views.tests.fairplay_base import FAIRPLAY_ON, FairPlayDataMixin

# Text that only the hidden assignment carries.
MARKERS = (
    'Which structure gives O(1) average lookup?', 'maps keys to values', 'Explain hashing.',
    'Upload your report.', 'Write it.', 'Reverse a list', 'CS 101 Midterm', 'def reverse(head)',
    'print(42)', 'Hash maps',
)
# Routes that are not pages about content (sign-in flows, staff tools, writes reached by GET).
SKIP = re.compile(r'^(admin/|accounts/|login|logout|social/|i18n/|500/|2fa/|impersonate/|password/|email/|'
                  r'api/fairplay/|api/token/|newsletter/|tasks/|mailgun/|activate/|register/|data/)')
PARAM = re.compile(r'<(?:(?P<conv>\w+):)?(?P<name>\w+)>')


def walk(patterns, prefix=''):
    for p in patterns:
        route = getattr(p.pattern, '_route', None)
        if route is None:          # re_path: skip (none of them serves problems)
            continue
        if isinstance(p, URLResolver):
            yield from walk(p.url_patterns, prefix + route)
        elif isinstance(p, URLPattern):
            yield prefix + route


@FAIRPLAY_ON
class FairPlayHidingCrawlTestCase(FairPlayDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Problem.objects.filter(pk=cls.code.pk).update(view_tester=True, view_test_cases=True)
        py3 = Language.objects.get(key='PY3')
        participation = ContestParticipation.objects.create(contest=cls.exam, user=cls.student.profile)
        cls.submission = Submission.objects.create(user=cls.student.profile, problem=cls.code, language=py3,
                                                   contest_object=cls.exam, status='D', result='WA',
                                                   case_points=0, case_total=1, time=0.1, memory=1024)
        SubmissionSource.objects.create(submission=cls.submission, source='print(42)')
        ContestSubmission.objects.create(submission=cls.submission, participation=participation,
                                         problem=cls.exam.contest_problems.get(problem=cls.code))
        cls.student.profile.current_contest = participation
        cls.student.profile.save()

    def values(self, name):
        sub = str(self.submission.id)
        return {
            'problem': ['fpcode', 'fpmcq', 'fpblank', 'fpshort', 'fpfile'], 'contest': ['fpexam'],
            'submission': [sub], 'user': ['fp_student'], 'case': ['1'], 'type': ['in', 'out'],
            'path': ['secret.in'], 'language': ['en'], 'page': ['1'], 'id': [sub], 'pk': [sub, str(self.org.pk)],
            'key': ['fpexam'], 'code': ['fpcode'], 'name': ['fpexam'], 'slug': ['fpexam', self.org.slug],
            'cpk': [str(self.klass.pk)], 'cslug': [self.klass.slug],
            'file_uuid': ['00000000-0000-0000-0000-000000000000'],
        }.get(name, ['1'])

    def urls(self):
        for route in sorted(set(walk(get_resolver().url_patterns))):
            if SKIP.match(route):
                continue
            paths = ['/' + route]
            for match in PARAM.finditer(route):
                values = self.values(match.group('name'))
                paths = [p.replace(match.group(0), value, 1) for p in paths for value in values]
            yield from paths

    def test_no_route_shows_a_hidden_assignment_to_its_student(self):
        queries = ({}, {'problem': 'fpcode', 'contest': 'fpexam', 'id': self.submission.id,
                        'term': 'Reverse', 'q': 'Reverse', 'problem_id': self.code.id, 'language': 'PY3'})
        leaks = []
        for user in (self.student, self.outsider):
            client = Client()
            client.force_login(user)
            for url in self.urls():
                for query in queries:
                    try:
                        response = client.get(url, query)
                    except Exception:   # a route that needs more than a GET: not a page
                        continue
                    if response.status_code != 200:
                        continue
                    body = b''.join(response) if response.streaming else response.content
                    text = body.decode('utf-8', errors='replace')
                    metadata_landing = user.pk == self.student.pk and url in {
                        reverse('contest_view', args=[self.exam.key]),
                        reverse('contest_fairplay_launch', args=[self.exam.key]),
                    }
                    if metadata_landing:
                        self.assertIn('<main class="fp-launch">', text)
                        self.assertIn('no-store', response['Cache-Control'])
                    discovery_list = user.pk == self.student.pk and url in {
                        reverse('contest_list'), reverse('contest_list', args=[1]),
                        reverse('contest_list_legacy'), reverse('contest_list_legacy', args=[1]),
                    }
                    if discovery_list:
                        self.assertIn(reverse('contest_fairplay_launch', args=[self.exam.key]), text)
                    allowed_name = (metadata_landing or discovery_list)
                    found = [m for m in MARKERS if m in text and not (allowed_name and m == self.exam.name)]
                    if found:
                        leaks.append('%s %s?%s -> %s' % (user.username, url, '&'.join(query), found))
        self.maxDiff = None
        self.assertEqual(leaks, [])

    def test_crawl_reaches_the_content_routes(self):
        # Sanity check of the crawler: the routes that serve problems are among those walked.
        urls = set(self.urls())
        for url in ('/problem/fpcode', '/contest/fpexam', '/submission/%d' % self.submission.id,
                    '/src/%d/raw' % self.submission.id, '/api/v2/problem/fpcode', '/contest/fpexam/export-pdf/'):
            self.assertIn(url, urls)
        teacher = Client()
        teacher.force_login(self.teacher)
        self.assertContains(teacher.get('/problem/fpcode'), 'Write it.')
