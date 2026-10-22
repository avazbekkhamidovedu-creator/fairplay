// @vitest-environment node
// 2026-10-23 (review fixes): one Python run cannot change what the next run sees, and the
// program has no way back to JavaScript. Runs the real Pyodide (from node_modules) in Node.
import { beforeAll, describe, expect, it } from "vitest";
import { createPySession, type PyodideLike, type PySession } from "../lib/code/pyDriver";

// the shared test setup clears localStorage after each test; Node has none
(globalThis as { localStorage?: unknown }).localStorage ??= { clear: () => undefined };

let session: PySession;
let py: PyodideLike;

beforeAll(async () => {
  const mod = (await import("pyodide")) as unknown as {
    loadPyodide: (o: { indexURL: string; fullStdLib?: boolean }) => Promise<PyodideLike>;
  };
  const indexURL = new URL("../../node_modules/pyodide/", import.meta.url).pathname;
  py = await mod.loadPyodide({ indexURL, fullStdLib: false });
  session = createPySession(py);
}, 120_000);

const run = (code: string, stdin = "") => session.run(code, stdin, 10_000);

describe("Python runs are isolated from each other", () => {
  it("runs a normal program with input", () => {
    const r = run("n = int(input())\nprint(n * 2)", "21\n");
    expect(r).toMatchObject({ stdout: "42\n", failed: false, restart: false });
  });

  it("a run that replaces the driver does not change the next result (the reviewer's attack)", () => {
    const a = run(`import __main__\n__main__.__fairplay_run = lambda src: (print("42"), 0)[1]\nprint("A done")`);
    expect(a.stdout).toBe("A done\n");
    expect(run("print(int(input()) * 2)", "5\n").stdout).toBe("10\n");
  });

  it("builtins, sys streams, modules and sys.path are back to normal next run", () => {
    run(`
import builtins, sys, math, io
builtins.print = lambda *a, **k: sys.__stdout__.write("HACKED\\n")
builtins.input = lambda *a: "999"
builtins.int = lambda *a: 7
math.pi = 3
sys.stdout = io.StringIO()
sys.__stdout__.write = lambda s: 0
sys.path.insert(0, "/evil")
sys.modules["json"] = None
sys.setrecursionlimit(50)
`);
    const r = run(`
import math, json, sys
print(int(input()) + 1, math.pi > 3.14, json.dumps([1]), "/evil" in sys.path, sys.getrecursionlimit() > 50)`, "1\n");
    expect(r.stdout).toBe("2 True [1] False True\n");
  });

  it("a trace function or a new module from one run is gone in the next", () => {
    run(`
import sys
def tr(frame, event, arg):
    if event == "call":
        sys.__stdout__.write("TRACED\\n")
    return None
sys.settrace(tr)
sys.setprofile(lambda *a: None)
import types
m = types.ModuleType("helper")
m.answer = 41
sys.modules["helper"] = m
`);
    const r = run(`
def f():
    return 1
f()
try:
    import helper
    print("helper still there")
except ImportError:
    print("clean")`);
    expect(r.stdout).toBe("clean\n");
  });

  it("files a program writes are removed; a module file cannot shadow the standard library", () => {
    run(`
open("random.py", "w").write("def randint(a, b):\\n    return 4\\n")
open("/tmp/note.txt", "w").write("hi")
import os
os.makedirs("/home/pyodide/sub/dir", exist_ok=True)
`);
    const r = run(`
import os, random
print(os.path.exists("/tmp/note.txt"), os.path.exists("random.py"), os.path.exists("/home/pyodide/sub"), hasattr(random, "shuffle"))`);
    expect(r.stdout).toBe("False False False True\n");
    expect(r.restart).toBe(false);
  });

  it("audit hooks cannot be installed", () => {
    const r = run(`import sys\nsys.addaudithook(lambda e, a: print("seen", e))`);
    expect(r.failed).toBe(true);
    expect(r.stderr).toMatch(/not available in the exam/);
    expect(run(`print("ok")`).stdout).toBe("ok\n");
  });

  it("has no way back to JavaScript", () => {
    for (const mod of ["js", "pyodide_js", "pyodide", "pyodide.ffi", "pyodide.code", "_pyodide", "_pyodide_core"]) {
      const r = run(`import ${mod}`);
      expect(r.failed, mod).toBe(true);
      expect(r.stderr, mod).toMatch(/ImportError|ModuleNotFoundError/);
    }
    const r = run(`
import sys, importlib
print(any("Js" in type(f).__name__ for f in sys.meta_path))
try:
    importlib.import_module("js")
    print("open")
except ImportError:
    print("blocked")`);
    expect(r.stdout).toBe("False\nblocked\n");
  });

  it("everyday modules still work", () => {
    const r = run(`
import math, random, re, json, itertools, collections, heapq, bisect, functools, string, statistics, fractions, decimal, datetime
print(math.gcd(12, 18), sorted(collections.Counter("aab").items()), statistics.mean([1, 2, 3]), fractions.Fraction(1, 3) * 3)
print(decimal.Decimal("0.1") + decimal.Decimal("0.2"), re.sub(r"\\d", "#", "a1b2"), list(itertools.permutations([1, 2])))`);
    expect(r.failed, r.stderr).toBe(false);
    expect(r.stdout).toBe("6 [('a', 2), ('b', 1)] 2 1\n0.3 a#b# [(1, 2), (2, 1)]\n");
  });

  it("still reports errors and exit codes", () => {
    const e = run("x = 1/0");
    expect(e.failed).toBe(true);
    expect(e.stderr).toMatch(/ZeroDivisionError/);
    expect(e.stderr).not.toMatch(/__fairplay_run/);
    expect(run("import sys\nprint('a')\nsys.exit(0)")).toMatchObject({ stdout: "a\n", failed: false });
    expect(run("import sys\nsys.exit(3)").failed).toBe(true);
    expect(run("print(")).toMatchObject({ failed: true });
    expect(run("print('é ✓')").stdout).toBe("é ✓\n");
  });

  it("a session made on a fresh Pyodide sees nothing of the last one", () => {
    // what grading does: a fresh worker per answer (here: a fresh session state check)
    const r = run("print(len([k for k in globals() if not k.startswith('__')]))");
    expect(r.stdout).toBe("0\n");
  });
});
