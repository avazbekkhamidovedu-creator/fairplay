"""2026-10-23 (review): GET /api/tools/site-check tells the teacher whether an allowed
website can be shown inside the exam's websites pane (httpx mocked, no network)."""
import httpx
import pytest

from app import site_check
from .conftest import login


@pytest.fixture
def fake_web():
    seen: list[str] = []
    pages: dict[str, httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        r = pages.get(str(request.url))
        if r is None:
            raise httpx.ConnectError("no such site", request=request)
        return r

    site_check.set_transport_factory(lambda: httpx.MockTransport(handler))
    yield pages, seen
    site_check.set_transport_factory(None)


def check(client, h, host):
    return client.get("/api/tools/site-check", params={"host": host}, headers=h)


def test_frameable_and_refusing_sites(client, teacher, fake_web):
    pages, _ = fake_web
    pages["https://docs.python.org/"] = httpx.Response(200, text="<html>")
    pages["https://github.com/"] = httpx.Response(200, headers={"X-Frame-Options": "deny"})
    pages["https://example.org/"] = httpx.Response(
        200, headers={"Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'"})
    pages["https://open.example.net/"] = httpx.Response(
        200, headers={"Content-Security-Policy": "frame-ancestors *"})
    r = check(client, teacher, "docs.python.org")
    assert r.status_code == 200, r.text
    assert r.json()["frameable"] is True
    gh = check(client, teacher, "github.com").json()
    assert gh["frameable"] is False and "X-Frame-Options" in gh["reason"]
    ex = check(client, teacher, "example.org").json()
    assert ex["frameable"] is False and "frame-ancestors" in ex["reason"]
    assert check(client, teacher, "open.example.net").json()["frameable"] is True


def test_wildcard_checks_the_base_and_redirects_are_limited(client, teacher, fake_web):
    pages, seen = fake_web
    pages["https://python.org/"] = httpx.Response(301, headers={"Location": "https://www.python.org/"})
    pages["https://www.python.org/"] = httpx.Response(200)
    assert check(client, teacher, "*.python.org").json()["frameable"] is True
    assert seen[:2] == ["https://python.org/", "https://www.python.org/"]
    # a redirect loop stops after MAX_REDIRECTS hops: unknown, not frameable
    pages["https://loop.example.com/"] = httpx.Response(302, headers={"Location": "https://loop.example.com/"})
    before = len(seen)
    out = check(client, teacher, "loop.example.com").json()
    assert out["frameable"] is None
    assert len(seen) - before == site_check.MAX_REDIRECTS + 1
    # leaving the allowed site: the apps would block it, so it can't be shown
    pages["https://short.example.com/"] = httpx.Response(302, headers={"Location": "https://other.example.org/x"})
    off = check(client, teacher, "short.example.com").json()
    assert off["frameable"] is False and "other.example.org" in off["reason"]


def test_unreachable_is_unknown(client, teacher, fake_web):
    out = check(client, teacher, "nowhere.example.com").json()
    assert out["frameable"] is None and out["reason"]


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.5", "localhost", "foo.localhost", "[::1]",
                                  "printer.local", "http://169.254.169.254/", "com"])
def test_addresses_and_private_names_are_refused(client, teacher, fake_web, host):
    _, seen = fake_web
    r = check(client, teacher, host)
    assert r.status_code == 422, r.text
    assert seen == []


def test_private_resolution_is_refused_by_the_real_transport(client, teacher, monkeypatch):
    """No mock transport: the guarded backend refuses a name that resolves to loopback."""
    import asyncio
    import socket

    async def getaddrinfo(self, host, port, type=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", getaddrinfo)
    site_check._cache.clear()
    out = check(client, teacher, "rebind.example.com").json()
    assert out["frameable"] is None and "private network" in out["reason"]


def test_teacher_only_and_rate_limited(client, teacher, fake_web):
    pages, _ = fake_web
    pages["https://docs.python.org/"] = httpx.Response(200)
    assert check(client, login(client, "alice"), "docs.python.org").status_code == 403
    assert client.get("/api/tools/site-check", params={"host": "docs.python.org"}).status_code == 401
    codes = [check(client, teacher, "docs.python.org").status_code for _ in range(site_check.CHECKS_PER_MIN + 1)]
    assert codes[-1] == 429 and codes[0] == 200
