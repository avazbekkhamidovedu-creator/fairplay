"""LOCAL TEST FIXTURE ONLY. Simulated LMS and simulated native lock evidence.

No routes are mounted in the FairPlay server. No real university credentials or
questions belong here. HMAC proves possession of a fixture key, NOT macOS lockdown.
Run only via the loopback command in README.md.
"""
from dataclasses import asdict
import hashlib
import hmac
import secrets
import threading
import time
from fastapi import FastAPI, Header, HTTPException
from app.university_profile import UniversityProfile, ProtectedLaunch, validate_protected_launches


def proof_message(attempt: dict, nonce: str, locked: bool) -> str:
    return "|".join(("fairplay.fixture.lock.v1", attempt["id"], attempt["student"],
                     attempt["session"], attempt["device"], "1", nonce, "1" if locked else "0"))


def sign_lock(key: str, attempt: dict, nonce: str, locked: bool) -> str:
    return hmac.new(key.encode(), proof_message(attempt, nonce, locked).encode(), hashlib.sha256).hexdigest()


def create_app(*, native_key: str, student_key: str, clock=time.time) -> FastAPI:
    app = FastAPI(title="FairPlay isolated mock admission fixture")
    profile = UniversityProfile("fixture", "https://lms.example.edu", "https://fairplay.example.edu",
                                ("https://lms.example.edu",), "mock_canvas", 1)
    launches = validate_protected_launches(profile, (
        ProtectedLaunch("https://lms.example.edu/courses/1/quizzes/2", "exam_fixture"),))
    attempts: dict[str, dict] = {}
    admissions: dict[str, dict] = {}
    mutex = threading.Lock()

    def auth(actual, expected):
        if not isinstance(actual, str) or not hmac.compare_digest(actual, expected):
            raise HTTPException(401, "Fixture credential required")

    def attempt_for(aid, session, device):
        a = attempts.get(aid)
        if a is None:
            raise HTTPException(404, "Unknown attempt")
        if not session or not device or a["session"] != session or a["device"] != device:
            raise HTTPException(403, "Wrong device or session")
        return a

    @app.get("/api/university/profile")
    def get_profile(x_fixture_native: str = Header(default="")):
        auth(x_fixture_native, native_key)
        return {**asdict(profile), "protected_launches": [
            {"launch_url": url, "exam_id": eid} for url, eid in launches.items()],
            "mode": "mock_only", "production_gate_verified": False}

    @app.post("/mock-lms/launch")
    def launch(body: dict, x_fixture_student: str = Header(default="")):
        auth(x_fixture_student, student_key)
        # A single authenticated fixture student and single protected resource.
        if body.get("launch_url") not in launches:
            raise HTTPException(403, "Unknown protected exam")
        if any(k in body for k in ("student", "exam_id", "return_url", "question_url", "allowed_domains")):
            raise HTTPException(400, "Cannot override server-owned context")
        device, session = body.get("device"), body.get("session")
        if not all(isinstance(v, str) and 1 <= len(v) <= 128 and v.isascii() and
                   all(c.isalnum() or c in "_-" for c in v) for v in (device, session)):
            raise HTTPException(400, "Invalid fixture binding")
        with mutex:
            aid = secrets.token_urlsafe(24)
            a = dict(id=aid, student="fixture_student", device=device, session=session,
                     state="pending", nonce=secrets.token_urlsafe(24), expires=clock()+120)
            attempts[aid] = a
            return {k: a[k] for k in ("id", "student", "device", "session", "nonce", "expires", "state")}

    @app.post("/api/attempts/{aid}/lock")
    def locked(aid: str, body: dict, x_fixture_native: str = Header(default=""),
               x_app_session: str = Header(default=""), x_device: str = Header(default="")):
        auth(x_fixture_native, native_key)
        with mutex:
            a = attempt_for(aid, x_app_session, x_device)
            if a["state"] != "pending" or clock() >= a["expires"]:
                raise HTTPException(409, "Attempt is not pending or challenge expired")
            if body.get("locked") is not True or body.get("nonce") != a["nonce"]:
                raise HTTPException(403, "Lock did not succeed")
            expected = sign_lock(native_key, a, a["nonce"], True)
            if not isinstance(body.get("proof"), str) or not hmac.compare_digest(body["proof"], expected):
                raise HTTPException(403, "Invalid fixture lock proof")
            token = secrets.token_urlsafe(32)
            # Atomic in this single-process fixture only; production needs DB/Redis atomicity.
            a.update(state="locked", nonce=None, heartbeat=clock())
            admissions[token] = dict(attempt=aid, consumed=False, expires=clock()+30)
            return {"admission": token, "state": "locked"}

    @app.post("/mock-lms/admit")
    def admit(body: dict, x_fixture_native: str = Header(default=""),
              x_app_session: str = Header(default=""), x_device: str = Header(default="")):
        auth(x_fixture_native, native_key)
        with mutex:
            token = body.get("admission")
            grant = admissions.get(token) if isinstance(token, str) else None
            if not grant or grant["consumed"] or clock() >= grant["expires"]:
                raise HTTPException(403, "Missing, expired or replayed admission")
            a = attempt_for(grant["attempt"], x_app_session, x_device)
            if a["state"] != "locked" or clock() - a["heartbeat"] >= 20:
                raise HTTPException(403, "Lock is not current")
            grant["consumed"] = True
            a["state"] = "active"
            return {"attempt_id": a["id"], "state": "active"}

    @app.get("/mock-lms/attempts/{aid}/questions")
    def questions(aid: str, x_fixture_native: str = Header(default=""),
                  x_app_session: str = Header(default=""), x_device: str = Header(default="")):
        auth(x_fixture_native, native_key)
        with mutex:
            a = attempt_for(aid, x_app_session, x_device)
            if a["state"] != "active" or clock() - a["heartbeat"] >= 20:
                raise HTTPException(403, "No active locked admission")
            return {"questions": [{"id": "q1", "text": "Fixture only: what is 2 + 2?"}]}

    @app.post("/api/attempts/{aid}/heartbeat")
    def heartbeat(aid: str, body: dict, x_fixture_native: str = Header(default=""),
                  x_app_session: str = Header(default=""), x_device: str = Header(default="")):
        auth(x_fixture_native, native_key)
        with mutex:
            a = attempt_for(aid, x_app_session, x_device)
            if a["state"] not in ("locked", "active") or clock() - a["heartbeat"] >= 20:
                raise HTTPException(409, "Session cannot resume without a new launch")
            if body.get("locked") is not True:
                a["state"] = "revoked"
            else:
                a["heartbeat"] = clock()
            return {"state": a["state"]}

    @app.post("/mock-lms/attempts/{aid}/submit")
    def submit(aid: str, x_fixture_native: str = Header(default=""),
               x_app_session: str = Header(default=""), x_device: str = Header(default="")):
        auth(x_fixture_native, native_key)
        with mutex:
            a = attempt_for(aid, x_app_session, x_device)
            if a["state"] != "active" or clock() - a["heartbeat"] >= 20:
                raise HTTPException(403, "Submission requires active locked admission")
            a["state"] = "submitted"
            return {"receipt": secrets.token_urlsafe(24), "state": "submitted"}

    return app
