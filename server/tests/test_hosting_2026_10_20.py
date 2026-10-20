"""Hosted test server (2026-10-20): Render database URLs and FAIRPLAY_INITIAL_USERS."""
from app.config import async_db_url, initial_users

from .conftest import login  # noqa: F401


def test_render_database_urls_use_asyncpg():
    assert async_db_url("postgres://u:p@h:5432/db") == "postgresql+asyncpg://u:p@h:5432/db"
    assert async_db_url("postgresql://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert async_db_url("postgresql+asyncpg://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert async_db_url("sqlite+aiosqlite:///./data/x.db") == "sqlite+aiosqlite:///./data/x.db"


def test_initial_users_parsing():
    got = initial_users("mister:longpassword1:Avazbek Khamidov:teacher; s1:studentpw1:Student One:student;"
                        "bad:short:X:student;x:longpassword:Y:admin;broken")
    assert got == [("mister", "longpassword1", "Avazbek Khamidov", "teacher"),
                   ("s1", "studentpw1", "Student One", "student")]
    assert initial_users("") == []


def test_initial_users_sign_in_without_demo_accounts(tmp_path, static_dir):
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app
    s = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'h.db'}", secret="test-secret-0123456789abcdef",
                 pbkdf2_iterations=1000, require_app=True, app_keys=["k" * 40], dev=False, seed_demo=False,
                 single_process=True, static_dir=str(static_dir), uploads_dir=str(tmp_path / "up"),
                 initial_users=initial_users("mister:goodpassword:Mister:teacher;"
                                             "teacher:teacher123:Fake:teacher"))
    with TestClient(create_app(s)) as c:
        ok = c.post("/api/auth/login", json={"username": "mister", "password": "goodpassword"})
        assert ok.status_code == 200 and ok.json()["role"] == "teacher", ok.text
        # a demo name with its public password is never created this way
        assert c.post("/api/auth/login", json={"username": "teacher", "password": "teacher123"}).status_code == 401
