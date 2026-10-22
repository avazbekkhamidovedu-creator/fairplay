// Phase 4: running a program against tests and summing the points of the passed ones.
import { outputsMatch } from "./compare";
import type { RunResult, Runner } from "./runner";

export interface TestCase {
  input: string;
  expected: string;
  /** missing = sample test (what students get) */
  visible?: boolean;
  points?: number;
}

export type TestStatus = "pass" | "fail" | "error" | "timeout" | "unavailable";

export interface TestOutcome {
  index: number;
  status: TestStatus;
  input: string;
  expected: string;
  stdout: string;
  stderr: string;
  timeMs: number;
  visible: boolean;
  points: number;
  truncated?: boolean;
}

export function outcomeOf(index: number, t: TestCase, r: RunResult): TestOutcome {
  let status: TestStatus;
  if (r.status === "timeout") status = "timeout";
  else if (r.status === "unavailable") status = "unavailable";
  else if (r.status === "error") status = "error";
  else status = outputsMatch(r.stdout, t.expected) ? "pass" : "fail";
  return {
    index,
    status,
    input: t.input,
    expected: t.expected,
    stdout: r.stdout,
    stderr: r.stderr,
    timeMs: r.timeMs,
    visible: t.visible !== false,
    points: t.points ?? 0,
    truncated: r.truncated,
  };
}

/** Runs the tests one after the other. Stops early when the runner is unavailable. */
export async function runTests(
  runner: Runner,
  code: string,
  tests: TestCase[],
  timeLimitMs: number,
  onProgress?: (done: TestOutcome[]) => void,
): Promise<TestOutcome[]> {
  const out: TestOutcome[] = [];
  for (let i = 0; i < tests.length; i++) {
    const t = tests[i]!;
    const r = await runner.run(code, t.input, timeLimitMs);
    out.push(outcomeOf(i, t, r));
    onProgress?.([...out]);
    if (r.status === "unavailable") {
      for (let j = i + 1; j < tests.length; j++) out.push(outcomeOf(j, tests[j]!, r));
      break;
    }
  }
  return out;
}

export interface TestScore {
  passed: number;
  total: number;
  /** points earned (test points; or the question's points shared out when tests have none) */
  points: number;
}

/** Points for a set of outcomes. When no test carries points, the question's points are
 *  shared out by the share of tests passed (rounded to 2 decimals). */
export function scoreOutcomes(outcomes: TestOutcome[], questionPoints: number): TestScore {
  const total = outcomes.length;
  const passed = outcomes.filter((o) => o.status === "pass").length;
  const testPoints = outcomes.reduce((s, o) => s + (o.points || 0), 0);
  let points: number;
  if (total === 0) points = 0;
  else if (testPoints > 0) points = outcomes.reduce((s, o) => s + (o.status === "pass" ? o.points || 0 : 0), 0);
  else points = Math.round(((questionPoints * passed) / total) * 100) / 100;
  return { passed, total, points: Math.min(points, questionPoints) };
}

/** "Passed 3 of 4 tests (samples 2 of 2, hidden 1 of 2)." — the grade comment. */
export function testSummary(outcomes: TestOutcome[]): string {
  const total = outcomes.length;
  const passed = outcomes.filter((o) => o.status === "pass").length;
  const vis = outcomes.filter((o) => o.visible);
  const hid = outcomes.filter((o) => !o.visible);
  const parts: string[] = [];
  if (vis.length && hid.length) {
    parts.push(`samples ${vis.filter((o) => o.status === "pass").length} of ${vis.length}`);
    parts.push(`hidden ${hid.filter((o) => o.status === "pass").length} of ${hid.length}`);
  }
  return `Passed ${passed} of ${total} ${total === 1 ? "test" : "tests"}${parts.length ? ` (${parts.join(", ")})` : ""}.`;
}

export const STATUS_LABEL: Record<TestStatus, string> = {
  pass: "Passed",
  fail: "Wrong answer",
  error: "Error",
  timeout: "Time limit exceeded",
  unavailable: "Not run",
};
