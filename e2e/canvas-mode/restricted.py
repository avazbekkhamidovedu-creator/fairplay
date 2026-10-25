"""The Canvas part of ExternalExamController / MainWindowController, ported: the restricted browser's
session state and delegate callbacks, driving a Tab (tabs.py). The fill flow is NativeStandIn.on_loaded.

Source of truth: mac/Sources/Proctora/ExternalExamController.swift (canvasCommitted, recoverIfBlocked,
autoOpenIfNeeded, quizPressed, backPressed, backAllowed, reportBlocked, checkFinishURL) and
MainWindowController.swift (decidePolicyFor action and response, didCommit, didFinish).
"""
from __future__ import annotations

import time
from urllib.parse import urlsplit

import canvas_nav as N
import standin as S

MAX_BLOCK_REPORTS = 100
MAX_RECOVERIES = 5
NOTICE = "This page is not available during the exam."


class RestrictedApp:
    def __init__(self, open_msg: dict, tab, clock=time.time):
        self.stand = S.NativeStandIn(open_msg)
        self.tab = tab
        tab.app = self
        self.clock = clock
        self.start_url = open_msg["url"]
        self.exam_host = (urlsplit(self.start_url).hostname or "").lower()
        self.scope = N.scope_from_json(open_msg.get("canvas_scope"))
        self.phase = N.LOBBY
        self.auto_opened = False
        self.sign_in_seen = False
        self.recovery = N.RecoveryGate()
        self.block_log = N.RecoveryGate()
        self.recovery_pending_at: float | None = None
        self.recovery_tries = 0
        self.blocked_reports = 0
        self.finishing = False
        self.finish_due: float | None = None
        self.finish_reason: str | None = None
        self.notice: str | None = None
        self.events: list[tuple[str, str]] = []
        self.queued: list[tuple[str, str]] = []
        self.timeline: list[str] = []
        self.auto_open_count = 0
        self.fills_attempted = 0
        self.recoveries: list[str] = []

    # ---------------------------------------------------------------- policy helpers
    def allows(self, url: str) -> bool:
        return S.policy_allows(url, self.stand.rules)

    def blocks_main_frame(self, url: str) -> bool:
        return self.scope is not None and N.decide(url, self.exam_host, self.scope, self.phase) == N.BLOCK

    def allows_main_frame(self, url: str) -> bool:
        return self.allows(url) and not self.blocks_main_frame(url)

    def child_allows(self, url: str, main_frame: bool = True) -> bool:
        return S.is_blank(url) or (self.allows(url) and (not main_frame or not self.blocks_main_frame(url)))

    def back_allowed(self, previous: str) -> bool:
        if not self.allows_main_frame(previous):
            return False
        if self.phase == N.QUIZ:
            h = (urlsplit(previous).hostname or "").lower()
            return h == self.exam_host and not N.is_sign_in(previous, self.exam_host)
        return True

    # ---------------------------------------------------------------- events
    def record(self, typ: str, detail: str) -> None:
        self.events.append((typ, detail))
        self.stand.report(typ, detail)

    def report_blocked(self, url: str) -> None:
        detail = N.block_detail(url)
        if self.blocked_reports < MAX_BLOCK_REPORTS and self.block_log.first_time(detail):
            self.blocked_reports += 1
            self.record("blocked_navigation", detail)
        self.notice = NOTICE

    # ---------------------------------------------------------------- delegate: decidePolicyFor
    def decide_navigation(self, url: str, main_frame: bool) -> bool:
        if main_frame and self.allows(url) and self.blocks_main_frame(url):
            self.timeline.append(f"block {url}")
            self.report_blocked(url)
            return False
        if self.allows(url) or S.is_blank(url):
            self.timeline.append(f"allow {url}")
            return True
        h = urlsplit(url).hostname or url
        self.timeline.append(f"deny {url}")
        self.record("blocked_navigation", h)
        return False

    @staticmethod
    def is_attachment(disposition: str | None) -> bool:
        d = (disposition or "").strip().lower()
        return d == "attachment" or d.startswith("attachment;") or d.startswith("attachment ")

    def decide_response(self, url: str, main_frame: bool, disposition: str | None = None) -> bool:
        if main_frame and not self.child_allows(url):
            if self.allows(url):
                self.report_blocked(url)
            else:
                self.record("blocked_navigation", urlsplit(url).hostname or url)
            return False
        if main_frame and self.is_attachment(disposition):
            self.record("blocked_navigation", f"download from {urlsplit(url).hostname or '?'}")
            self.notice = NOTICE
            return False
        return True

    # ---------------------------------------------------------------- delegate: didCommit, KVO url, didFinish
    def on_commit(self, url: str) -> None:
        if self.scope is None or not url:
            return
        if N.is_sign_in(url, self.exam_host):
            self.sign_in_seen = True
        if self.phase == N.LOBBY and N.enters_quiz(url, self.exam_host, self.scope):
            self.phase = N.QUIZ
            self.timeline.append(f"phase quiz at {url}")

    def queue(self, kind: str, url: str) -> None:
        self.queued.append((kind, url))

    def busy(self) -> bool:
        return bool(self.queued) or self.finish_due is not None or self.recovery_pending_at is not None

    def run_queue(self) -> None:
        while self.queued:
            kind, url = self.queued.pop(0)
            if kind == "url":
                self.check_finish_url(url)
                self.recover_if_blocked(url)
            elif kind == "finish":
                self.page_loaded()
        now = self.clock()
        if self.recovery_pending_at is not None and now >= self.recovery_pending_at:
            self.recovery_pending_at = None
            self.recover_if_blocked(self.tab.url)
        if self.finish_due is not None and now >= self.finish_due:
            self.finish_due = None
            live = self.tab.url
            if not self.finishing and self.stand.finish_hit(live):
                self.finishing = True
                self.finish_reason = "finish_url"

    # ---------------------------------------------------------------- canvasPageLoaded
    def page_loaded(self) -> None:
        if self.finishing:
            return
        self.auto_open_if_needed()
        out = self.stand.on_loaded(self.tab)
        if out == "clicked":
            self.fills_attempted += 1

    def auto_open_if_needed(self) -> None:
        if self.scope is None or self.auto_opened or not self.sign_in_seen or self.phase != N.LOBBY:
            return
        url = self.tab.url
        if not N.is_landing(url, self.exam_host, self.scope):
            return
        self.auto_opened = True
        self.auto_open_count += 1
        self.timeline.append("auto-open")
        self.tab.load(self.start_url)

    # ---------------------------------------------------------------- checkFinishURL (Canvas)
    def check_finish_url(self, url: str) -> None:
        if self.finishing or not url or not self.stand.finish_hit(url):
            return
        if self.finish_due is not None:
            return
        self.finish_due = self.clock() + 2.0

    # ---------------------------------------------------------------- recoverIfBlocked
    def recover_if_blocked(self, url: str | None) -> None:
        if (self.scope is None or self.finishing or not url or not self.allows(url)
                or not self.blocks_main_frame(url)):
            self.recovery_tries = 0
            return
        detail = N.block_detail(url)
        if self.recovery.first_time(detail) and self.blocked_reports < MAX_BLOCK_REPORTS:
            self.blocked_reports += 1
            self.record("blocked_navigation", detail + " (in page)")
        self.notice = NOTICE
        if not self.recovery.should_recover(self.clock()):
            self.schedule_recheck()
            return
        if self.recovery_tries >= MAX_RECOVERIES:
            return
        self.recovery_tries += 1
        start_ok = self.allows_main_frame(self.start_url)
        previous = self.tab.back_url()
        if self.phase == N.QUIZ and start_ok:
            self.recoveries.append("load start")
            self.tab.load(self.start_url)
        elif previous and previous != url and self.back_allowed(previous):
            self.recoveries.append(f"back to {previous}")
            self.tab.go_back()
        elif start_ok:
            self.recoveries.append("load start")
            self.tab.load(self.start_url)
        self.schedule_recheck()

    def schedule_recheck(self) -> None:
        if self.recovery_pending_at is None:
            self.recovery_pending_at = self.clock() + N.RecoveryGate.MIN_INTERVAL + 0.1

    # ---------------------------------------------------------------- strip buttons
    def quiz_pressed(self) -> None:
        if self.finishing or self.tab.eval("document.readyState") != "complete":
            return
        cur = self.tab.url
        if (self.scope is not None and (urlsplit(cur).hostname or "").lower() == self.exam_host
                and N.is_under_item(cur, self.scope)):
            return
        self.tab.load(self.start_url)

    def back_pressed(self) -> None:
        previous = self.tab.back_url()
        if self.finishing or not previous or not self.back_allowed(previous):
            return
        self.tab.go_back()

    def back_enabled(self) -> bool:
        previous = self.tab.back_url()
        return bool(previous) and self.back_allowed(previous)

    # ---------------------------------------------------------------- start
    def start(self) -> None:
        self.tab.load(self.start_url)

    # ---------------------------------------------------------------- test helpers
    def click(self, css: str, settle: bool = True) -> None:
        self.tab.click(css)
        if settle:
            self.tab.settle()

    def goto_unguarded(self, url: str) -> None:
        """A page that reached the view without passing the delegate (the fill gate must still hold)."""
        self.tab.app = None
        try:
            self.tab.load(url)
            self.tab.pump(1.0)
            self.tab.settle()
        finally:
            self.tab.app = self
