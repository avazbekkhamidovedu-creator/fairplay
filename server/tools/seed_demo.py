#!/usr/bin/env python3
"""Local demo data for the access-code sign-in, with no DKU OJ.

    PROCTORA_SECRET=<same value as the server> python3 server/tools/seed_demo.py \\
        --db /tmp/demo/fairplay.db --pdf /tmp/demo/codes.pdf [--opens-in-minutes 2]

Makes a NEW SQLite file (an existing file, or anything that is not a plain path, is refused)
with the demo accounts, a DKU-style exam "Demo exam: access codes" (six questions, 12 fake
students, one access code each) that is still a draft and starts in N minutes. Everything goes
through the app's own models and sync code (apply_listing, ensure_codes).

The exam opens by itself at its start: the running server's DKU OJ loop does it
(sync.open_due, every 5 seconds). That loop only runs while the server counts DKU OJ as
connected, so start the server with DKUOJ_URL and DKUOJ_TOKEN set to anything on loopback
(tools/run_demo.sh does); the sync calls to that address fail and are only logged. The
PROCTORA_SECRET must be the server's: the codes are sealed and looked up with it.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

KEY = "demo-access-codes"
TITLE = "Demo exam: access codes"
PACKAGE_VERSION = "demo1"

STUDENTS = [
    ("Emma", "Johnson"), ("Liam", "Chen"), ("Mary Ann", "Smith"), ("小明", "王"), ("Sofia", "Garcia"),
    ("Noah", "Patel"), ("Aisha", "Rahman"), ("Daniel", "Kim"), ("Olivia", "Brown"), ("Mateo", "Rossi"),
    ("Fatima", "Zahra"), ("Lukas", "Weber"),
]

CHOICES = [
    ("Which structure finds a value by its key in O(1) on average?", ["Array", "Linked list", "Hash table", "Binary heap"]),
    ("What is the running time of binary search on a sorted array of n items?", ["O(1)", "O(log n)", "O(n)", "O(n log n)"]),
    ("Which keyword starts a function definition in Python?", ["func", "def", "fn", "lambda"]),
    ("Which HTTP status code means the page was not found?", ["200", "301", "404", "500"]),
    ("Which of these is not a sorting algorithm?", ["Merge sort", "Quick sort", "Dijkstra", "Insertion sort"]),
]


def package(rng: random.Random) -> dict:
    a, b = rng.randint(12, 49), rng.randint(12, 49)
    mcq = rng.sample(CHOICES, 2)

    def choice(order: int, text: str, options: list[str]) -> dict:
        return {"id": f"p{order:06d}", "order": order, "label": f"Q{order}", "title": f"Question {order}",
                "type": "choice", "points": 10, "partial": False, "statement_md": text, "statement_html": None,
                "choice": {"multiple": False, "true_false": False, "shuffle": True,
                           "options": [{"id": order * 10 + i, "text_md": o} for i, o in enumerate(options)]}}

    questions = [
        choice(1, *mcq[0]),
        choice(2, *mcq[1]),
        {"id": "p000003", "order": 3, "label": "Q3", "title": "Arithmetic", "type": "fill_blank", "points": 10,
         "partial": False, "statement_md": f"What is {a} x {b}? Answer: ___",
         "fill_blank": {"blanks": [{"index": 0, "points": 10}], "case_sensitive": False}},
        {"id": "p000004", "order": 4, "label": "Q4", "title": "Short answer", "type": "short_answer", "points": 10,
         "partial": True, "statement_md": "In one sentence, what does a hash function do?", "statement_html": None,
         "short_answer": {}},
        {"id": "p000005", "order": 5, "label": "Q5", "title": "Explain", "type": "short_answer", "points": 20,
         "partial": True, "statement_md": "Explain in a short paragraph why a list must be sorted before binary "
                                           "search works, and what happens if it is not.",
         "statement_html": None, "short_answer": {}},
        {"id": "p000006", "order": 6, "label": "Q6", "title": "Reverse a list", "type": "code", "points": 40,
         "partial": True, "statement_md": "Write `reverse(items)` that returns the list in reverse order.",
         "code": {"languages": [{"key": "PY3", "name": "Python 3"}, {"key": "CPP17", "name": "C++17"}],
                  "time_limit_s": 2.0, "memory_limit_kb": 262144,
                  "templates": {"PY3": "def reverse(items):\n    pass\n",
                                "CPP17": "#include <vector>\nstd::vector<int> reverse(std::vector<int> items) {\n}\n"},
                  "samples": [{"input": "1 2 3\n", "output": "3 2 1\n"}]}},
    ]
    return {"key": KEY, "package_version": PACKAGE_VERSION, "total_points": 100,
            "description_md": "Demo exam. Closed book.", "questions": questions, "assets": []}


def netids(rng: random.Random) -> list[str]:
    taken, out = set(), []
    for first, last in STUDENTS:
        initials = ("".join(ch for ch in first.lower() if ch.isascii() and ch.isalpha())[:1] or "x") + \
                   ("".join(ch for ch in last.lower() if ch.isascii() and ch.isalpha())[:1] or "w")
        while True:
            n = f"{initials}{rng.randint(10, 999)}"
            if n not in taken:
                taken.add(n)
                out.append(n)
                break
    return out


def sqlite_path(db: str) -> str:
    if "://" in db or db.startswith("sqlite"):
        raise SystemExit("--db must be a plain path to a NEW SQLite file, not a database URL")
    path = os.path.abspath(db)
    if os.path.exists(path):
        raise SystemExit(f"{path} already exists. Use a new file (this tool never touches an existing database).")
    if not os.path.isdir(os.path.dirname(path)):
        raise SystemExit(f"the folder {os.path.dirname(path)} does not exist")
    return path


async def seed(settings, args) -> list[dict]:
    from sqlalchemy import select, update

    from app.db import get_database
    from app.integrations.dkuoj import access_codes, access_codes_pdf
    from app.integrations.dkuoj.adapter import questions_from_package
    from app.integrations.dkuoj.source import SOURCE
    from app.integrations.dkuoj.sync import apply_listing
    from app.main import create_app
    from app.models import Exam, User
    from app.timeutil import iso, utcnow

    from app.integrations.dkuoj import sync as ojsync
    ojsync.BACKGROUND = False       # this process only seeds; the server's own loop opens the exam
    rng = random.Random(args.seed)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        db = get_database()
        now = utcnow()
        start = now + timedelta(minutes=args.opens_in_minutes)
        ids = netids(rng)
        pkg = package(rng)
        async with db.sessionmaker() as s:
            await s.execute(update(User).where(User.username == "teacher", User.netid.is_(None)).values(netid="teacher"))
            await s.commit()
            item = {
                "key": KEY, "name": TITLE, "course": {"id": 1, "name": "Demo course"},
                "classes": [{"id": 1, "name": "Demo section A"}],
                "audience": {"mode": "classes", "confirmed": True, "class_ids": [1], "organization_ids": [],
                             "selected_only": False},
                "start_time": iso(start), "end_time": iso(start + timedelta(hours=3)),
                "time_limit_seconds": 5400, "results_hidden": True, "package_version": PACKAGE_VERSION,
                "removed": False, "updated_at": iso(now),
                "editors": [{"username": "teacher", "first_name": "Demo", "last_name": "Teacher"}],
                "roster": [{"username": n, "first_name": f, "last_name": l, "email": f"{n}@demo.test",
                            "extra_time_seconds": 0} for n, (f, l) in zip(ids, STUDENTS)],
            }
            e = await apply_listing(s, item, SOURCE.config, now)
            await s.commit()
            questions = questions_from_package(pkg, "demo")
            for q in questions:
                if q.get("external_ref", {}).get("question_id") == "p000005":
                    q["answer_lines"] = 8
            e = await s.get(Exam, e.id)
            e.questions, e.question_count = questions, len(questions)
            e.total_points = float(pkg["total_points"])
            e.external_version = PACKAGE_VERSION
            e.external_meta = {**(e.external_meta or {}), "package_version": PACKAGE_VERSION,
                               "description_md": pkg["description_md"], "assets": []}
            await s.commit()
            sheet = await access_codes.ensure_codes(s, app.state.settings.secret, e)
            rows = sorted(({"last": r.last_name, "first": r.first_name, "netid": r.netid, "code": code}
                           for r, code in sheet.rows), key=lambda x: (x["last"].casefold(), x["first"].casefold()))
            pdf = access_codes_pdf.render(e.title, e.course, utcnow(), rows)
            with open(args.pdf, "wb") as f:
                f.write(pdf)
            exam_id = e.id
        return [{"exam_id": exam_id, "start": start, **r} for r in rows]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Seed a NEW SQLite file with the access-code demo exam.")
    ap.add_argument("--db", required=True, help="path of a new SQLite file")
    ap.add_argument("--pdf", required=True, help="where to write the access-code sheet")
    ap.add_argument("--opens-in-minutes", type=float, default=2.0, help="scheduled start from now (default 2)")
    ap.add_argument("--seed", type=int, default=None, help="random seed (same questions and NetIDs)")
    args = ap.parse_args(argv)
    path = sqlite_path(args.db)
    if os.path.exists(args.pdf):
        raise SystemExit(f"{args.pdf} already exists")

    from app.config import Settings
    settings = Settings()
    settings.database_url = f"sqlite+aiosqlite:///{path}"
    settings.redis_url = None
    settings.seed_demo = True
    settings.single_process = True
    settings.require_app = False
    rows = asyncio.run(seed(settings, args))

    print(f"Exam: {TITLE}   id {rows[0]['exam_id']}   starts {rows[0]['start']:%H:%M:%S} UTC")
    print(f"{'NetID':<8} {'Name':<22} Code")
    for r in rows:
        print(f"{r['netid']:<8} {(r['first'] + ' ' + r['last']):<22} {r['code']}")
    print(f"Sheet: {args.pdf}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
