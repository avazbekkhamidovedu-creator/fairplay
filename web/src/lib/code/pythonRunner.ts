// Phase 4: Python runner (page side). One worker holds Pyodide; a program that runs past
// its time limit is stopped by terminating the worker, and the next run starts a new one
// (Pyodide's files then come from the browser cache).
// 2026-10-23: the student's runs share one worker (each run starts from the same interpreter
// state, see pyDriver.ts); grading makes a PythonRunner per answer (runners.createRunner) and
// disposes it after scoring, so no answer can change the results of the next one.
import { MAX_OUTPUT_CHARS, SerialQueue, timedOut, unavailable, type RunResult, type Runner } from "./runner";

/** Where the Pyodide files are served (server/static/pyodide/, copied at build time). */
export const PYODIDE_BASE = "/pyodide/";
/** First load of Pyodide (~12 MB) may take a while on school Wi-Fi. */
export const LOAD_TIMEOUT_MS = 120_000;

interface DoneMessage {
  type: "done";
  id: number;
  stdout: string;
  stderr: string;
  failed: boolean;
  truncated?: boolean;
  timeMs: number;
  /** the program changed the interpreter's files: start a fresh worker for the next run */
  restart?: boolean;
}

export class PythonRunner implements Runner {
  readonly language = "python" as const;
  private queue = new SerialQueue();
  private worker: Worker | null = null;
  private ready: Promise<Worker> | null = null;
  private nextId = 1;

  prepare(): Promise<void> {
    return this.ensure().then(() => undefined);
  }

  private ensure(): Promise<Worker> {
    if (this.ready) return this.ready;
    if (typeof Worker === "undefined") return Promise.reject(new Error("Run is not available in this browser."));
    const w = new Worker(new URL("./pyWorker.ts", import.meta.url), { type: "module" });
    this.worker = w;
    const p = new Promise<Worker>((resolve, reject) => {
      const timer = setTimeout(() => fail("Python took too long to load. Check the connection and try again."), LOAD_TIMEOUT_MS);
      const fail = (message: string) => {
        clearTimeout(timer);
        w.terminate();
        if (this.worker === w) this.worker = null;
        if (this.ready === p) this.ready = null;
        reject(new Error(message));
      };
      w.onmessage = (ev: MessageEvent<{ type: string; message?: string }>) => {
        if (ev.data?.type === "ready") {
          clearTimeout(timer);
          resolve(w);
        } else if (ev.data?.type === "loadError") {
          fail("Python could not load. It needs the connection to the exam server the first time.");
        }
      };
      w.onerror = (ev) => {
        ev.preventDefault?.();
        fail("Python could not load. It needs the connection to the exam server the first time.");
      };
      w.postMessage({ type: "load", base: PYODIDE_BASE });
    });
    this.ready = p;
    return p;
  }

  run(code: string, stdin: string, timeLimitMs: number): Promise<RunResult> {
    return this.queue.push(() => this.runOne(code, stdin, timeLimitMs));
  }

  private async runOne(code: string, stdin: string, timeLimitMs: number): Promise<RunResult> {
    let w: Worker;
    try {
      w = await this.ensure();
    } catch (e) {
      return unavailable(e instanceof Error ? e.message : String(e));
    }
    const id = this.nextId++;
    return new Promise<RunResult>((resolve) => {
      const timer = setTimeout(() => {
        // the program is still running: stop it and drop this worker
        this.kill(w);
        resolve(timedOut(timeLimitMs));
      }, timeLimitMs);
      w.onmessage = (ev: MessageEvent<DoneMessage>) => {
        const d = ev.data;
        if (!d || d.type !== "done" || d.id !== id) return;
        clearTimeout(timer);
        if (d.restart) this.kill(w);
        resolve({
          status: d.failed ? "error" : "ok",
          stdout: d.stdout,
          stderr: d.stderr,
          timeMs: Math.round(d.timeMs),
          truncated: !!d.truncated,
        });
      };
      w.onerror = (ev) => {
        ev.preventDefault?.();
        clearTimeout(timer);
        this.kill(w);
        resolve({ status: "error", stdout: "", stderr: "Python stopped unexpectedly.\n", timeMs: 0 });
      };
      w.postMessage({ type: "run", id, code, stdin, maxOutput: MAX_OUTPUT_CHARS });
    });
  }

  private kill(w: Worker): void {
    w.terminate();
    if (this.worker === w) {
      this.worker = null;
      this.ready = null;
    }
  }

  dispose(): void {
    if (this.worker) this.kill(this.worker);
  }
}
