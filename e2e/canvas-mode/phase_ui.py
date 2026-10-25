"""Teacher UI phase: link Canvas, build a class, create and open a Canvas exam in the real web UI."""
import re
import time

import config as C
from lib import check, fake, fake_state, note, section

import browsers

CAPTURED = []      # every API response body the teacher UI saw


def teacher_login(p):
    p.goto(f"{C.SERVER}/teacher/login")
    p.fill("#username", "teacher")
    p.fill("#password", "teacher123")
    p.get_by_role("button", name="Sign in").click()
    p.wait_for_url(re.compile(r".*/teacher/?$"))


def run(browser, shots: str):
    """Returns dict(exam_id, start_code, invigilator_code, teacher_ctx, code)."""
    section("3. Teacher UI: link Canvas, class, Canvas exam, open")
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, ignore_https_errors=True)
    T = ctx.new_page()
    errors = []
    T.on("pageerror", lambda e: errors.append(str(e)))
    T.on("response", lambda r: _capture(r))
    teacher_login(T)

    # --- link the fake Canvas account
    T.goto(f"{C.SERVER}/teacher/lms")
    T.wait_for_selector("#lms-url")
    T.fill("#lms-url", C.CANVAS)
    T.fill("#lms-token", C.TOKEN)
    T.get_by_role("button", name=re.compile(r"^Connect Canvas")).click()
    T.wait_for_timeout(1500)
    check("UI: Canvas account linked (fake accepted the token)",
          any(c["path"] == "/api/v1/users/self" and c["auth_ok"] for c in fake_state()["api_calls"]))
    T.screenshot(path=f"{shots}/ui-01-lms.png")

    # --- class with students
    T.goto(f"{C.SERVER}/teacher/classes")
    T.get_by_role("button", name="New class").click()
    T.fill("#class-new-name", "Canvas E2E class")
    T.get_by_role("button", name="Create class").click()
    T.wait_for_url(re.compile(r".*/teacher/classes/\d+"))
    class_id = re.search(r"/classes/(\d+)", T.url).group(1)
    T.locator("textarea").first.fill("alice\nbob\ncarol")
    T.get_by_role("button", name="Add students").click()
    T.wait_for_selector("text=Added 3")
    check("UI: class created with 3 students", True)
    T.screenshot(path=f"{shots}/ui-02-class.png")

    # --- exam setup page
    T.goto(f"{C.SERVER}/teacher/exams/new?class={class_id}")
    T.fill("#exam-title", "Canvas E2E quiz")
    T.fill("#exam-duration", "40")
    T.get_by_role("button", name=re.compile("More settings")).click()
    T.get_by_text("On another website").first.click()
    T.get_by_text("Canvas quiz", exact=True).first.click()
    T.fill("#exam-external-url", C.QUIZ_URL)
    T.fill("#exam-allowed-domains", C.SSO_HOST)
    T.wait_for_selector("[data-testid=canvas-settings]")
    ctext = T.inner_text("[data-testid=canvas-settings]")
    check("UI: type detected as Classic, managed code chosen by default",
          "Classic" in ctext and "FairPlay sets and changes it for me" in ctext, ctext[:300])
    check("UI: no 'link Canvas' warning because an account is linked",
          T.locator("[data-testid=canvas-link-warning]").count() == 0)
    T.screenshot(path=f"{shots}/ui-03-setup.png", full_page=True)
    T.get_by_role("button", name="Save exam").click()
    T.wait_for_url(re.compile(r".*/teacher/exams/e_[A-Za-z0-9_-]+"), timeout=15000)
    exam_id = re.search(r"/exams/(e_[A-Za-z0-9_-]+)", T.url).group(1)
    check("UI: exam saved as draft; fake Canvas has no code yet", fake_state()["code"] == "")
    T.wait_for_timeout(500)
    T.screenshot(path=f"{shots}/ui-04-draft.png", full_page=True)

    # --- open
    T.get_by_role("button", name=re.compile("Open exam", re.I)).first.click()
    T.get_by_text(re.compile(r"^\d{6}$")).first.wait_for(timeout=20000)
    T.wait_for_timeout(800)
    text = T.inner_text("body")
    code = fake_state()["code"]
    check("fake Canvas now has a random 24-char code",
          len(code) == 24 and re.fullmatch(r"[A-Za-z0-9]{24}", code) is not None and
          not set(code) & set("0O1lI"), code[:2] + "…")
    check("exactly one rotation call reached the fake", fake_state()["set_calls"] == 1, str(fake_state()["set_calls"]))
    start_code = re.search(r"\b(\d{6})\b", text).group(1)
    T.screenshot(path=f"{shots}/ui-05-open.png", full_page=True)
    T.goto(f"{C.SERVER}/teacher/exams/{exam_id}?tab=live")
    T.wait_for_timeout(1500)
    live_text = T.inner_text("body")
    T.goto(f"{C.SERVER}/teacher/exams/{exam_id}/edit")
    T.wait_for_timeout(1500)
    edit_text = T.inner_text("body")
    edit_html = T.content()
    T.screenshot(path=f"{shots}/ui-06-edit.png", full_page=True)

    texts = [text, live_text, edit_text, edit_html]
    check("code is not in any teacher page text or HTML", all(code not in t for t in texts))
    bodies = [b for b in CAPTURED]
    check("code is not in any API response the teacher UI received", all(code not in b for b in bodies),
          f"{len(bodies)} responses")
    check("no page error in the teacher UI", not errors, str(errors[:2]))
    return {"exam_id": exam_id, "start_code": start_code, "class_id": class_id, "ctx": ctx, "page": T,
            "code": code}


def _capture(r):
    try:
        if "/api/" in r.url:
            CAPTURED.append(r.text())
    except Exception:
        pass
