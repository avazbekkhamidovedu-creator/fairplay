// A few runners (workers) that grading shares between answers, instead of loading a new
// interpreter for every answer. Each runner starts every run from a clean interpreter
// (pyDriver.ts, a new database, a new Blob worker); on top of that a runner whose answer
// timed out, crashed or errored is replaced before anyone else uses it.
import type { CodeLanguage } from "../../api/types";
import type { Runner } from "./runner";
import { createRunner } from "./runners";

/** min(4, hardwareConcurrency / 2), at least 1. */
export function gradingPoolSize(cores: number | undefined = typeof navigator === "undefined" ? undefined : navigator.hardwareConcurrency): number {
  const half = Math.floor((cores && cores > 0 ? cores : 2) / 2);
  return Math.max(1, Math.min(4, half));
}

export class RunnerPool {
  private idle: Runner[] = [];
  private all = new Set<Runner>();
  private waiting: Array<{ resolve: (r: Runner) => void; reject: (e: Error) => void }> = [];
  private closed = false;

  constructor(
    readonly language: CodeLanguage,
    readonly size: number = gradingPoolSize(),
    private make: (lang: CodeLanguage) => Runner | null = createRunner,
  ) {}

  /** A runner nobody else is using. Waits when all `size` runners are busy. */
  acquire(): Promise<Runner> {
    if (this.closed) return Promise.reject(new Error("Stopped."));
    const free = this.idle.pop();
    if (free) return Promise.resolve(free);
    if (this.all.size < this.size) {
      const r = this.make(this.language);
      if (!r) return Promise.reject(new Error("This language cannot run in the browser."));
      this.all.add(r);
      return Promise.resolve(r);
    }
    return new Promise<Runner>((resolve, reject) => this.waiting.push({ resolve, reject }));
  }

  /** Gives the runner back; `recycle` throws it away (the next acquire makes a new one). */
  release(r: Runner, recycle = false): void {
    if (!this.all.has(r)) return;
    if (this.closed) {
      r.dispose();
      return;
    }
    if (recycle) {
      this.all.delete(r);
      r.dispose();
      const next = this.waiting.shift();
      if (next) {
        const fresh = this.make(this.language);
        if (fresh) {
          this.all.add(fresh);
          next.resolve(fresh);
        } else this.waiting.unshift(next);
      }
      return;
    }
    const next = this.waiting.shift();
    if (next) next.resolve(r);
    else this.idle.push(r);
  }

  /** Stops every runner (running programs end at once). */
  dispose(): void {
    this.closed = true;
    for (const r of this.all) r.dispose();
    this.all.clear();
    this.idle = [];
    for (const w of this.waiting.splice(0)) w.reject(new Error("Stopped."));
  }
}
