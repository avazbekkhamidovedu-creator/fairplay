"""A Canvas exam with a code the teacher types (manual), built in the web UI."""
import re

import config as C
import engine_suite
import phase_abuse
import standin as S
from browsers import settle
from lib import check, fake, fake_state, section

MANUAL = "ManualCode-77"


def run(T, pw, class_id, teacher_token, db_state):
    section("4b. Manual access code (teacher types it), Chromium")
    import browsers
    fake("POST", "/code", {"code": MANUAL})
    fake("POST", "/forget-sessions")
    sets_before = fake_state()["set_calls"]
    T.goto(f"{C.SERVER}/teacher/exams/new?class={class_id}")
    T.fill("#exam-title", "Canvas manual quiz")
    T.fill("#exam-duration", "30")
    T.get_by_role("button", name=re.compile("More settings")).click()
    T.get_by_text("On another website").first.click()
    T.get_by_text("Canvas quiz", exact=True).first.click()
    T.fill("#exam-external-url", C.QUIZ_URL)
    T.get_by_text("I will type the code I set in Canvas").click()
    T.fill("#exam-canvas-code-input", MANUAL)
    T.get_by_role("button", name="Save exam").click()
    T.wait_for_url(re.compile(r".*/teacher/exams/e_[A-Za-z0-9_-]+"), timeout=15000)
    exam_id = re.search(r"/exams/(e_[A-Za-z0-9_-]+)", T.url).group(1)
    T.goto(f"{C.SERVER}/teacher/exams/{exam_id}/edit")
    T.wait_for_timeout(1500)
    html = T.content() + T.inner_text("body")
    check("manual: after saving, the edit page does not show the typed code", MANUAL not in html)
    check("manual: the edit page says a code is saved", "A code is saved" in html or "code is saved" in html.lower())
    T.get_by_role("button", name=re.compile("Open exam", re.I)).first.click()
    T.get_by_text(re.compile(r"^\d{6}$")).first.wait_for(timeout=20000)
    start_code = re.search(r"\b(\d{6})\b", T.inner_text("body")).group(1)
    check("manual: opening did not rotate the code in Canvas", fake_state()["set_calls"] == sets_before and fake_state()["code"] == MANUAL)
    s, view = S.jhttp("GET", f"/api/exams/{exam_id}", token=teacher_token)
    check("manual: teacher view says source manual, code set, and holds no code",
          view["canvas"]["access_code_source"] == "manual" and view["canvas"]["code_status"] == "set"
          and MANUAL not in str(view), str(view["canvas"]))
    row = db_state(exam_id)
    check("manual: stored code is encrypted at rest", row[0] and MANUAL not in str(row[0]))
    exam = {"id": exam_id, "start_code": start_code}
    stand, token, (st, a) = phase_abuse.make_stand("lt0001", exam, "manual-dev-0001")
    check("manual: a student starts the manual Canvas exam", stand is not None, str(st))
    eng = browsers.ChromiumEngine(pw)
    try:
        page = eng.new_page()
        page.goto(C.QUIZ_URL)
        settle(page)
        outs = engine_suite.drive(stand, page, first_click="#take_quiz")
        settle(page)
        check("manual: the app typed the teacher's code and the quiz opened", outs == ["clicked", "nofield"] and "What is 2+2?" in page.text()
              and stand.codes_seen == [MANUAL], f"{outs} {stand.codes_seen}")
    finally:
        eng.close()
    s, res = S.jhttp("POST", f"/api/exams/{exam_id}/close", None, teacher_token)
    check("manual: close succeeds", s == 200, str(s))
    check("manual: closing leaves the teacher's own code in Canvas (it is theirs)", fake_state()["code"] == MANUAL)
    row = db_state(exam_id)
    check("manual: the stored code is forgotten on close", not row[0], str(row))
    s, res = S.jhttp("POST", f"/api/exams/{exam_id}/open", None, teacher_token)
    check("manual: reopening without the code is refused 409", s == 409, f"{s} {res}")
    s, res = S.jhttp("POST", f"/api/exams/{exam_id}/open", {"canvas_access_code": MANUAL}, teacher_token)
    check("manual: reopening with the code works", s == 200, f"{s} {res}")
    S.jhttp("POST", f"/api/exams/{exam_id}/close", None, teacher_token)
    return MANUAL
