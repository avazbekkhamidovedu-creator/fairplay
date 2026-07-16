"""Server-authoritative markdown fixtures shared with resources/tests (RTE)."""
import json
import os

from django.test import SimpleTestCase

from judge.jinja2.markdown import markdown


FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__),
    '..', '..', '..', 'resources', 'tests', 'fixtures', 'rte_markdown.json',
)
PREVIEW_TEMPLATE = os.path.join(
    os.path.dirname(__file__),
    '..', '..', '..', 'templates', 'problem', 'preview.html',
)


def _load_cases():
    with open(os.path.abspath(FIXTURE_PATH), encoding='utf-8') as f:
        return json.load(f)['cases']


class RteMarkdownFixtureTest(SimpleTestCase):
    """Same Markdown fixtures as JS tests, rendered via mistune+bleach (problem style)."""

    def test_fixtures_via_markdown(self):
        for case in _load_cases():
            with self.subTest(case=case['id']):
                html = str(markdown(case['markdown'], 'problem'))
                for needle in case.get('expect_substrings') or []:
                    self.assertIn(needle, html, msg=f'{case["id"]}: missing {needle!r} in {html!r}')
                for needle in case.get('forbid_substrings') or []:
                    self.assertNotIn(needle, html, msg=f'{case["id"]}: forbidden {needle!r} in {html!r}')


class ProblemPreviewTemplateContractTest(SimpleTestCase):
    """create_v2 server preview posts to problem_preview → this template → markdown('problem')."""

    def test_preview_template_uses_problem_markdown_style(self):
        with open(os.path.abspath(PREVIEW_TEMPLATE), encoding='utf-8') as f:
            source = f.read()
        self.assertIn("markdown('problem'", source)
