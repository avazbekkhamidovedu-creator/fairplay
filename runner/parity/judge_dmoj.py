#!/usr/bin/env python3
"""Judge every answer on the real DMOJ judge (the judge DKU OJ runs) and write the baseline.

Run with the judge's own Python, as the unprivileged judge user (DMOJ refuses to run as root):

    setpriv --reuid=classroom-judge --regid=classroom-judge --clear-groups \
        env -u JAVA_TOOL_OPTIONS HOME=/srv/classroom-judge \
        /srv/classroom-judge/venv/bin/python judge_dmoj.py --config parity-judge.yml \
        --kit /srv/classroom-judge/parity --out /srv/classroom-judge/parity/results/dmoj-baseline.json

One answer at a time, so timings are not disturbed by other runs. The output is the per-test
verdict, points, time and memory for every answer, plus the runtime versions used.
"""
import argparse
import json
import os
import platform
import sys
import time

LANG = {".py": "PY3", ".cpp": "CPP17", ".java": "JAVA"}


class Recorder:
    """Stands in for DMOJ's network packet manager and keeps what the judge reports."""

    def __init__(self, judge):
        self.judge = judge
        self.reset()

    def reset(self):
        self.cases, self.compile_error, self.internal_error, self.compile_message = [], None, None, None

    def test_case_status_packet(self, position, result):
        self.cases.append({
            "case": position,
            "verdict": result.readable_codes()[0],
            "codes": result.readable_codes(),
            "points": result.points,
            "total": result.total_points,
            "time_s": round(result.execution_time, 4),
            "wall_s": round(result.wall_clock_time, 4),
            "memory_kb": result.max_memory,
            "feedback": result.feedback,
            "runtime_version": result.runtime_version,
        })

    def compile_error_packet(self, log):
        self.compile_error = log

    def compile_message_packet(self, log):
        self.compile_message = log

    def internal_error_packet(self, message):
        self.internal_error = message

    def __getattr__(self, name):
        # every other packet (begin/end grading, batches, acknowledgements) is not needed here
        if name.endswith("_packet") or name in ("run", "close", "_receive_packet"):
            return lambda *a, **k: None
        raise AttributeError(name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--kit", required=True, help="folder with problems/ and answers/")
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="", help="substring filter on problem/answer")
    args = ap.parse_args()

    sys.argv = ["dmoj-cli", "-c", args.config, "--no-ansi"]
    from dmoj import judgeenv, executors, contrib
    from dmoj.judge import Judge, Submission
    import yaml

    judgeenv.load_env(cli=True)
    # Ubuntu's OpenJDK reads its security config from /etc/java-*-openjdk (symlinked from the JDK).
    # extra_fs in the config covers running; the compiler sandbox has its own list, so add it there.
    # (DKU's judge image may not need this; it is an environment difference to record, not a verdict one.)
    import glob
    from dmoj.cptbox.filesystem_policies import RecursiveDir
    from dmoj.executors import java_executor
    java_executor.JavacExecutor.compiler_read_fs = [RecursiveDir(d) for d in glob.glob("/etc/java-*-openjdk")]
    executors.load_executors()
    contrib.load_contrib_modules()

    judge = Judge.__new__(Judge)
    rec = Recorder(judge)
    Judge.__init__(judge, rec)

    versions = {}
    for lang in sorted(set(LANG.values())):
        ex = executors.executors.get(lang)
        if ex is None:
            versions[lang] = "NOT AVAILABLE"
            continue
        try:
            versions[lang] = [(name, ".".join(map(str, v))) for name, v in ex.Executor.get_runtime_versions()]
        except Exception as e:  # pragma: no cover
            versions[lang] = f"unknown ({e})"

    results = []
    answers_root = os.path.join(args.kit, "answers")
    sub_id = 0
    for pid in sorted(os.listdir(answers_root)):
        meta = yaml.safe_load(open(os.path.join(args.kit, "problems", pid, "meta.yml")))
        for fname in sorted(os.listdir(os.path.join(answers_root, pid))):
            if args.only and args.only not in f"{pid}/{fname}":
                continue
            lang = LANG[os.path.splitext(fname)[1]]
            if lang not in executors.executors:
                results.append({"problem": pid, "answer": fname, "language": lang, "worst": "NOT AVAILABLE"})
                continue
            src = open(os.path.join(answers_root, pid, fname)).read()
            rec.reset()
            sub_id += 1
            t0 = time.time()
            judge.begin_grading(
                Submission(sub_id, pid, lang, src, float(meta["time_limit_s"]), int(meta["memory_limit_mb"]) * 1024,
                           False, {}),
                blocking=True, report=lambda *_: None,
            )
            elapsed = time.time() - t0
            if rec.compile_error is not None:
                worst = "CE"
            elif rec.internal_error is not None:
                worst = "IE"
            else:
                order = ["IE", "TLE", "MLE", "OLE", "RTE", "IR", "WA", "SC"]
                seen = [c["verdict"] for c in rec.cases]
                worst = next((v for v in order if v in seen), "AC")
            row = {
                "problem": pid, "answer": fname, "language": lang, "worst": worst,
                "points": round(sum(c["points"] for c in rec.cases), 3),
                "total": round(sum(c["total"] for c in rec.cases), 3) if rec.cases else 100,
                "cases": rec.cases, "judge_seconds": round(elapsed, 2),
            }
            if rec.compile_error is not None:
                row["compile_error"] = rec.compile_error[:2000]
            if rec.internal_error is not None:
                row["internal_error"] = rec.internal_error[:2000]
            results.append(row)
            print(f"{pid:14} {fname:26} {lang:6} {worst:4} {row['points']:6}/{row['total']:<4} {elapsed:6.2f}s",
                  flush=True)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump({
            "judge": "DMOJ judge-server (dmoj on PyPI), the judge DKU OJ runs",
            "dmoj_version": __import__("dmoj").__version__ if hasattr(__import__("dmoj"), "__version__") else "?",
            "host": platform.platform(),
            "runtimes": versions,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "results": results,
        }, f, indent=1)
    print("wrote", args.out)


if __name__ == "__main__":
    main()
