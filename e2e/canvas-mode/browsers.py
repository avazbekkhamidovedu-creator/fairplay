"""One small page interface over Chromium (Playwright) and WebKit (WebKitGTK through WebDriver)."""
from __future__ import annotations

import os
import sys
import time

import config as C

HOSTS = [C.CANVAS_HOST, C.SSO_HOST, C.OTHER_HOST]
RESOLVER_RULES = ", ".join(f"MAP {h} 127.0.0.1" for h in HOSTS)


def _wrap(js: str) -> str:
    return js


class ChromiumEngine:
    name = "chromium"

    def __init__(self, pw):
        self._pw = pw
        self.browser = self._pw.chromium.launch(args=[f"--host-resolver-rules={RESOLVER_RULES}"])
        self.ctx = self.browser.new_context(ignore_https_errors=True, viewport={"width": 1100, "height": 800})
        self.ctx.add_init_script(_script_guard())

    def new_page(self):
        return ChromiumPage(self, self.ctx.new_page())

    def fresh_context(self):
        self.ctx.close()
        self.ctx = self.browser.new_context(ignore_https_errors=True)
        self.ctx.add_init_script(_script_guard())

    def close(self):
        self.browser.close()


def _script_guard() -> str:
    """The popup guard the app installs at document start (CanvasScripts.popupGuard)."""
    import standin
    return standin.SCRIPTS.popup_guard


class ChromiumPage:
    def __init__(self, engine, page):
        self.engine, self.p = engine, page

    @property
    def url(self) -> str:
        return self.p.url

    def goto(self, url: str) -> None:
        self.p.goto(url, wait_until="load")

    def eval(self, js: str):
        return self.p.evaluate(js)

    def set_mark(self):
        self.p.evaluate("window.__mark = 1")

    def mark_gone(self) -> bool:
        try:
            return self.p.evaluate("window.__mark === undefined")
        except Exception:
            return False

    def ready(self) -> bool:
        try:
            return self.p.evaluate("document.readyState") == "complete"
        except Exception:
            return False

    def click(self, css: str) -> None:
        self.p.click(css)

    def value_of(self, css: str):
        return self.p.evaluate("(s) => { var e = document.querySelector(s); return e ? e.value : null; }", css)

    def text(self) -> str:
        return self.p.inner_text("body")

    def click_popup(self, css: str):
        before = set(self.engine.ctx.pages)
        self.p.click(css)
        deadline = time.time() + 6
        while time.time() < deadline:
            new = [x for x in self.engine.ctx.pages if x not in before]
            if new:
                new[0].wait_for_load_state("load")
                return ChromiumPage(self.engine, new[0])
            self.p.wait_for_timeout(100)
        return None

    def close(self):
        self.p.close()


class WebKitEngine:
    name = "webkit"

    def __init__(self):
        sys.path.insert(0, C.SEL_PKGS)
        from selenium import webdriver
        from selenium.webdriver.webkitgtk.options import Options
        for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            os.environ.pop(k, None)
        o = Options()
        o.binary_location = "/usr/lib/x86_64-linux-gnu/webkit2gtk-4.1/MiniBrowser"
        o.add_argument("--automation")
        o.set_capability("acceptInsecureCerts", True)
        self.driver = webdriver.WebKitGTK(options=o)
        self.driver.set_script_timeout(20)
        self.main = self.driver.current_window_handle
        self.guard = _script_guard()

    def new_page(self):
        return WebKitPage(self, self.driver.current_window_handle)

    def fresh_context(self):
        self.driver.delete_all_cookies()

    def close(self):
        self.driver.quit()


class WebKitPage:
    def __init__(self, engine, handle):
        self.engine, self.h = engine, handle

    @property
    def d(self):
        self.engine.driver.switch_to.window(self.h)
        return self.engine.driver

    @property
    def url(self) -> str:
        return self.d.current_url

    def goto(self, url: str) -> None:
        self.d.get(url)
        self._guard()

    def _guard(self):
        # WebDriver has no document-start script hook: install the guard right after load.
        try:
            self.d.execute_script(self.engine.guard)
        except Exception:
            pass

    def eval(self, js: str):
        return self.d.execute_script("return " + js.strip().rstrip(";"))

    def set_mark(self):
        self.d.execute_script("window.__mark = 1")

    def mark_gone(self) -> bool:
        try:
            return self.d.execute_script("return window.__mark === undefined")
        except Exception:
            return False

    def ready(self) -> bool:
        try:
            return self.d.execute_script("return document.readyState") == "complete"
        except Exception:
            return False

    def click(self, css: str) -> None:
        self.d.find_element("css selector", css).click()

    def value_of(self, css: str):
        return self.d.execute_script(
            "var e = document.querySelector(arguments[0]); return e ? e.value : null;", css)

    def text(self) -> str:
        return self.d.find_element("css selector", "body").text

    def click_popup(self, css: str):
        d = self.d
        before = set(d.window_handles)
        d.find_element("css selector", css).click()
        deadline = time.time() + 6
        while time.time() < deadline:
            new = [h for h in d.window_handles if h not in before]
            if new:
                time.sleep(0.8)
                return WebKitPage(self.engine, new[0])
            time.sleep(0.1)
        return None

    def close(self):
        if self.h != self.engine.main:
            self.d.close()
            self.engine.driver.switch_to.window(self.engine.main)


def settle(page, quiet: float = 0.5, timeout: float = 15.0) -> None:
    """Wait until the page is loaded and its URL has not changed for `quiet` seconds."""
    deadline = time.time() + timeout
    last, since = None, time.time()
    while time.time() < deadline:
        try:
            u = page.url
            ok = page.ready()
        except Exception:
            u, ok = None, False
        if ok and u == last:
            if time.time() - since >= quiet:
                return
        else:
            last, since = u, time.time()
        time.sleep(0.1)
    raise TimeoutError("page did not settle")


def wait_new_document(page, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if page.mark_gone():
            return
        time.sleep(0.1)
    raise TimeoutError("no navigation after the click")
