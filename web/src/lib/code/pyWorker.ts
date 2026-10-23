// Phase 4: Python worker. Loads Pyodide from the base the page sends in its "load" message
// (perf 0.6.2: the FairPlay app's own copy of the asset pack, or /app-assets/pyodide/ on our
// server; no CDN; a cross-origin base needs the app's Access-Control-Allow-Origin) and runs
// one program per "run" message with the given stdin. The page terminates this worker when a
// program runs past its time limit.
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
  // a WebAssembly file that does not load (e.g. a broken copy in the app) makes Pyodide only
  // log "wasm instantiation failed!" and leave its promise pending: fail at once instead, so
  // the page tries the server's copy without waiting for its timeout
  const warn = console.warn;
  let onFail: (ev: Event) => void = () => {};
  const failed = new Promise<never>((_, reject) => {
    console.warn = (...args: unknown[]) => {
      warn.apply(console, args);
      if (typeof args[0] === "string" && args[0].includes("wasm instantiation failed")) reject(new Error(args[0]));
    };
    onFail = (ev: Event) => {
      const r = (ev as PromiseRejectionEvent).reason ?? (ev as ErrorEvent).message;
      reject(r instanceof Error ? r : new Error(String(r)));
    };
    self.addEventListener("unhandledrejection", onFail);
  });
  let p: PyodideLike;
  try {
    p = await Promise.race([mod.loadPyodide({ indexURL: base, fullStdLib: false }), failed]);
  } finally {
    console.warn = warn;
    self.removeEventListener("unhandledrejection", onFail);
  }
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
      const id = msg.id;
      // the page times the run from "started" to "ended": only the student's own code
      const r = session.run(msg.code, msg.stdin, msg.maxOutput, {
        onStart: () => ctx.postMessage({ type: "started", id }),
        onEnd: () => ctx.postMessage({ type: "ended", id }),
      });
      ctx.postMessage({ type: "done", id: msg.id, ...r });
    } catch (e) {
      // the interpreter is in an unknown state: the page starts a fresh worker next time
      const stderr = (e instanceof Error ? e.message : String(e)) + "\n";
      ctx.postMessage({ type: "done", id: msg.id, stdout: "", stderr, failed: true, timeMs: 0, restart: true });
    }
  }
};
