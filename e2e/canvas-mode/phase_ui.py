"""Teacher UI phase: build a class, create and open a Canvas exam in the real web UI."""
import re
import time

import config as C
from lib import check, fake_state, section


CAPTURED = []      # every API response body the teacher UI saw


def teacher_login(p):
    p.goto(f"{C.SERVER}/teacher/login")
    p.fill("#username", "teacher")
    p.fill("#password", "teacher123")
    p.get_by_role("button", name="Sign in").click()
    p.wait_for_url(re.compile(r".*/teacher/?$"))


def run(browser, shots: str):
    """Returns dict(exam_id, start_code, class_id, ctx, page)."""
    section("3. Teacher UI: class, Canvas exam, open")
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, ignore_https_errors=True)
    T = ctx.new_page()
    errors = []
    T.on("pageerror", lambda e: errors.append(str(e)))
    T.on("response", lambda r: _capture(r))
    teacher_login(T)

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
    page_text = T.inner_text("body")
    check("UI: type detected as Classic, the lock-in sentence is shown",
          "Classic" in ctext and "cannot leave until they submit" in ctext, ctext[:300])
    check("UI: no access-code field, option or sentence anywhere on the Canvas setup page",
          T.locator("#exam-canvas-code-input").count() == 0 and not re.search(r"(?i)access code|managed|I will type the code", page_text),
          re.sub(r"\s+", " ", page_text)[:200])
    check("UI: no 'link Canvas' warning and no Canvas account is needed",
          T.locator("[data-testid=canvas-link-warning]").count() == 0)
    T.screenshot(path=f"{shots}/ui-03-setup.png", full_page=True)
    T.get_by_role("button", name="Save exam").click()
    T.wait_for_url(re.compile(r".*/teacher/exams/e_[A-Za-z0-9_-]+"), timeout=15000)
    exam_id = re.search(r"/exams/(e_[A-Za-z0-9_-]+)", T.url).group(1)
    check("UI: exam saved as draft", True)
    T.wait_for_timeout(500)
    T.screenshot(path=f"{shots}/ui-04-draft.png", full_page=True)

    # --- open
    T.get_by_role("button", name=re.compile("Open exam", re.I)).first.click()
    T.get_by_text(re.compile(r"^\d{6}$")).first.wait_for(timeout=20000)
    T.wait_for_timeout(800)
    text = T.inner_text("body")
    start_code = re.search(r"\b(\d{6})\b", text).group(1)
    check("exam opened and the fake Canvas got no API request at all (the server has nothing to set there)",
          not any("/api/v1/" in h for h in fake_state()["page_hits"]), str(fake_state()["page_hits"][:3]))
    T.screenshot(path=f"{shots}/ui-05-open.png", full_page=True)
    T.goto(f"{C.SERVER}/teacher/exams/{exam_id}?tab=live")
    T.wait_for_timeout(1500)
    live_text = T.inner_text("body")
    T.goto(f"{C.SERVER}/teacher/exams/{exam_id}/edit")
    T.wait_for_timeout(1500)
    edit_text = T.inner_text("body")
    edit_html = T.content()
    T.screenshot(path=f"{shots}/ui-06-edit.png", full_page=True)

    check("no access-code wording on the live or edit pages", not re.search(r"(?i)access code", live_text + edit_text), "")
    check("no page error in the teacher UI", not errors, str(errors[:2]))
    return {"exam_id": exam_id, "start_code": start_code, "class_id": class_id, "ctx": ctx, "page": T}


def _capture(r):
    try:
        if "/api/" in r.url:
            CAPTURED.append(r.text())
    except Exception:
        pass
