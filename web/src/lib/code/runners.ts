// Phase 4: the shared runner per language. Both runners are small and bundled with the
// page (so Run for JavaScript keeps working if the Wi-Fi drops); Pyodide itself loads from
// the asset pack on first use (the app's copy, or /app-assets/ on the server).
// 2026-10-23: getRunner is the student's shared runner (one Pyodide for all their runs);
// createRunner makes a runner of its own (its own worker) for grading one answer, which the
// caller disposes when done, so no student's code can touch another student's results.
import type { CodeLanguage } from "../../api/types";
import { JsRunner } from "./jsRunner";
import { canRunInBrowser } from "./languages";
import { PythonRunner } from "./pythonRunner";
import type { Runner } from "./runner";

const runners = new Map<CodeLanguage, Runner>();
const overrides = new Map<CodeLanguage, Runner>();
const factories = new Map<CodeLanguage, () => Runner>();

function create(lang: CodeLanguage): Runner | null {
  if (lang === "python") return new PythonRunner();
  if (lang === "javascript") return new JsRunner();
  return null; // C, C++, Java: no browser runner (a server runner can plug in here)
}

/** The shared runner for a language, or null when it cannot run in the browser. */
export function getRunner(lang: CodeLanguage): Promise<Runner> | null {
  if (!canRunInBrowser(lang)) return null;
  const o = overrides.get(lang);
  if (o) return Promise.resolve(o);
  let r = runners.get(lang);
  if (!r) {
    const made = create(lang);
    if (!made) return null;
    r = made;
    runners.set(lang, r);
  }
  return Promise.resolve(r);
}

/** A new runner with a worker of its own (grading). The caller disposes it. */
export function createRunner(lang: CodeLanguage): Runner | null {
  if (!canRunInBrowser(lang)) return null;
  const f = factories.get(lang);
  if (f) return f();
  const o = overrides.get(lang);
  if (o) return o;
  return create(lang);
}

/** Stops the student's shared runner (the exam is over): its worker and Pyodide go away. */
export function resetSharedRunners(): void {
  for (const r of runners.values()) r.dispose();
  runners.clear();
}

/** For tests: replace the runner of a language (null restores the built-in one). */
export function setRunnerForTests(lang: CodeLanguage, runner: Runner | null): void {
  if (runner) overrides.set(lang, runner);
  else overrides.delete(lang);
}

/** For tests: how createRunner makes runners (null restores the built-in one). */
export function setRunnerFactoryForTests(lang: CodeLanguage, factory: (() => Runner) | null): void {
  if (factory) factories.set(lang, factory);
  else factories.delete(lang);
}

/** Load the runtime in idle time (used for Python when the exam has Python questions). */
export function prefetchRunner(lang: CodeLanguage): void {
  const r = getRunner(lang);
  if (!r) return;
  const go = () => void r.then((x) => x.prepare()).catch(() => undefined);
  const w = window as Window & { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number };
  if (typeof w.requestIdleCallback === "function") w.requestIdleCallback(go, { timeout: 15000 });
  else setTimeout(go, 3000);
}
