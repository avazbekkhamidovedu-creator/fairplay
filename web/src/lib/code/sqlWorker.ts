// SQL worker: loads sqlite-wasm from the base the page sends (the asset pack's sqlite/
// directory) and runs one setup + query per "run" message on a fresh in-memory database.
// The page terminates this worker at the time limit. OPFS is switched off and the network
// globals are removed once SQLite is loaded.
import { runSql, type SqliteLike } from "./sqlEngine";
import { lockGlobals } from "./sandboxGlobals";

type InMessage = { type: "load"; base: string } | { type: "run"; id: number; setup: string; code: string; maxOutput: number };

const ctx = self as unknown as {
  postMessage(message: unknown): void;
  onmessage: ((ev: MessageEvent<InMessage>) => void) | null;
};
let sqlite3: SqliteLike | null = null;
let loading: Promise<SqliteLike> | null = null;

async function load(base: string): Promise<SqliteLike> {
  const quiet = () => undefined;
  (globalThis as { sqlite3ApiConfig?: unknown }).sqlite3ApiConfig = {
    disable: { vfs: { opfs: true, "opfs-vfs": true } },
    debug: quiet,
    warn: quiet,
    error: quiet,
    log: quiet,
  };
  const mod = (await import(/* @vite-ignore */ `${base}index.mjs`)) as {
    default: (o: { locateFile: (f: string) => string; print: () => void; printErr: () => void }) => Promise<SqliteLike>;
  };
  const s = await mod.default({ locateFile: (f) => base + f, print: quiet, printErr: quiet });
  lockGlobals(self);
  return s;
}

ctx.onmessage = async (ev: MessageEvent<InMessage>) => {
  const msg = ev.data;
  if (msg.type === "load") {
    try {
      loading ??= load(msg.base);
      sqlite3 = await loading;
      ctx.postMessage({ type: "ready" });
    } catch (e) {
      loading = null;
      ctx.postMessage({ type: "loadError", message: e instanceof Error ? e.message : String(e) });
    }
    return;
  }
  if (!sqlite3) {
    ctx.postMessage({ type: "done", id: msg.id, stdout: "", stderr: "SQL is not loaded.\n", failed: true, truncated: false, timeMs: 0 });
    return;
  }
  const t0 = performance.now();
  const r = runSql(sqlite3, msg.setup, msg.code, msg.maxOutput);
  ctx.postMessage({ type: "done", id: msg.id, ...r, timeMs: performance.now() - t0 });
};
