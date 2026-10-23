// Phase 4: running a student's program. The server never runs code (there is no safe
// sandbox there), so programs run in the browser in a Web Worker that is thrown away when
// the time limit is hit. Python uses Pyodide (self-hosted, asset pack), JavaScript a
// worker built from a Blob. C, C++ and Java have no runner yet: a server runner can be
// added later behind the same Runner interface.
import type { CodeLanguage } from "../../api/types";

export type RunStatus = "ok" | "error" | "timeout" | "unavailable";

export interface RunResult {
  status: RunStatus;
  stdout: string;
  stderr: string;
  /** run time in milliseconds (0 when it did not run) */
  timeMs: number;
  /** output was longer than MAX_OUTPUT_CHARS and was cut */
  truncated?: boolean;
}

export interface Runner {
  readonly language: CodeLanguage;
  /** Load the runtime ahead of the first run (Pyodide is ~12 MB). Safe to call often. */
  prepare(): Promise<void>;
  run(code: string, stdin: string, timeLimitMs: number): Promise<RunResult>;
  /** Stop a running program and free the worker. */
  dispose(): void;
}

/** Output kept per stream; more is cut (a print loop must not eat the page's memory). */
export const MAX_OUTPUT_CHARS = 256 * 1024;

export function unavailable(message: string): RunResult {
  return { status: "unavailable", stdout: "", stderr: message, timeMs: 0 };
}

export function timedOut(timeLimitMs: number, stdout = "", stderr = ""): RunResult {
  return {
    status: "timeout",
    stdout,
    stderr: stderr + (stderr && !stderr.endsWith("\n") ? "\n" : "") + `Time limit exceeded (${fmtSeconds(timeLimitMs)}).`,
    timeMs: timeLimitMs,
  };
}

export function fmtSeconds(ms: number): string {
  const s = ms / 1000;
  return `${Number.isInteger(s) ? s : s.toFixed(s < 1 ? 2 : 1)} s`;
}

/** Runs one job at a time, in order (each worker runs one program at a time). */
export class SerialQueue {
  private tail: Promise<unknown> = Promise.resolve();
  push<T>(job: () => Promise<T>): Promise<T> {
    const next = this.tail.then(job, job);
    this.tail = next.catch(() => undefined);
    return next;
  }
}
