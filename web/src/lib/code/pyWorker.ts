// Phase 4: Python worker. Loads Pyodide from our own server (/pyodide/, copied there at
// build time; no CDN) and runs one program per "run" message with the given stdin. The page
// terminates this worker when a program runs past its time limit.
// 2026-10-23: after loading, the worker drops its network / storage globals and the
// interpreter is locked down and put back as it was before every run (see pyDriver.ts).
import { createPySession, type PyodideLike, type PySession } from "./pyDriver";
import { lockGlobals } from "./sandboxGlobals";

type InMessage = { type: "load"; base: string } | { type: "run"; id: number; code: string; stdin: string; maxOutput: number };

const ctx = self as unknown as {
  postMessage(message: unknown): void;
  onmessage: ((ev: MessageEvent<InMessage>) => void) | null;
};
let session: PySession | null = null;
let loading: Promise<PySession> | null = null;

async function load(base: string): Promise<PySession> {
  const mod = (await import(/* @vite-ignore */ `${base}pyodide.mjs`)) as {
    loadPyodide: (o: { indexURL: string; fullStdLib?: boolean }) => Promise<PyodideLike>;
  };
  const p = await mod.loadPyodide({ indexURL: base, fullStdLib: false });
  const s = createPySession(p);
  // nothing more is fetched from here on
  lockGlobals(self);
  return s;
}

ctx.onmessage = async (ev: MessageEvent<InMessage>) => {
  const msg = ev.data;
  if (msg.type === "load") {
    try {
      loading ??= load(msg.base);
      session = await loading;
      ctx.postMessage({ type: "ready" });
    } catch (e) {
      loading = null;
      ctx.postMessage({ type: "loadError", message: e instanceof Error ? e.message : String(e) });
    }
    return;
  }
  if (msg.type === "run") {
    if (!session) {
      ctx.postMessage({ type: "done", id: msg.id, stdout: "", stderr: "Python is not loaded.", failed: true, timeMs: 0 });
      return;
    }
    try {
      const r = session.run(msg.code, msg.stdin, msg.maxOutput);
      ctx.postMessage({ type: "done", id: msg.id, ...r });
    } catch (e) {
      // the interpreter is in an unknown state: the page starts a fresh worker next time
      const stderr = (e instanceof Error ? e.message : String(e)) + "\n";
      ctx.postMessage({ type: "done", id: msg.id, stdout: "", stderr, failed: true, timeMs: 0, restart: true });
    }
  }
};
