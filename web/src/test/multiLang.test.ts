// Multi-language coding questions, web runtime: language registry, SQL (real sqlite-wasm run
// in Node), TypeScript, the hot spare worker, the grading pool.
import sqlite3InitModule from "@sqlite.org/sqlite-wasm";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { GradingQuestion } from "../api/types";
import { scoreCode } from "../components/teacher/CodeGrading";
import { sqlOutputsMatch } from "../lib/code/compare";
import {
  CODE_LANGUAGES,
  LANGUAGE_LABEL,
  LANGUAGE_RUNTIME,
  TS_NOTE,
  canRunInBrowser,
  indentFor,
  isCodeLanguage,
  runnableLanguages,
} from "../lib/code/languages";
import { PythonRunner } from "../lib/code/pythonRunner";
import type { RunResult, Runner } from "../lib/code/runner";
import { RunnerPool, gradingPoolSize } from "../lib/code/runnerPool";
import { getRunner, resetSharedRunners, setRunnerFactoryForTests, warmUpRunners } from "../lib/code/runners";
import { runTests } from "../lib/code/runTests";
import { formatRows, runSql, type SqliteLike } from "../lib/code/sqlEngine";
import { SqlRunner } from "../lib/code/sqlRunner";
import { TsRunner, stripTypes } from "../lib/code/tsRunner";

let sqlite: SqliteLike;
beforeAll(async () => {
  sqlite = (await (sqlite3InitModule as unknown as (o: object) => Promise<unknown>)({ print: () => undefined, printErr: () => undefined })) as SqliteLike;
}, 30_000);

const sql = (setup: string, code: string) => runSql(sqlite, setup, code, 256 * 1024);

describe("language registry", () => {
  it("labels and runtimes", () => {
    expect(LANGUAGE_LABEL.typescript).toBe("TypeScript");
    expect(LANGUAGE_LABEL.sql).toBe("SQL");
    expect(Object.keys(LANGUAGE_RUNTIME).sort()).toEqual([...CODE_LANGUAGES].sort());
    for (const l of ["python", "javascript", "typescript", "sql"] as const) {
      expect(LANGUAGE_RUNTIME[l]).toBe("browser");
      expect(canRunInBrowser(l)).toBe(true);
    }
    for (const l of ["c", "cpp", "java"] as const) {
      expect(LANGUAGE_RUNTIME[l]).toBe("remote");
      expect(canRunInBrowser(l)).toBe(false);
    }
    expect(runnableLanguages()).toEqual(["python", "javascript", "typescript", "sql"]);
    expect(isCodeLanguage("sql") && isCodeLanguage("typescript") && !isCodeLanguage("ruby")).toBe(true);
    expect(TS_NOTE).toMatch(/not checked/);
    expect(indentFor("sql")).toBe("  ");
    expect(indentFor("python")).toBe("    ");
  });

  it("getRunner makes runners for the new browser languages only", async () => {
    resetSharedRunners();
    expect((await getRunner("sql")!).language).toBe("sql");
    expect((await getRunner("typescript")!).language).toBe("typescript");
    expect(getRunner("java")).toBeNull();
    resetSharedRunners();
  });

  it("warmUpRunners prepares each browser language once", () => {
    const prepared: string[] = [];
    resetSharedRunners();
    const mk = (language: Runner["language"]): Runner => ({
      language,
      prepare: () => (prepared.push(language), Promise.resolve()),
      run: () => Promise.reject(new Error("unused")),
      dispose: () => undefined,
    });
    // the shared runner of each language is created on demand; swap in counting ones
    return import("../lib/code/runners").then(async ({ setRunnerForTests }) => {
      setRunnerForTests("python", mk("python"));
      setRunnerForTests("sql", mk("sql"));
      warmUpRunners(["python", "sql", "python", "java"]);
      await Promise.resolve();
      await Promise.resolve();
      expect(prepared.sort()).toEqual(["python", "sql"]);
      setRunnerForTests("python", null);
      setRunnerForTests("sql", null);
    });
  });
});

describe("SQL engine", () => {
  it("runs setup then the query; header, TAB-joined rows, NULL, numbers, blobs", () => {
    const r = sql(
      "create table t(a integer, b text, c blob, d real); insert into t values (1, 'x', x'00ff', 1.5), (null, null, null, 2);",
      "select a, b, c, d from t order by d;",
    );
    expect(r.failed).toBe(false);
    expect(r.stdout).toBe("a\tb\tc\td\n1\tx\t00ff\t1.5\nNULL\tNULL\tNULL\t2\n");
  });

  it("prints big integers exactly", () => {
    expect(sql("", "select 9007199254740993 as n").stdout).toBe("n\n9007199254740993\n");
  });

  it("prints the last statement that returns rows; later statements without rows do not hide it", () => {
    const r = sql("create table t(a); insert into t values (1),(2);", "select 'first' as x; select a from t; insert into t values (3); create table u(z);");
    expect(r.stdout).toBe("a\n1\n2\n");
  });

  it("the setup's own results are not the output", () => {
    expect(sql("select 1 as setup_col;", "create table q(a);").stdout).toBe("");
  });

  it("a query with no rows still prints the header", () => {
    expect(sql("create table t(a, b);", "select * from t").stdout).toBe("a\tb\n");
  });

  it("semicolons inside strings, triggers and comments", () => {
    const r = sql(
      "create table t(a); create table log(m); create trigger tr after insert on t begin insert into log values ('x;y'); end;",
      "-- note; here\ninsert into t values (1); /* c */ select m from log; -- tail",
    );
    expect(r.stdout).toBe("m\nx;y\n");
  });

  it("reports an error with SQLite's message and no output", () => {
    const r = sql("", "select * from nope");
    expect(r.failed).toBe(true);
    expect(r.stdout).toBe("");
    expect(r.stderr).toBe("Error: no such table: nope\n");
    expect(sql("", "selec 1").stderr).toMatch(/^Error: near "selec": syntax error/);
    expect(sql("create table broken(", "select 1").failed).toBe(true);
  });

  it("every run starts on a fresh database", () => {
    expect(sql("create table keep(a);", "select 1").failed).toBe(false);
    expect(sql("", "select * from keep").stderr).toMatch(/no such table: keep/);
  });

  it("ATTACH and load_extension are refused", () => {
    const a = sql("", "attach database ':memory:' as other");
    expect(a.failed).toBe(true);
    expect(a.stderr).toMatch(/too many attached databases/);
    expect(sql("", "attach database 'file:/etc/passwd?mode=ro' as p").failed).toBe(true);
    const l = sql("", "select load_extension('/tmp/x')");
    expect(l.failed).toBe(true);
    expect(l.stderr).toMatch(/no such function: load_extension/);
  });

  it("cuts the output at the cap", () => {
    const r = runSql(sqlite, "", "with recursive c(n) as (select 1 union all select n+1 from c where n < 5000) select n, 'padding padding' from c", 2000);
    expect(r.truncated).toBe(true);
    expect(r.stdout.length).toBeLessThanOrEqual(2000);
  });

  it("formatRows", () => {
    expect(formatRows(["a", "b"], [[1, null], ["s", new Uint8Array([1, 171])]])).toBe("a\tb\n1\tNULL\ns\t01ab\n");
  });
});

describe("SQL comparison", () => {
  it("unordered: same header and same multiset of rows", () => {
    expect(sqlOutputsMatch("a\n2\n1\n", "a\n1\n2\n")).toBe(true);
    expect(sqlOutputsMatch("a\n1\n1\n2\n", "a\n1\n2\n2\n")).toBe(false);
    expect(sqlOutputsMatch("b\n1\n", "a\n1\n")).toBe(false);
    expect(sqlOutputsMatch("a\n1\n", "a\n1\n2\n")).toBe(false);
    expect(sqlOutputsMatch("a\n2   \n1\n\n", "a\n1\n2")).toBe(true);
  });
  it("ordered: exact line order", () => {
    expect(sqlOutputsMatch("a\n2\n1\n", "a\n1\n2\n", "ordered")).toBe(false);
    expect(sqlOutputsMatch("a\n1\n2\n", "a\n1\n2", "ordered")).toBe(true);
  });
});

/** A runner that executes SQL for real (engine in this process), as the worker would. */
function engineRunner(): Runner {
  return {
    language: "sql",
    prepare: () => Promise.resolve(),
    run: async (code, setup) => {
      const r = runSql(sqlite, setup, code, 256 * 1024);
      return { status: r.failed ? "error" : "ok", stdout: r.stdout, stderr: r.stderr, timeMs: 1 };
    },
    dispose: () => undefined,
  };
}

describe("SQL tests through runTests", () => {
  const tests = [{ input: "", expected: "name\nAda\nBo\n" }];
  const setup = "create table p(name); insert into p values ('Bo'),('Ada');";
  it("unordered by default, ordered when asked", async () => {
    const q = "select name from p";
    expect((await runTests(engineRunner(), q, tests, 1000, undefined, undefined, { sqlSetup: setup }))[0]!.status).toBe("pass");
    expect((await runTests(engineRunner(), q, tests, 1000, undefined, undefined, { sqlSetup: setup, sqlCompare: "ordered" }))[0]!.status).toBe("fail");
    expect((await runTests(engineRunner(), q + " order by name", tests, 1000, undefined, undefined, { sqlSetup: setup, sqlCompare: "ordered" }))[0]!.status).toBe("pass");
  });
  it("a SQL error is an error outcome with the message", async () => {
    const o = (await runTests(engineRunner(), "select * from missing", tests, 1000, undefined, undefined, { sqlSetup: setup }))[0]!;
    expect(o.status).toBe("error");
    expect(o.stderr).toContain("no such table: missing");
  });
});

type Listener = (ev: { data: unknown }) => void;

interface FakeWorker {
  onmessage: Listener | null;
  terminated: boolean;
  reply(data: unknown): void;
}

function fakeWorkers(onRun: (w: FakeWorker, m: { id: number }) => void) {
  const made: FakeWorker[] = [];
  class Fake implements FakeWorker {
    onmessage: Listener | null = null;
    onerror: ((ev: unknown) => void) | null = null;
    terminated = false;
    constructor() {
      made.push(this);
    }
    reply(data: unknown) {
      if (!this.terminated) queueMicrotask(() => this.onmessage?.({ data }));
    }
    postMessage(m: { type: string; id: number }) {
      if (m.type === "load") this.reply({ type: "ready" });
      if (m.type === "run") onRun(this, m);
    }
    terminate() {
      this.terminated = true;
    }
  }
  vi.stubGlobal("Worker", Fake);
  return made;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  setRunnerFactoryForTests("python", null);
});

describe("SQL runner (page side)", () => {
  it("returns the worker's result", async () => {
    const made = fakeWorkers((w, m) => w.reply({ type: "done", id: m.id, stdout: "a\n1\n", stderr: "", failed: false, timeMs: 2 }));
    const r = new SqlRunner();
    const out = await r.run("select 1 as a", "", 1000);
    expect(out).toMatchObject({ status: "ok", stdout: "a\n1\n" });
    expect(made).toHaveLength(1);
    r.dispose();
  });

  it("a query past the limit is stopped by terminating the worker, and a new one starts at once", async () => {
    const made = fakeWorkers(() => undefined);
    const r = new SqlRunner();
    const out = await r.run("with recursive c as (select 1 union all select 1 from c) select * from c", "", 40);
    expect(out.status).toBe("timeout");
    expect(made[0]!.terminated).toBe(true);
    expect(made).toHaveLength(2);
    expect(made[1]!.terminated).toBe(false);
    r.dispose();
    expect(made[1]!.terminated).toBe(true);
    expect(made).toHaveLength(2);
  });
});

describe("Python hot spare", () => {
  it("a run that reports restart (os._exit, files changed) respawns the worker immediately", async () => {
    let first = true;
    const made = fakeWorkers((w, m) => {
      w.reply({ type: "started", id: m.id });
      w.reply({ type: "ended", id: m.id });
      w.reply({ type: "done", id: m.id, stdout: "", stderr: "", failed: false, timeMs: 1, restart: first });
      first = false;
    });
    const r = new PythonRunner();
    await r.run("import os; os._exit(0)", "", 1000);
    await new Promise((res) => setTimeout(res, 0));
    expect(made).toHaveLength(2);
    expect(made[0]!.terminated).toBe(true);
    const ok = await r.run("print(1)", "", 1000);
    expect(ok.status).toBe("ok");
    expect(made).toHaveLength(2); // the spare was used, nothing new started
    r.dispose();
    expect(made).toHaveLength(2); // disposing does not start another
  });

  it("a timeout respawns the worker", async () => {
    let calls = 0;
    const made = fakeWorkers((w, m) => {
      w.reply({ type: "started", id: m.id });
      if (calls++ === 0) return; // runs forever
      w.reply({ type: "ended", id: m.id });
      w.reply({ type: "done", id: m.id, stdout: "2\n", stderr: "", failed: false, timeMs: 1 });
    });
    const r = new PythonRunner();
    const t = await r.run("while True: pass", "", 30);
    expect(t.status).toBe("timeout");
    expect(made).toHaveLength(2);
    const ok = await r.run("print(2)", "", 1000);
    expect(ok).toMatchObject({ status: "ok", stdout: "2\n" });
    expect(made).toHaveLength(2);
    r.dispose();
  });
});

describe("TypeScript runner", () => {
  it("strips types and keeps line numbers", async () => {
    const out = await stripTypes("interface P { a: number }\nconst x: number = 1;\nfunction f<T>(v: T): T { return v }\nconsole.log(f<number>(x) as number);\n");
    expect(out).not.toMatch(/interface|: number|<T>|as number/);
    expect(out.split("\n")).toHaveLength(5);
  });

  it("runs the stripped code in the JavaScript runner", async () => {
    let seen = "";
    const js: Runner = {
      language: "javascript",
      prepare: () => Promise.resolve(),
      run: (code): Promise<RunResult> => ((seen = code), Promise.resolve({ status: "ok", stdout: "1\n", stderr: "", timeMs: 1 })),
      dispose: () => undefined,
    };
    const r = new TsRunner(js);
    expect((await r.run("let a: string = 'x'; console.log(a)", "", 1000)).status).toBe("ok");
    expect(seen).not.toMatch(/: string/);
    expect(r.language).toBe("typescript");
  });

  it("a syntax error is a compile error and the program does not run", async () => {
    const run = vi.fn();
    const js: Runner = { language: "javascript", prepare: () => Promise.resolve(), run, dispose: () => undefined };
    const out = await new TsRunner(js).run("const = ;", "", 1000);
    expect(out.status).toBe("error");
    expect(out.stderr).toMatch(/^SyntaxError: /);
    expect(run).not.toHaveBeenCalled();
  });

  it("type errors are not reported (no type checking)", async () => {
    const out = await stripTypes("const n: number = 'text';");
    expect(out).toContain("'text'");
  });
});

describe("grading pool", () => {
  it("size is min(4, cores / 2), at least 1", () => {
    expect(gradingPoolSize(1)).toBe(1);
    expect(gradingPoolSize(2)).toBe(1);
    expect(gradingPoolSize(6)).toBe(3);
    expect(gradingPoolSize(8)).toBe(4);
    expect(gradingPoolSize(64)).toBe(4);
    expect(gradingPoolSize(0)).toBe(1);
  });

  function countingPool(size: number) {
    const made: Runner[] = [];
    let live = 0;
    let peak = 0;
    const pool = new RunnerPool("python", size, () => {
      const r: Runner = { language: "python", prepare: () => Promise.resolve(), run: () => Promise.resolve({ status: "ok", stdout: "", stderr: "", timeMs: 0 }), dispose: vi.fn() };
      made.push(r);
      return r;
    });
    const use = async () => {
      const r = await pool.acquire();
      live += 1;
      peak = Math.max(peak, live);
      await new Promise((res) => setTimeout(res, 5));
      live -= 1;
      pool.release(r);
    };
    return { pool, made, use, peak: () => peak };
  }

  it("never has more runners than its size, reuses them, and waits when all are busy", async () => {
    const c = countingPool(3);
    await Promise.all(Array.from({ length: 12 }, c.use));
    expect(c.peak()).toBe(3);
    expect(c.made).toHaveLength(3);
    c.pool.dispose();
    for (const r of c.made) expect(r.dispose).toHaveBeenCalled();
  });

  it("a recycled runner is disposed and replaced", async () => {
    const c = countingPool(1);
    const a = await c.pool.acquire();
    c.pool.release(a, true);
    expect(a.dispose).toHaveBeenCalled();
    const b = await c.pool.acquire();
    expect(b).not.toBe(a);
    c.pool.dispose();
  });

  it("dispose rejects waiting acquirers", async () => {
    const c = countingPool(1);
    await c.pool.acquire();
    const waiting = c.pool.acquire();
    c.pool.dispose();
    await expect(waiting).rejects.toThrow("Stopped.");
  });

  const q: GradingQuestion = {
    question_id: "q",
    index: 0,
    type: "code",
    prompt: "p",
    points: 10,
    rubric: [],
    to_grade: 1,
    graded: 0,
    language: "python",
    tests: [
      { input: "1", expected: "1", visible: true, points: 4 },
      { input: "2", expected: "2", visible: false, points: 6 },
    ],
  } as unknown as GradingQuestion;

  it("scoring through a pool gives the same result as a runner per answer", async () => {
    const echo = (): Runner => ({
      language: "python",
      prepare: () => Promise.resolve(),
      run: (code, stdin) => Promise.resolve({ status: "ok", stdout: code.includes("bad") && stdin === "2" ? "9" : stdin, stderr: "", timeMs: 1 }),
      dispose: () => undefined,
    });
    setRunnerFactoryForTests("python", echo);
    const pool = new RunnerPool("python", 2);
    const answers = ["good", "bad", "good", "bad", "good"];
    const direct = await Promise.all(answers.map((a) => scoreCode(q, a)));
    const pooled = await Promise.all(answers.map((a) => scoreCode(q, a, undefined, undefined, pool)));
    expect(pooled.map((s) => [s.points, s.comment])).toEqual(direct.map((s) => [s.points, s.comment]));
    expect(pooled.map((s) => s.points)).toEqual([10, 4, 10, 4, 10]);
    expect(pooled.map((s) => s.outcomes.map((o) => o.status))).toEqual(direct.map((s) => s.outcomes.map((o) => o.status)));
    pool.dispose();
  });

  it("SQL grading uses the question's setup and compare mode", async () => {
    setRunnerFactoryForTests("sql", engineRunner);
    const sq = {
      ...q,
      language: "sql",
      sql_setup: "create table p(n); insert into p values (2),(1);",
      sql_compare: "ordered",
      tests: [{ input: "", expected: "n\n1\n2\n", visible: false, points: 10 }],
    } as unknown as GradingQuestion;
    expect((await scoreCode(sq, "select n from p order by n")).points).toBe(10);
    expect((await scoreCode(sq, "select n from p")).points).toBe(0);
    expect((await scoreCode({ ...sq, sql_compare: "unordered" } as GradingQuestion, "select n from p")).points).toBe(10);
    setRunnerFactoryForTests("sql", null);
  });
});
