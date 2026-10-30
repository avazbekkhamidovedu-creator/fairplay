// SQL runner (page side). One worker holds sqlite-wasm; every run gets a fresh in-memory
// database inside it. A query past its time limit ends by terminating the worker; a new
// one is started at once, so the next run does not wait for the load. The run's `stdin` is
// the setup script (runTests passes the question's sql_setup there).
import { MAX_OUTPUT_CHARS, SerialQueue, timedOut, unavailable, type RunResult, type Runner } from "./runner";
import { SERVER_ASSET_BASE, assetBase, isAppBase, markAppAssetsFailed } from "../appAssets";

export const SQLITE_DIR = "sqlite/";
export const SQL_LOAD_TIMEOUT_MS = 60_000;
export const SQL_APP_LOAD_TIMEOUT_MS = 20_000;

interface DoneMessage {
  type: "done";
  id: number;
  stdout: string;
  stderr: string;
  failed: boolean;
  truncated?: boolean;
  timeMs: number;
}

const STOPPED = new Error("Stopped.");
const LOAD_FAILED = "SQL could not load. It needs the connection to the exam server the first time.";

export class SqlRunner implements Runner {
  readonly language = "sql" as const;
  private queue = new SerialQueue();
  private worker: Worker | null = null;
  private ready: Promise<Worker> | null = null;
  private nextId = 1;
  private stopCurrent: (() => void) | null = null;
  private disposed = false;

  prepare(): Promise<void> {
    return this.ensure().then(() => undefined);
  }

  private ensure(): Promise<Worker> {
    if (this.ready) return this.ready;
    if (typeof Worker === "undefined") return Promise.reject(new Error("Run is not available in this browser."));
    this.disposed = false;
    const base = assetBase();
    const p: Promise<Worker> = this.start(base).catch((e: unknown) => {
      if (!isAppBase(base) || e === STOPPED || this.ready !== p) throw e;
      markAppAssetsFailed(e);
      return this.start(SERVER_ASSET_BASE);
    });
    this.ready = p;
    p.catch(() => {
      if (this.ready === p) this.ready = null;
    });
    return p;
  }

  private start(base: string): Promise<Worker> {
    const w = new Worker(new URL("./sqlWorker.ts", import.meta.url), { type: "module" });
    this.worker = w;
    return new Promise<Worker>((resolve, reject) => {
      const timer = setTimeout(() => fail("SQL took too long to load. Check the connection and try again."), isAppBase(base) ? SQL_APP_LOAD_TIMEOUT_MS : SQL_LOAD_TIMEOUT_MS);
      const fail = (message: string) => {
        clearTimeout(timer);
        const stopped = this.worker !== w;
        w.terminate();
        if (this.worker === w) this.worker = null;
        reject(stopped ? STOPPED : new Error(message));
      };
      w.onmessage = (ev: MessageEvent<{ type: string }>) => {
        if (ev.data?.type === "ready") {
          clearTimeout(timer);
          resolve(w);
        } else if (ev.data?.type === "loadError") fail(LOAD_FAILED);
      };
      w.onerror = (ev) => {
        ev.preventDefault?.();
        fail(LOAD_FAILED);
      };
      w.postMessage({ type: "load", base: base + SQLITE_DIR });
    });
  }

  run(code: string, setup: string, timeLimitMs: number): Promise<RunResult> {
    return this.queue.push(() => this.runOne(code, setup, timeLimitMs));
  }

  private async runOne(code: string, setup: string, timeLimitMs: number): Promise<RunResult> {
    let w: Worker;
    try {
      w = await this.ensure();
    } catch (e) {
      return unavailable(e instanceof Error ? e.message : String(e));
    }
    const id = this.nextId++;
    return new Promise<RunResult>((resolve) => {
      let settled = false;
      let timer: ReturnType<typeof setTimeout> | undefined;
      const end = (r: RunResult, kill: boolean) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (this.stopCurrent === stop) this.stopCurrent = null;
        if (kill) this.kill(w, !this.disposed && r.status !== "unavailable");
        resolve(r);
      };
      const stop = () => end(unavailable("Stopped."), true);
      this.stopCurrent = stop;
      timer = setTimeout(() => end(timedOut(timeLimitMs), true), timeLimitMs);
      w.onmessage = (ev: MessageEvent<DoneMessage>) => {
        const d = ev.data;
        if (!d || d.type !== "done" || d.id !== id) return;
        end({ status: d.failed ? "error" : "ok", stdout: d.stdout, stderr: d.stderr, timeMs: Math.round(d.timeMs), truncated: !!d.truncated }, false);
      };
      w.onerror = (ev) => {
        ev.preventDefault?.();
        end({ status: "error", stdout: "", stderr: "SQL stopped unexpectedly.\n", timeMs: 0 }, true);
      };
      w.postMessage({ type: "run", id, setup, code, maxOutput: MAX_OUTPUT_CHARS });
    });
  }

  private kill(w: Worker, respawn: boolean): void {
    w.terminate();
    if (this.worker === w) {
      this.worker = null;
      this.ready = null;
    }
    if (respawn) this.ensure().catch(() => undefined);
  }

  dispose(): void {
    this.disposed = true;
    this.stopCurrent?.();
    if (this.worker) this.kill(this.worker, false);
  }
}
