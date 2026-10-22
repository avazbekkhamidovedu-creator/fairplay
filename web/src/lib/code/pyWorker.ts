// Phase 4: Python worker. Loads Pyodide from our own server (/pyodide/, copied there at
// build time; no CDN) and runs one program per "run" message with the given stdin. The page
// terminates this worker when a program runs past its time limit.

interface PyodideLike {
  runPython(code: string, options?: { globals?: unknown }): unknown;
  setStdout(o: { write: (buf: Uint8Array) => number }): void;
  setStderr(o: { write: (buf: Uint8Array) => number }): void;
  setStdin(o: { read: (buf: Uint8Array) => number }): void;
  globals: { get(name: string): unknown };
}

type InMessage = { type: "load"; base: string } | { type: "run"; id: number; code: string; stdin: string; maxOutput: number };

const ctx = self as unknown as {
  postMessage(message: unknown): void;
  onmessage: ((ev: MessageEvent<InMessage>) => void) | null;
};
let py: PyodideLike | null = null;
let loading: Promise<PyodideLike> | null = null;

// The driver runs the program as __main__ in a fresh namespace each time and prints a
// traceback without FairPlay's own frame. SystemExit ends the program quietly.
const DRIVER = `
import sys, traceback
def __fairplay_run(src):
    g = {"__name__": "__main__", "__builtins__": __builtins__}
    try:
        code = compile(src, "main.py", "exec")
    except SyntaxError:
        traceback.print_exc(limit=0)
        return 1
    try:
        exec(code, g)
        return 0
    except SystemExit as e:
        if e.code not in (None, 0):
            if not isinstance(e.code, int):
                print(e.code, file=sys.stderr)
            return 1
        return 0
    except BaseException:
        et, ev, tb = sys.exc_info()
        traceback.print_exception(et, ev, tb.tb_next if tb is not None else None)
        return 1
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
`;

async function load(base: string): Promise<PyodideLike> {
  const mod = (await import(/* @vite-ignore */ `${base}pyodide.mjs`)) as {
    loadPyodide: (o: { indexURL: string; fullStdLib?: boolean }) => Promise<PyodideLike>;
  };
  const p = await mod.loadPyodide({ indexURL: base, fullStdLib: false });
  p.runPython(DRIVER);
  return p;
}

ctx.onmessage = async (ev: MessageEvent<InMessage>) => {
  const msg = ev.data;
  if (msg.type === "load") {
    try {
      loading ??= load(msg.base);
      py = await loading;
      ctx.postMessage({ type: "ready" });
    } catch (e) {
      loading = null;
      ctx.postMessage({ type: "loadError", message: e instanceof Error ? e.message : String(e) });
    }
    return;
  }
  if (msg.type === "run") {
    if (!py) {
      ctx.postMessage({ type: "done", id: msg.id, stdout: "", stderr: "Python is not loaded.", failed: true, timeMs: 0 });
      return;
    }
    const max = msg.maxOutput;
    const streams = [
      { text: "", dec: new TextDecoder() },
      { text: "", dec: new TextDecoder() },
    ];
    let cut = false;
    const writer = (i: 0 | 1) => (buf: Uint8Array) => {
      const s = streams[i]!;
      if (s.text.length < max) {
        const chunk = s.dec.decode(buf, { stream: true });
        const room = max - s.text.length;
        if (chunk.length > room) cut = true;
        s.text += chunk.slice(0, room);
      } else cut = true;
      return buf.length;
    };
    py.setStdout({ write: writer(0) });
    py.setStderr({ write: writer(1) });
    const bytes = new TextEncoder().encode(msg.stdin);
    let pos = 0;
    py.setStdin({
      read(buf: Uint8Array) {
        const n = Math.min(buf.length, bytes.length - pos);
        buf.set(bytes.subarray(pos, pos + n));
        pos += n;
        return n;
      },
    });
    const run = py.globals.get("__fairplay_run") as (src: string) => number;
    const t0 = performance.now();
    let rc = 1;
    try {
      rc = run(msg.code);
    } catch (e) {
      streams[1]!.text += (e instanceof Error ? e.message : String(e)) + "\n";
    }
    const timeMs = performance.now() - t0;
    ctx.postMessage({
      type: "done",
      id: msg.id,
      stdout: streams[0]!.text,
      stderr: streams[1]!.text,
      failed: rc !== 0,
      truncated: cut,
      timeMs,
    });
  }
};
