// Phase 4: output comparison, test scoring, and the JavaScript runner's worker script
// (run here in a Node vm context in place of a real Web Worker).
import { runInNewContext } from "node:vm";
import { describe, expect, it } from "vitest";
import { firstDifferentLine, normalizeOutput, outputsMatch } from "../lib/code/compare";
import { buildJsWorkerSource, syntaxErrorText } from "../lib/code/jsRunner";
import { canRunInBrowser, resolveLanguage } from "../lib/code/languages";
import { SerialQueue, fmtSeconds, timedOut, type RunResult, type Runner } from "../lib/code/runner";
import { getRunner, setRunnerForTests } from "../lib/code/runners";
import { outcomeOf, runTests, scoreOutcomes, testSummary } from "../lib/code/runTests";

describe("output comparison", () => {
  it("ignores trailing spaces per line and trailing newlines", () => {
    expect(outputsMatch("5\n", "5")).toBe(true);
    expect(outputsMatch("1 2 3   \n4\t\n\n\n", "1 2 3\n4")).toBe(true);
    expect(outputsMatch("a\r\nb\r\n", "a\nb")).toBe(true);
    expect(outputsMatch("", "\n\n")).toBe(true);
  });
  it("keeps everything else exact", () => {
    expect(outputsMatch("Yes", "yes")).toBe(false);
    expect(outputsMatch(" 5", "5")).toBe(false); // leading space
    expect(outputsMatch("1  2", "1 2")).toBe(false); // spaces inside a line
    expect(outputsMatch("a\n\nb", "a\nb")).toBe(false); // blank line in between
    expect(outputsMatch("5\n6", "5")).toBe(false);
  });
  it("normalizes and finds the first different line", () => {
    expect(normalizeOutput("x  \ny \n\n")).toBe("x\ny");
    expect(firstDifferentLine("1\n2\n3", "1\n2\n3\n")).toBeNull();
    expect(firstDifferentLine("1\n9\n3", "1\n2\n3")).toBe(2);
    expect(firstDifferentLine("1\n2", "1\n2\n3")).toBe(3);
  });
});

describe("languages", () => {
  it("resolves the question language, else the exam default, else Python", () => {
    expect(resolveLanguage("cpp", "java")).toBe("cpp");
    expect(resolveLanguage(null, "java")).toBe("java");
    expect(resolveLanguage(undefined, "ruby")).toBe("python");
    expect(canRunInBrowser("python") && canRunInBrowser("javascript")).toBe(true);
    expect(canRunInBrowser("c") || canRunInBrowser("cpp") || canRunInBrowser("java")).toBe(false);
  });
  it("has no runner for C, C++ and Java", () => {
    expect(getRunner("cpp")).toBeNull();
    expect(getRunner("java")).toBeNull();
    expect(getRunner("c")).toBeNull();
  });
  it("formats time limits", () => {
    expect(fmtSeconds(2000)).toBe("2 s");
    expect(fmtSeconds(1500)).toBe("1.5 s");
    expect(fmtSeconds(250)).toBe("0.25 s");
    expect(timedOut(1000).stderr).toMatch(/Time limit exceeded \(1 s\)/);
  });
});

function fakeRunner(fn: (code: string, stdin: string) => RunResult): Runner {
  return {
    language: "python",
    prepare: () => Promise.resolve(),
    run: (code, stdin) => Promise.resolve(fn(code, stdin)),
    dispose: () => undefined,
  };
}

const ok = (stdout: string): RunResult => ({ status: "ok", stdout, stderr: "", timeMs: 3 });

describe("running tests and scoring", () => {
  const tests = [
    { input: "1", expected: "1", visible: true, points: 2 },
    { input: "2", expected: "4", visible: false, points: 3 },
    { input: "3", expected: "9", visible: false, points: 5 },
  ];

  it("marks pass, wrong answer, error and time-out", async () => {
    const r = fakeRunner((_c, stdin) => {
      if (stdin === "1") return ok("1\n");
      if (stdin === "2") return ok("5");
      return { status: "timeout", stdout: "", stderr: "Time limit exceeded", timeMs: 1000 };
    });
    const progress: number[] = [];
    const out = await runTests(r, "code", tests, 1000, (d) => progress.push(d.length));
    expect(out.map((o) => o.status)).toEqual(["pass", "fail", "timeout"]);
    expect(progress).toEqual([1, 2, 3]);
    expect(out[1]!.visible).toBe(false);
    expect(scoreOutcomes(out, 10)).toEqual({ passed: 1, total: 3, points: 2 });
    expect(testSummary(out)).toBe("Passed 1 of 3 tests (samples 1 of 1, hidden 0 of 2).");
    expect(outcomeOf(0, tests[0]!, { status: "error", stdout: "1", stderr: "Boom", timeMs: 1 }).status).toBe("error");
  });

  it("shares the question points out when tests carry none", async () => {
    const r = fakeRunner((_c, stdin) => ok(stdin === "1" ? "1" : "x"));
    const noPts = tests.map((t) => ({ ...t, points: 0 }));
    const out = await runTests(r, "code", noPts, 1000);
    expect(scoreOutcomes(out, 9)).toEqual({ passed: 1, total: 3, points: 3 });
    expect(scoreOutcomes([], 9).points).toBe(0);
    expect(testSummary(out.slice(0, 1))).toBe("Passed 1 of 1 test.");
  });

  it("stops when the runner is unavailable", async () => {
    let calls = 0;
    const r = fakeRunner(() => {
      calls += 1;
      return { status: "unavailable", stdout: "", stderr: "Python could not load.", timeMs: 0 };
    });
    const out = await runTests(r, "code", tests, 1000);
    expect(calls).toBe(1);
    expect(out.map((o) => o.status)).toEqual(["unavailable", "unavailable", "unavailable"]);
  });

  it("can swap in a runner for tests", async () => {
    const r = fakeRunner(() => ok("hi"));
    setRunnerForTests("python", r);
    expect(await getRunner("python")).toBe(r);
    setRunnerForTests("python", null);
  });

  it("runs queued jobs one at a time, in order", async () => {
    const q = new SerialQueue();
    const seen: string[] = [];
    const slow = (name: string, ms: number) => () =>
      new Promise<string>((res) => setTimeout(() => (seen.push(name), res(name)), ms));
    const [a, b] = await Promise.all([q.push(slow("a", 20)), q.push(slow("b", 1))]);
    expect([a, b]).toEqual(["a", "b"]);
    expect(seen).toEqual(["a", "b"]);
    await expect(q.push(() => Promise.reject(new Error("x")))).rejects.toThrow("x");
    expect(await q.push(() => Promise.resolve(1))).toBe(1); // a failed job does not block the queue
  });
});

/** Runs the JavaScript worker script in a vm context and waits for its "done" message. */
function runJs(code: string, stdin = "", maxOutput?: number): Promise<{ stdout: string; stderr: string; failed: boolean; truncated: boolean }> {
  const { source } = buildJsWorkerSource(code, stdin, maxOutput);
  return new Promise((resolve, reject) => {
    const listeners: Record<string, ((e: unknown) => void)[]> = {};
    const ctx: Record<string, unknown> = {
      postMessage: (m: { stdout: string; stderr: string; failed: boolean; truncated: boolean }) => resolve(m),
      setTimeout,
      performance,
      Promise,
      BigInt,
    };
    ctx.self = {
      addEventListener: (t: string, f: (e: unknown) => void) => (listeners[t] ??= []).push(f),
    };
    try {
      runInNewContext(source, ctx, { filename: "blob:http://x/1" });
    } catch (e) {
      reject(e);
    }
    setTimeout(() => reject(new Error("no done message")), 2000);
  });
}

describe("JavaScript runner script", () => {
  it("reads lines with readline() and prints with console.log", async () => {
    const r = await runJs("const n = Number(readline());\nconst xs = readline().split(' ').map(Number);\nconsole.log(Math.max(...xs), n);", "3\n1 5 2\n");
    expect(r.stdout).toBe("5 3\n");
    expect(r.failed).toBe(false);
    expect(await runJs("console.log(readline(), readline())", "only\n")).toMatchObject({ stdout: "only null\n" });
  });

  it("supports input(), fs.readFileSync(0) and process.stdout.write", async () => {
    expect((await runJs("console.log(input())", "hi\n")).stdout).toBe("hi\n");
    const r = await runJs("const s = require('fs').readFileSync(0, 'utf8');\nprocess.stdout.write(s.trim().split('\\n').length + '!');", "a\nb\nc\n");
    expect(r.stdout).toBe("3!");
  });

  it("supports the readline module with line and close events", async () => {
    const code = [
      "const rl = require('readline').createInterface({ input: process.stdin });",
      "let sum = 0;",
      "rl.on('line', (l) => { sum += Number(l); });",
      "rl.on('close', () => console.log(sum));",
    ].join("\n");
    expect((await runJs(code, "1\n2\n3\n")).stdout).toBe("6\n");
    const iter = "const rl = require('node:readline').createInterface({});\nfor await (const l of rl) console.log(l.toUpperCase());";
    expect((await runJs(iter, "a\nb")).stdout).toBe("A\nB\n");
  });

  it("formats values like Node", async () => {
    const r = await runJs("console.log([1, 'a', [2]], {x: 1}, null, undefined, 2n, true)");
    expect(r.stdout).toBe("[ 1, \"a\", [ 2 ] ] { x: 1 } null undefined 2 true\n");
  });

  it("reports errors with the student's line number", async () => {
    const r = await runJs("console.log(1);\nfoo();\n", "");
    expect(r.failed).toBe(true);
    expect(r.stdout).toBe("1\n");
    expect(r.stderr).toMatch(/^ReferenceError: foo is not defined/);
    expect(r.stderr).toMatch(/\(line 2\)/);
    expect(syntaxErrorText("Uncaught SyntaxError: Unexpected token '}'", 140, 137)).toBe("SyntaxError: Unexpected token '}' (line 3)");
  });

  it("process.exit() ends quietly; console.error goes to stderr without failing", async () => {
    const r = await runJs("console.log('a');\nconsole.error('note');\nprocess.exit(0);\nconsole.log('b');");
    expect(r).toMatchObject({ stdout: "a\n", stderr: "note\n", failed: false });
  });

  it("cuts very long output", async () => {
    const r = await runJs("for (let i = 0; i < 1000; i++) console.log('xxxxxxxxxx');", "", 100);
    expect(r.stdout.length).toBe(100);
    expect(r.truncated).toBe(true);
  });

  it("does not let the program reach the page: no eval needed, code is plain script text", () => {
    const { source, lineOffset } = buildJsWorkerSource("console.log(1)", "x");
    expect(source.split("\n")[lineOffset]).toBe("console.log(1)");
    expect(source).not.toMatch(/\beval\(|new Function/);
    expect(source).toContain(JSON.stringify("x"));
  });
});
