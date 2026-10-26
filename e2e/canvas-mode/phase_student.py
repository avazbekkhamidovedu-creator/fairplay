import json
import os
import re

import config as C

APP_JS = open(os.path.join(C.HERE, "fake_app.js")).read()


def fake_app(device: str) -> str:
    return (APP_JS.replace("%KEY%", json.dumps(C.APP_KEY)).replace("%DEVICE%", json.dumps(device))
            .replace("%VERSION%", json.dumps(C.APP_VERSION)))


def app_context(browser, device: str = C.DEVICE):
    ctx = browser.new_context(viewport={"width": 1280, "height": 860}, ignore_https_errors=True)
    ctx.add_init_script(fake_app(device))
    return ctx


def student_login(p, user: str):
    p.goto(f"{C.SERVER}/student/login")
    p.fill("#username", user)
    p.fill("#password", f"{user}123")
    p.get_by_role("button", name="Sign in").click()
    p.wait_for_url(re.compile(r".*/student/?$"))


def start_in_app(p, title: str, code: str) -> dict:
    """Types the start code in the student page of the (stand-in) app; returns the openExternal message."""
    try:
        p.get_by_text(title).first.wait_for(timeout=10000)
    except Exception:
        print("STUDENT HOME:", p.inner_text("body")[:800])
        p.screenshot(path=f"{C.SCRATCH}/shots/student-home-fail.png")
        raise
    card = p.locator("article, li, section").filter(has_text=title).filter(
        has=p.get_by_role("button", name=re.compile("^(Start exam|Enter start code)$"))).last
    try:
        card.get_by_role("button", name=re.compile("^(Start exam|Enter start code)$")).click(timeout=8000)
    except Exception:
        print("STUDENT HOME:", p.inner_text("body")[:800])
        p.screenshot(path=f"{C.SCRATCH}/shots/student-home-fail.png", full_page=True)
        raise
    boxes = p.get_by_role("textbox", name=re.compile(r"Digit \d of 6"))
    ready = p.get_by_label(re.compile("I understand and I'm ready"))
    if ready.count():
        ready.check()
        p.get_by_role("button", name="Lock and continue").click()
    try:
        boxes.first.wait_for(timeout=15000)
    except Exception:
        print("BEFORE START:", p.url, p.inner_text("body")[:900], p.evaluate("window.__native"))
        p.screenshot(path=f"{C.SCRATCH}/shots/student-start-fail.png", full_page=True)
        raise
    for i, d in enumerate(code):
        boxes.nth(i).fill(d)
    p.screenshot(path=f"{C.SCRATCH}/shots/student-code-entry.png")
    p.get_by_role("button", name=re.compile("^(Start exam|Start|Continue)$")).last.click()
    try:
        p.wait_for_url(re.compile(r".*/student/external/a_.+"), timeout=15000)
    except Exception:
        print("AFTER CODE:", p.url, p.inner_text("body")[:600], p.evaluate("window.__native"))
        p.screenshot(path=f"{C.SCRATCH}/shots/student-after-code.png", full_page=True)
        raise
    for _ in range(100):
        msgs = p.evaluate("window.__native")
        m = next((x for x in msgs if x.get("action") == "openExternal"), None)
        if m:
            return {"msg": m, "actions": [x.get("action") for x in msgs]}
        p.wait_for_timeout(200)
    raise RuntimeError("no openExternal from the exam page: " + p.inner_text("body")[:300])
