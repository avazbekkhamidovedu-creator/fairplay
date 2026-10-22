// 2026-10-23 (review fixes): the runner workers drop network / storage globals before the
// student's program runs, and grading uses a fresh runner per answer.
import { runInNewContext } from "node:vm";
import { afterEach, describe, expect, it } from "vitest";
import { buildJsWorkerSource } from "../lib/code/jsRunner";
import type { RunResult, Runner } from "../lib/code/runner";
import { createRunner, getRunner, setRunnerFactoryForTests, setRunnerForTests } from "../lib/code/runners";
import { BLOCKED_GLOBALS, lockGlobals } from "../lib/code/sandboxGlobals";
import { scoreCode } from "../components/teacher/CodeGrading";
import type { GradingQuestion } from "../api/types";

/** A scope shaped like a worker: the APIs sit on prototypes, as in WorkerGlobalScope. */
function workerLike(): Record<string, unknown> {
  const eventTarget = { addEventListener: () => undefined };
  const wgs = Object.create(eventTarget) as Record<string, unknown>;
  wgs.fetch = () => "fetched";
  wgs.importScripts = () => "imported";
  wgs.indexedDB = { open: () => "db" };
  const scope = Object.create(wgs) as Record<string, unknown>;
  scope.XMLHttpRequest = function XMLHttpRequest() {};
  scope.WebSocket = function WebSocket() {};
  scope.Worker = function Worker() {};
  scope.navigator = Object.create({ sendBeacon: () => true, storage: {} });
  return scope;
}

describe("worker globals", () => {
  it("lockGlobals removes them from the scope and its prototypes", () => {
    const scope = workerLike();
    const proto = Object.getPrototypeOf(scope) as Record<string, unknown>;
    lockGlobals(scope);
    for (const name of ["fetch", "importScripts", "indexedDB", "XMLHttpRequest", "WebSocket", "Worker"]) {
      expect(scope[name], name).toBeUndefined();
    }
    expect(proto.fetch).toBeUndefined(); // WorkerGlobalScope.prototype.fetch.call(self, …) is gone too
    expect((scope.navigator as Record<string, unknown>).sendBeacon).toBeUndefined();
    expect(() => {
      scope.fetch = () => "again"; // locked: cannot be put back
    }).toThrow(TypeError);
    expect(scope.fetch).toBeUndefined();
    expect(BLOCKED_GLOBALS).toEqual(expect.arrayContaining(["fetch", "XMLHttpRequest", "WebSocket", "indexedDB", "importScripts"]));
  });

  it("the JavaScript program sees none of them", async () => {
    const code = [
      "const names = ['fetch', 'XMLHttpRequest', 'WebSocket', 'indexedDB', 'importScripts', 'Worker', 'EventSource', 'caches'];",
      "console.log(names.map((n) => typeof self[n] + '/' + typeof globalThis[n]).join(' '));",
      "try { fetch('/api/me'); console.log('reached'); } catch (e) { console.log('blocked'); }",
      "console.log(typeof Object.getPrototypeOf(self).fetch);",
    ].join("\n");
    const { source } = buildJsWorkerSource(code, "");
    const out = await new Promise<{ stdout: string; stderr: string }>((resolve, reject) => {
      const ctx = workerLike();
      Object.assign(ctx, {
        postMessage: (m: { stdout: string; stderr: string }) => resolve(m),
        setTimeout,
        performance,
        Promise,
        BigInt,
      });
      ctx.self = ctx;
      try {
        runInNewContext(source, ctx, { filename: "blob:http://x/1" });
      } catch (e) {
        reject(e);
      }
      setTimeout(() => reject(new Error("no done message")), 2000);
    });
    expect(out.stderr).toBe("");
    const [types, call, proto] = out.stdout.trim().split("\n");
    expect(types!.split(" ").every((t) => t === "undefined/undefined")).toBe(true);
    expect(call).toBe("blocked");
    expect(proto).toBe("undefined");
  });
});

// ------------------------------------------------------------ grading: a runner per answer

const ok = (stdout: string): RunResult => ({ status: "ok", stdout, stderr: "", timeMs: 1 });

const gq: GradingQuestion = {
  question_id: "q",
  index: 0,
  type: "code",
  prompt: "Echo",
  points: 2,
  rubric: [],
  to_grade: 2,
  graded: 0,
  language: "python",
  time_limit_ms: 1000,
  tests: [
    { input: "1", expected: "1", visible: true, points: 1 },
    { input: "2", expected: "2", visible: false, points: 1 },
  ],
};

afterEach(() => {
  setRunnerFactoryForTests("python", null);
  setRunnerForTests("python", null);
});

describe("grading runs every answer in a runner of its own", () => {
  it("creates a fresh runner per answer and disposes it after scoring", async () => {
    const made: { disposed: boolean; calls: number }[] = [];
    // a "worker" whose state leaks between runs: the first answer poisons it
    setRunnerFactoryForTests("python", () => {
      const st = { disposed: false, calls: 0, poisoned: false };
      made.push(st);
      const r: Runner = {
        language: "python",
        prepare: () => Promise.resolve(),
        run: (code, stdin) => {
          st.calls += 1;
          if (code === "poison") st.poisoned = true;
          return Promise.resolve(ok(st.poisoned ? stdin : "wrong"));
        },
        dispose: () => {
          st.disposed = true;
        },
      };
      return r;
    });
    const a = await scoreCode(gq, "poison");
    const b = await scoreCode(gq, "honest");
    expect(a.points).toBe(2);
    expect(b.points).toBe(0); // with a shared runner the poisoned state would give 2
    expect(made).toHaveLength(2);
    expect(made.every((m) => m.disposed && m.calls === 2)).toBe(true);
  });

  it("disposes the runner when the tests cannot run", async () => {
    let disposed = false;
    setRunnerFactoryForTests("python", () => ({
      language: "python",
      prepare: () => Promise.reject(new Error("Python could not load.")),
      run: () => Promise.resolve(ok("")),
      dispose: () => {
        disposed = true;
      },
    }));
    await expect(scoreCode(gq, "x")).rejects.toThrow("Python could not load.");
    expect(disposed).toBe(true);
  });

  it("createRunner never hands out the student's shared runner", async () => {
    const shared = await getRunner("python");
    const fresh = createRunner("python");
    expect(fresh).not.toBeNull();
    expect(fresh).not.toBe(shared);
    expect(createRunner("python")).not.toBe(fresh);
    expect(createRunner("cpp")).toBeNull();
    fresh!.dispose();
  });
});
