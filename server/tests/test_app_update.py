"""Pilot 0.8.8: GET /api/app/latest (app/app_update.py)."""
from app import app_update
from app.store import get_store

from .test_app_session import app_client  # noqa: F401  (fixture)

REPO = "avazbekkhamidovedu-creator/fairplay-download"
SHA = "ab12" * 16


def release(tag="v0.8.8", name="FairPlay-Mac.zip", digest="sha256:" + SHA, draft=False, url=None):
    return {"tag_name": tag, "draft": draft, "prerelease": False, "assets": [
        {"name": name, "state": "uploaded", "digest": digest,
         "browser_download_url": url or f"https://github.com/{REPO}/releases/download/{tag}/{name}"}]}


def test_parse_release_rules():
    ok = app_update.parse_release(release(), REPO, "mac")
    assert ok == {"version": "0.8.8", "url": f"https://github.com/{REPO}/releases/download/v0.8.8/FairPlay-Mac.zip",
                  "sha256": SHA}
    assert app_update.parse_release(release(digest=None), REPO, "mac") is None
    assert app_update.parse_release(release(digest="sha256:abc"), REPO, "mac") is None
    assert app_update.parse_release(release(draft=True), REPO, "mac") is None
    assert app_update.parse_release(release(tag="nightly"), REPO, "mac") is None
    assert app_update.parse_release(release(url="https://evil.example/FairPlay-Mac.zip"), REPO, "mac") is None
    assert app_update.parse_release(release(), REPO, "windows") is None      # no windows asset


def fetcher(calls, result):
    async def fetch(settings):
        calls.append(1)
        return result() if callable(result) else result
    return fetch


def test_endpoint_caches_github_and_answers_304(app_client):
    s = app_client.app.state.settings
    calls = []
    s.app_update_github = REPO
    gh = {"mac": app_update.parse_release(release(), REPO, "mac"), "windows": None}
    app_client.app.state.app_update_fetch = fetcher(calls, gh)
    s.min_app_version_mac = "0.8.7"
    r = app_client.get("/api/app/latest?platform=mac")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["version"] == "0.8.8" and b["sha256"] == SHA and b["min_version"] == "0.8.7"
    assert b["platform"] == "mac" and b["url"].endswith("/v0.8.8/FairPlay-Mac.zip") and b["checked_at"].endswith("Z")
    etag = r.headers["etag"]
    assert "max-age" in r.headers["cache-control"]
    r2 = app_client.get("/api/app/latest?platform=mac", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    assert len(calls) == 1                      # the second request came from the cache
    assert app_client.get("/api/app/latest?platform=windows").status_code == 404   # nothing for windows
    assert app_client.get("/api/app/latest?platform=ipad").status_code == 404


def test_env_overrides_come_first(app_client):
    s = app_client.app.state.settings
    calls = []
    s.app_update_github = REPO
    app_client.app.state.app_update_fetch = fetcher(calls, {"mac": app_update.parse_release(release(), REPO, "mac")})
    s.latest_app_version_mac, s.app_update_url_mac, s.app_update_sha256_mac = "0.9.0", "https://x.example/m.zip", "cd" * 32
    b = app_client.get("/api/app/latest?platform=mac").json()
    assert (b["version"], b["url"], b["sha256"]) == ("0.9.0", "https://x.example/m.zip", "cd" * 32)
    # a pinned version without a pinned file does not borrow another release's file
    s.app_update_url_mac = s.app_update_sha256_mac = ""
    b = app_client.get("/api/app/latest?platform=mac").json()
    assert b["version"] == "0.9.0" and b["url"] == "" and b["sha256"] == ""


def test_github_down_keeps_last_good_copy_and_empty_is_404(app_client, monkeypatch):
    s = app_client.app.state.settings
    assert app_client.get("/api/app/latest?platform=mac").status_code == 404      # GitHub off, nothing pinned
    s.app_update_github = REPO
    app_client.app.state.app_update_fetch = fetcher([], None)
    assert app_client.get("/api/app/latest?platform=mac").status_code == 404
    monkeypatch.setattr(app_update, "REFRESH_S", 0)

    def refresh_window_over():          # what ten minutes passing does
        app_client.portal.call(get_store().delete, app_update.TRY_KEY)
    gh = {"mac": app_update.parse_release(release(), REPO, "mac")}
    app_client.app.state.app_update_fetch = fetcher([], gh)
    refresh_window_over()
    assert app_client.get("/api/app/latest?platform=mac").json()["version"] == "0.8.8"
    app_client.app.state.app_update_fetch = fetcher([], None)      # GitHub down now
    refresh_window_over()
    assert app_client.get("/api/app/latest?platform=mac").json()["version"] == "0.8.8"
    s.app_update_github = "off"
    assert app_client.get("/api/app/latest?platform=mac").json()["version"] == "0.8.8"
