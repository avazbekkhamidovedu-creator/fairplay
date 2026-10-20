"""The navigation bar is read from the cache: no queries for it after the first page, the
active tab still follows the item regexes, and editing the bar shows up right away."""
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from judge.models import NavigationBar


class NavigationBarCacheTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.problems = NavigationBar.objects.create(order=1, key='problems', label='Problems', path='/problems/',
                                                    regex=r'^/problem')
        cls.contests = NavigationBar.objects.create(order=2, key='contests', label='Contests', path='/contests/',
                                                    regex=r'^/contest')
        cls.sub = NavigationBar.objects.create(order=1, key='calendar', label='Calendar', path='/contests/calendar/',
                                               regex=r'^/contests/calendar', parent=cls.contests)

    def setUp(self):
        cache.clear()

    def nav_queries(self, url):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        return response, [q for q in ctx.captured_queries if 'judge_navigationbar' in q['sql']]

    def test_second_page_reads_no_navigation_rows(self):
        self.nav_queries('/problems/')
        response, queries = self.nav_queries('/problems/')
        self.assertEqual(queries, [])
        self.assertContains(response, 'class="nav-problems active"')
        self.assertContains(response, 'class="nav-contests"')

    def test_active_tab_includes_the_parent(self):
        from judge.template_context import _nav_tab_keys
        self.assertEqual(_nav_tab_keys('/contests/calendar/2026/10/'), ['contests'])  # parent comes first in tree order
        self.sub.regex = r'^/contests/calendar'
        self.contests.regex = r'^/contests/$'
        self.contests.save()
        self.sub.save()
        self.assertEqual(_nav_tab_keys('/contests/calendar/2026/10/'), ['contests', 'calendar'])
        self.assertEqual(_nav_tab_keys('/nowhere/'), [])

    def test_changes_show_up_immediately(self):
        self.nav_queries('/problems/')
        NavigationBar.objects.create(order=3, key='about', label='About us', path='/about/', regex=r'^/about')
        response, _ = self.nav_queries('/problems/')
        self.assertContains(response, 'About us')
        self.contests.label = 'Coursework Z'
        self.contests.save()
        response, _ = self.nav_queries('/problems/')
        self.assertContains(response, 'Coursework Z')
