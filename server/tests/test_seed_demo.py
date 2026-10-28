import importlib.util
import os
import sqlite3

import pytest

SPEC = importlib.util.spec_from_file_location(
    "seed_demo", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "seed_demo.py"))
seed_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seed_demo)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("PROCTORA_SECRET", "demo-secret-0123456789abcdef")
    monkeypatch.setenv("FAIRPLAY_DEV", "1")
    monkeypatch.setenv("FAIRPLAY_UPLOADS_DIR", str(tmp_path / "up"))
    monkeypatch.delenv("DATABASE_URL", raising=False)


def test_refuses_urls_and_existing_files(tmp_path):
    for bad in ("postgresql://u:p@h/db", "sqlite+aiosqlite:///x.db", "sqlite:///x.db"):
        with pytest.raises(SystemExit):
            seed_demo.main(["--db", bad, "--pdf", str(tmp_path / "c.pdf")])
    existing = tmp_path / "prod.db"
    existing.write_bytes(b"data")
    with pytest.raises(SystemExit):
        seed_demo.main(["--db", str(existing), "--pdf", str(tmp_path / "c.pdf")])
    assert existing.read_bytes() == b"data"
    with pytest.raises(SystemExit):
        seed_demo.main(["--db", str(tmp_path / "missing" / "x.db"), "--pdf", str(tmp_path / "c.pdf")])


def test_seeds_the_demo_exam_with_codes_and_a_sheet(env, tmp_path, capsys):
    db, pdf = tmp_path / "demo.db", tmp_path / "codes.pdf"
    assert seed_demo.main(["--db", str(db), "--pdf", str(pdf), "--opens-in-minutes", "5", "--seed", "1"]) == 0
    out = capsys.readouterr().out
    assert "Demo exam: access codes" in out and "小明" in out and "Mary Ann" in out
    assert pdf.read_bytes().startswith(b"%PDF")
    con = sqlite3.connect(db)
    title, status, source, count, sched = con.execute(
        "select title, status, source, question_count, scheduled_at from exams where external_key = ?",
        (seed_demo.KEY,)).fetchone()
    assert (title, status, source, count) == ("Demo exam: access codes", "draft", "dkuoj", 6)
    assert con.execute("select count(*) from exam_access_codes").fetchone()[0] == 12
    assert con.execute("select count(*) from oj_roster").fetchone()[0] == 12
    types = [q["type"] for q in __import__("json").loads(
        con.execute("select questions from exams where external_key = ?", (seed_demo.KEY,)).fetchone()[0])]
    assert sorted(types) == ["code", "fill_blank", "mcq", "mcq", "short", "short"]
    assert con.execute("select count(*) from users where username = 'teacher'").fetchone()[0] == 1
    for line in out.splitlines():
        if line.startswith("xw"):
            assert len(line.split()[-1]) == 6
    with pytest.raises(SystemExit):
        seed_demo.main(["--db", str(db), "--pdf", str(tmp_path / "again.pdf")])
