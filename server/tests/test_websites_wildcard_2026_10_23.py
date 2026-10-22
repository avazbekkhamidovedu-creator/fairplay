"""2026-10-23 (review): allowed websites are exact ("docs.python.org") or a wildcard for a
whole site ("*.python.org"); a wildcard on a public suffix or shared hosting is refused."""
import pytest

from app.schemas import SHARED_SUFFIXES, is_shared_suffix, normalize_website, normalize_websites

from .conftest import SAMPLE_EXAM


@pytest.mark.parametrize("given,want", [
    ("*.python.org", "*.python.org"),
    ("*.Python.ORG.", "*.python.org"),
    ("https://*.cppreference.com/", "*.cppreference.com"),
    ("*.dku.edu.cn", "*.dku.edu.cn"),
    ("*.example.com.au", "*.example.com.au"),
    ("docs.python.org", "docs.python.org"),
    ("www.wolfram.com", "www.wolfram.com"),
])
def test_wildcard_accepted(given, want):
    assert normalize_website(given) == want


@pytest.mark.parametrize("bad", [
    "*.com", "*.org", "*.io", "*.github.io", "*.vercel.app", "*.netlify.app", "*.pages.dev", "*.web.app",
    "*.co.uk", "*.com.cn", "*.blogspot.com", "*.herokuapp.com", "*.appspot.com", "*.com.au", "*.edu.uz",
    "*.localhost", "*", "*.", "**.python.org", "*.*.python.org", "docs.*.org", "py*.org", "*python.org",
    "*.10.0.0.1", "*.python.org/3/", "*.python.org:443",
])
def test_wildcard_refused(bad):
    with pytest.raises(ValueError):
        normalize_website(bad)


def test_shared_suffix_rule():
    assert {"com", "github.io", "vercel.app", "co.uk", "com.cn"} <= SHARED_SUFFIXES
    assert is_shared_suffix("com") and is_shared_suffix("org.au") and is_shared_suffix("ac.kr")
    assert not is_shared_suffix("python.org") and not is_shared_suffix("dku.edu.cn")


def test_exact_and_wildcard_are_different_entries():
    assert normalize_websites(["python.org", "*.python.org", "*.PYTHON.org"]) == ["python.org", "*.python.org"]


def test_wildcards_through_the_api(client, teacher):
    r = client.post("/api/exams", json={**SAMPLE_EXAM, "websites": ["*.Python.org", "docs.python.org"]}, headers=teacher)
    assert r.status_code == 201, r.text
    assert r.json()["websites"] == ["*.python.org", "docs.python.org"]
    r = client.post("/api/exams", json={**SAMPLE_EXAM, "websites": ["*.github.io"]}, headers=teacher)
    assert r.status_code == 422
