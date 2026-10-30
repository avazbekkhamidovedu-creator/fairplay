// C, C++ and Java run on the exam server's runner, never in the page. One request carries a
// whole batch (all sample tests, or the single custom input); the results are mapped to the
// same RunResult shape the browser runners return, so the test UI and compare.ts work as is.
import { ApiError, codeRunApi } from "../../api/client";
import type { CodeLanguage, StudentRunResponse } from "../../api/types";
import { fmtSeconds, unavailable, type RunResult, type Runner } from "./runner";

export const RATE_LIMIT_GAP_MS = 3000;
export const MSG_WAIT = "Please wait a moment before running again.";
export const MSG_OFFLINE = "Run needs the network or the code runner is unavailable. Your answer is saved.";
const MSG_INTERNAL = "The code runner had a problem. Try again in a moment.";

export type StudentRunCall = (
  attemptId: string,
  questionId: string,
  source: string,
  stdins: string[],
  signal?: AbortSignal,
) => Promise<StudentRunResponse>;

/** A runner that answers a whole batch of inputs in one call. */
export interface BatchRunner extends Runner {
  runBatch(code: string, stdins: string[], timeLimitMs: number): Promise<RunResult[]>;
}

export function isBatchRunner(r: Runner): r is BatchRunner {
  return typeof (r as Partial<BatchRunner>).runBatch === "function";
}

// the server allows one run per 3 s per attempt: remember when this page last sent one
const lastSent = new Map<string, number>();

export function mapRunResponse(res: StudentRunResponse, count: number, timeLimitMs: number): RunResult[] {
  if (res.status === "compile_error") {
    const msg = res.compile_output || "Compile error.";
    return Array.from({ length: count }, () => ({ status: "error", stdout: "", stderr: msg, timeMs: 0 }));
  }
  if (res.status !== "ok" || !Array.isArray(res.results)) return Array.from({ length: count }, () => unavailable(MSG_INTERNAL));
  return Array.from({ length: count }, (_, i): RunResult => {
    const r = res.results[i];
    if (!r) return unavailable(MSG_INTERNAL);
    const base = { stdout: r.stdout ?? "", timeMs: r.time_ms ?? 0, truncated: r.truncated || r.status === "output_limit" || undefined };
    const err = r.stderr ?? "";
    const note = (m: string) => err + (err && !err.endsWith("\n") ? "\n" : "") + m;
    switch (r.status) {
      case "ok":
        return { ...base, status: "ok", stderr: err };
      case "time_limit":
        return { ...base, status: "timeout", stderr: note(`Time limit exceeded (${fmtSeconds(timeLimitMs)}).`) };
      case "memory_limit":
        return { ...base, status: "error", stderr: note("Memory limit exceeded.") };
      case "output_limit":
        return { ...base, status: "error", stderr: note("Output limit exceeded.") };
      default:
        return { ...base, status: "error", stderr: err };
    }
  });
}

export class RemoteRunner implements BatchRunner {
  private ac = new AbortController();
  private disposed = false;

  constructor(
    readonly language: CodeLanguage,
    private attemptId: string,
    private questionId: string,
    private call: StudentRunCall = codeRunApi.studentRun,
    private now: () => number = Date.now,
  ) {}

  prepare(): Promise<void> {
    return Promise.resolve();
  }

  async run(code: string, stdin: string, timeLimitMs: number): Promise<RunResult> {
    return (await this.runBatch(code, [stdin], timeLimitMs))[0]!;
  }

  async runBatch(code: string, stdins: string[], timeLimitMs: number): Promise<RunResult[]> {
    const fail = (m: string) => stdins.map(() => unavailable(m));
    if (this.disposed) return fail("Stopped.");
    const t = this.now();
    const last = lastSent.get(this.attemptId);
    if (last !== undefined && t - last < RATE_LIMIT_GAP_MS) return fail(MSG_WAIT);
    lastSent.set(this.attemptId, t);
    try {
      const res = await this.call(this.attemptId, this.questionId, code, stdins, this.ac.signal);
      if (this.disposed) return fail("Stopped.");
      return mapRunResponse(res, stdins.length, timeLimitMs);
    } catch (e) {
      if (this.disposed) return fail("Stopped.");
      if (e instanceof ApiError) {
        if (e.status === 429) {
          if (e.retryAfter) lastSent.set(this.attemptId, this.now() + e.retryAfter * 1000 - RATE_LIMIT_GAP_MS);
          return fail(MSG_WAIT);
        }
        if (e.status === 400 || e.status === 403 || e.status === 404) return fail(e.detail);
      }
      return fail(MSG_OFFLINE);
    }
  }

  dispose(): void {
    this.disposed = true;
    this.ac.abort();
  }
}

/** For tests: forget the client-side rate limit. */
export function _resetRunGateForTests(): void {
  lastSent.clear();
}
