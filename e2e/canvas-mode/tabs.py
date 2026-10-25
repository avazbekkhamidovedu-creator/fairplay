"""A browser tab with a navigation delegate, in Chromium (Playwright) and WebKit (WebKitGTK host).

The controller (restricted.py) is the delegate: `decide_navigation(url, main_frame)` is answered for every
navigation action including each redirect hop, `on_commit`, `on_url_changed` and `on_did_finish` are queued
and handled when the test calls `pump`.
"""
from __future__ import annotations

import json
import os
import select
import subprocess
import time

import config as C

try:
    from browsers import RESOLVER_RULES
except Exception:  # pragma: no cover
    RESOLVER_RULES = ""


class BaseTab:
    app = None
    decisions: list

    def __init__(self):
        self.decisions = []

    def settle(self, quiet: float = 0.7, timeout: float = 20.0):
        """Pump until the page is loaded, its URL has been still for `quiet` s and the app has nothing queued."""
        deadline = time.time() + timeout
        last, since = None, time.time()
        while time.time() < deadline:
            self.pump(0.1)
            try:
                u, ready = self.url, self.ready()
            except Exception:
                u, ready = None, False
            busy = self.app.busy() if self.app else False
            if ready and u == last and not busy:
                if time.time() - since >= quiet:
                    return
            else:
                last, since = u, time.time()
        raise TimeoutError(f"tab did not settle at {self.url}")

    def text(self) -> str:
        return self.eval("document.body ? document.body.innerText : ''") or ""


class ChromiumTab(BaseTab):
    name = "chromium"

    def __init__(self, pw):
        super().__init__()
        self.browser = pw.chromium.launch(args=[f"--host-resolver-rules={RESOLVER_RULES}"])
        self.ctx = self.browser.new_context(ignore_https_errors=True, viewport={"width": 1100, "height": 800})
        import standin
        self.ctx.add_init_script(standin.SCRIPTS.popup_guard)
        self.page = self.ctx.new_page()
        self.cdp = self.ctx.new_cdp_session(self.page)
        self.cdp.send("Page.enable")
        self.main_frame_id = None
        self.cdp.on("Page.frameNavigated", self._frame_navigated)
        self.cdp.on("Page.navigatedWithinDocument", self._same_doc)
        self.page.on("load", lambda p: self.app and self.app.queue("finish", self.page.url))
        self.main_frame_id = self.cdp.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
        self.cdp.on("Fetch.requestPaused", self._paused)
        self.cdp.send("Fetch.enable", {"patterns": [
            {"urlPattern": "*", "resourceType": "Document", "requestStage": "Request"},
            {"urlPattern": "*", "resourceType": "Document", "requestStage": "Response"}]})
        self.route_log: list[tuple[str, bool, bool]] = []

    def _paused(self, p):
        """Every document request (navigations, each redirect hop, frames) waits here for the controller.
        A refused one is answered 204, which leaves the current page on screen like WKWebView's .cancel."""
        url = p["request"]["url"]
        main = p.get("frameId") == self.main_frame_id
        if p.get("responseStatusCode") is not None or p.get("responseErrorReason"):
            headers = {h["name"].lower(): h["value"] for h in p.get("responseHeaders", [])}
            status = p.get("responseStatusCode", 0)
            final = not (300 <= status < 400 and "location" in headers)
            ok = True
            if final and self.app:
                ok = self.app.decide_response(url, main, headers.get("content-disposition"))
            if ok:
                self.cdp.send("Fetch.continueRequest", {"requestId": p["requestId"]})
            else:
                self.cdp.send("Fetch.fulfillRequest", {"requestId": p["requestId"], "responseCode": 204,
                                                       "responseHeaders": [], "body": ""})
            return
        redirect = p.get("redirectedRequestId") is not None
        ok = self.app.decide_navigation(url, main) if self.app else True
        self.route_log.append((url, main, redirect))
        if ok:
            self.cdp.send("Fetch.continueRequest", {"requestId": p["requestId"]})
        else:
            self.cdp.send("Fetch.fulfillRequest", {"requestId": p["requestId"], "responseCode": 204,
                                                   "responseHeaders": [], "body": ""})

    def _frame_navigated(self, params):
        frame = params["frame"]
        if frame.get("parentId"):
            return
        self.main_frame_id = frame["id"]
        if self.app:
            self.app.on_commit(frame["url"])
            self.app.queue("url", frame["url"])

    def _same_doc(self, params):
        if self.app and params.get("frameId") == self.main_frame_id:
            self.app.queue("url", params["url"])

    @property
    def url(self) -> str:
        return self.page.url

    def ready(self) -> bool:
        try:
            return self.page.evaluate("document.readyState") == "complete"
        except Exception:
            return False

    def load(self, url: str) -> None:
        try:
            self.page.goto(url, wait_until="commit")
        except Exception:
            pass

    def go_back(self) -> None:
        try:
            self.page.go_back(wait_until="commit")
        except Exception:
            pass

    def back_url(self):
        h = self.cdp.send("Page.getNavigationHistory")
        i = h["currentIndex"]
        return h["entries"][i - 1]["url"] if i > 0 else None

    def eval(self, js: str):
        return self.page.evaluate(js)

    def click(self, css: str) -> None:
        self.page.locator(css).first.click(no_wait_after=True, timeout=8000)

    def pump(self, seconds: float) -> None:
        self.page.wait_for_timeout(int(seconds * 1000))
        if self.app:
            self.app.run_queue()

    def close(self):
        try:
            self.browser.close()
        except Exception:
            pass


class WebKitTab(BaseTab):
    name = "webkit"

    def __init__(self):
        super().__init__()
        env = {**os.environ}
        for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            env.pop(k, None)
        self.proc = subprocess.Popen(["/usr/bin/python3.12", os.path.join(C.HERE, "wkhost.py")],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1, env=env)
        self.next_id = 0
        self.replies: dict[int, dict] = {}
        self.buf = ""
        self.route_log: list[tuple[str, bool, bool]] = []
        self._wait_event("ready", 40)

    def _send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _read(self, timeout: float):
        """One JSON line from the host, or None. Reads the raw fd and keeps its own buffer: select() on the
        pipe cannot see lines a buffered reader already holds."""
        while "\n" not in self.buf:
            r, _, _ = select.select([self.proc.stdout], [], [], timeout)
            if not r:
                return None
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("webkit host exited")
            self.buf += chunk.decode("utf-8", "replace")
            timeout = 0
        line, self.buf = self.buf.split("\n", 1)
        return json.loads(line) if line.strip() else self._read(0)

    def _dispatch(self, m) -> None:
        if "re" in m:
            self.replies[m["re"]] = m
            return
        ev = m.get("ev")
        if ev == "decide":
            if m["kind"] == "nav":
                ok = self.app.decide_navigation(m["url"], m["main"]) if self.app else True
                self.route_log.append((m["url"], m["main"], m["redirect"]))
            else:
                ok = (self.app.decide_response(m["url"], m["main"], m.get("disposition")) if self.app else True) \
                    and m.get("supported", True)
            self._send({"cmd": "decision", "id": m["id"], "allow": bool(ok)})
        elif ev == "commit" and self.app:
            self.app.on_commit(m["url"])
        elif ev == "uri" and self.app:
            self.app.queue("url", m["url"])
        elif ev == "finish" and self.app:
            self.app.queue("finish", m["url"])
        elif ev == "newwindow":
            self.decisions.append(("newwindow", m["url"]))

    def _wait_event(self, name, timeout):
        end = time.time() + timeout
        while time.time() < end:
            m = self._read(0.2)
            if m and m.get("ev") == name:
                return
            if m:
                self._dispatch(m)
        raise TimeoutError(name)

    def call(self, cmd: str, timeout: float = 20.0, **kw):
        self.next_id += 1
        cid = self.next_id
        self._send({"cmd": cmd, "id": cid, **kw})
        end = time.time() + timeout
        while time.time() < end:
            if cid in self.replies:
                return self.replies.pop(cid)
            m = self._read(0.1)
            if m:
                self._dispatch(m)
        raise TimeoutError(cmd)

    @property
    def url(self) -> str:
        return self.call("info").get("url") or ""

    def ready(self) -> bool:
        try:
            return not self.call("info").get("loading") and self.eval("document.readyState") == "complete"
        except Exception:
            return False

    def load(self, url: str) -> None:
        self.call("load", url=url)

    def go_back(self) -> None:
        self.call("back")

    def back_url(self):
        return self.call("info").get("back")

    def eval(self, js: str):
        r = self.call("eval", script=js.strip().rstrip(";"))
        if not r.get("ok"):
            raise RuntimeError(r.get("error"))
        return r.get("value")

    def click(self, css: str) -> None:
        ok = self.eval("(function(){var e=document.querySelector(" + json.dumps(css) + "); if(!e) return false; e.click(); return true;})()")
        if not ok:
            raise RuntimeError("no element " + css)

    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            m = self._read(min(0.05, max(0.0, end - time.time())))
            if m:
                self._dispatch(m)
        if self.app:
            self.app.run_queue()

    def close(self):
        try:
            self._send({"cmd": "quit", "id": 0})
            self.proc.wait(5)
        except Exception:
            self.proc.kill()
