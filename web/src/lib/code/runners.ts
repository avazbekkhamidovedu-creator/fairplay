// Phase 4: the shared runner per language. Both runners are small and bundled with the
// page (so Run for JavaScript keeps working if the Wi-Fi drops); Pyodide itself loads from
// /pyodide/ on first use.
import type { CodeLanguage } from "../../api/types";
import { JsRunner } from "./jsRunner";
import { canRunInBrowser } from "./languages";
import { PythonRunner } from "./pythonRunner";
import type { Runner } from "./runner";

const runners = new Map<CodeLanguage, Runner>();

function create(lang: CodeLanguage): Runner | null {
  if (lang === "python") return new PythonRunner();
  if (lang === "javascript") return new JsRunner();
  return null; // C, C++, Java: no browser runner (a server runner can plug in here)
}

/** The shared runner for a language, or null when it cannot run in the browser. */
export function getRunner(lang: CodeLanguage): Promise<Runner> | null {
  if (!canRunInBrowser(lang)) return null;
  let r = runners.get(lang);
  if (!r) {
    const made = create(lang);
    if (!made) return null;
    r = made;
    runners.set(lang, r);
  }
  return Promise.resolve(r);
}

/** For tests: replace the runner of a language (null restores the built-in one). */
export function setRunnerForTests(lang: CodeLanguage, runner: Runner | null): void {
  if (runner) runners.set(lang, runner);
  else runners.delete(lang);
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
