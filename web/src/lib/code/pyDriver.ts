// 2026-10-23: the Python side of the runner, apart from the worker so it can be tested with
// Pyodide in Node. One Pyodide stays loaded for the student's own runs (loading takes
// seconds), so every run must start from the same interpreter state:
//
//  - The program has no way back to JavaScript: `js`, `pyodide_js`, `pyodide.*`, `_pyodide*`
//    are blocked in sys.modules and the JsFinder import hook is removed (the worker also
//    removes fetch / XMLHttpRequest / WebSocket / ... from its own global scope).
//  - Before each run the interpreter is put back as it was after loading: sys.modules (new
//    modules are dropped), the dict of every module loaded then (so builtins.print = ...,
//    sys.stdout = ..., math.pi = 3 and replacing __main__ do not survive), sys.path and the
//    other lists in sys, the instance dicts of the standard streams, the trace / profile
//    hooks and the recursion limit; files the program created are deleted and a changed file
//    asks the page for a fresh worker. The reset runs after each run (and once after
//    loading), outside the student's timed window (hooks.onStart / onEnd).
//  - os._exit() ends the interpreter: the output so far is returned and the page starts a
//    fresh worker for the next run.
//  - The driver itself is compiled again per run into a dict of its own, with exec / dict
//    methods held from JavaScript (C functions a program cannot replace).
//
// What remains (the student's own worker only; grading uses a fresh worker per answer): a
// program that digs through the interpreter on purpose (gc, frames, class attributes of
// standard-library classes, finalizers that re-arm themselves) can still change what its
// own later runs see.

export interface PyProxyLike {
  (...args: unknown[]): unknown;
  get(key: unknown): unknown;
  set(key: unknown, value: unknown): void;
  readonly length: number;
  destroy(): void;
}

interface FsStat {
  mode: number;
  mtime: Date | number;
  size: number;
}

export interface PyodideLike {
  runPython(code: string, options?: { globals?: unknown }): unknown;
  setStdout(o: { write: (buf: Uint8Array) => number }): void;
  setStderr(o: { write: (buf: Uint8Array) => number }): void;
  setStdin(o: { read: (buf: Uint8Array) => number }): void;
  toPy(o: unknown): unknown;
  FS: {
    readdir(path: string): string[];
    stat(path: string): FsStat;
    unlink(path: string): void;
    rmdir(path: string): void;
    isDir(mode: number): boolean;
  };
}

export interface PyRunOutput {
  stdout: string;
  stderr: string;
  failed: boolean;
  truncated: boolean;
  timeMs: number;
  /** the program changed a file that was there after loading: use a fresh worker next */
  restart: boolean;
}

/** Module names (and packages under them) the student's program cannot import. */
export const BLOCKED_MODULES = ["js", "pyodide_js", "pyodide", "_pyodide", "_pyodide_core", "micropip"];

// Run once after loading, in a dict of its own. Returns what the per-run reset needs.
const SETUP = `
import sys, builtins, gc, io, traceback, types
_blocked = ${JSON.stringify(BLOCKED_MODULES)}
sys.meta_path[:] = [f for f in sys.meta_path if type(f).__name__ != "JsFinder"]
for _k in list(sys.modules):
    if _k.split(".")[0] in _blocked:
        sys.modules[_k] = None
for _k in _blocked + ["pyodide.ffi", "pyodide.code", "pyodide.http", "pyodide.webloop"]:
    sys.modules[_k] = None
def _no_audit_hooks(hook):
    raise RuntimeError("sys.addaudithook is not available in the exam")
sys.addaudithook = _no_audit_hooks
_dicts = []
for _m in list(sys.modules.values()):
    if isinstance(_m, types.ModuleType):
        _dicts.append((_m.__dict__, dict(_m.__dict__)))
for _s in (sys.stdin, sys.stdout, sys.stderr, sys.__stdin__, sys.__stdout__, sys.__stderr__):
    _d = getattr(_s, "__dict__", None)
    if isinstance(_d, dict):
        _dicts.append((_d, dict(_d)))
_dicts.append((sys.path_importer_cache, dict(sys.path_importer_cache)))
_dicts.append((sys.modules, dict(sys.modules)))
_lists = [(l, list(l)) for l in (sys.path, sys.meta_path, sys.path_hooks, sys.argv, sys.warnoptions)]
_calls = [(sys.settrace, None), (sys.setprofile, None), (sys.setrecursionlimit, sys.getrecursionlimit())]
if hasattr(sys, "set_int_max_str_digits"):
    _calls.append((sys.set_int_max_str_digits, sys.get_int_max_str_digits()))
(builtins.exec, dict, dict.clear, dict.update, list.clear, list.extend, gc.collect, _dicts, _lists, _calls)
`;

// Compiled again for every run, into a fresh dict.
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

/** Folders whose new files are removed after each run (the program's own files). */
const SKIP_DIRS = new Set(["/dev", "/proc", "/sys"]);

type Pairs = Array<[PyProxyLike, PyProxyLike]>;

function pairs(list: PyProxyLike): Pairs {
  const out: Pairs = [];
  for (let i = 0; i < list.length; i++) {
    const t = list.get(i) as PyProxyLike;
    out.push([t.get(0) as PyProxyLike, t.get(1) as PyProxyLike]);
    t.destroy();
  }
  return out;
}

function fsSnapshot(py: PyodideLike): Map<string, string> {
  const files = new Map<string, string>();
  const walk = (dir: string) => {
    let names: string[];
    try {
      names = py.FS.readdir(dir);
    } catch {
      return;
    }
    for (const n of names) {
      if (n === "." || n === "..") continue;
      const path = dir === "/" ? `/${n}` : `${dir}/${n}`;
      if (SKIP_DIRS.has(path)) continue;
      let st: FsStat;
      try {
        st = py.FS.stat(path);
      } catch {
        continue;
      }
      if (py.FS.isDir(st.mode)) {
        files.set(path, "dir");
        walk(path);
      } else files.set(path, `${Number(st.mtime instanceof Date ? st.mtime.getTime() : st.mtime)}:${st.size}`);
    }
  };
  walk("/");
  return files;
}

/** Called around the student's own code only (the page times the run between them). */
export interface PyRunHooks {
  onStart?(): void;
  onEnd?(): void;
}

export interface PySession {
  run(code: string, stdin: string, maxOutput: number, hooks?: PyRunHooks): PyRunOutput;
}

/** Pyodide's error for os._exit() / an interpreter exit: the interpreter cannot be used again. */
function exitStatus(e: unknown): number | null {
  if (!e || typeof e !== "object") return null;
  const x = e as { name?: unknown; status?: unknown; constructor?: { name?: unknown } };
  if (typeof x.status === "number" && (x.name === "Exit" || x.constructor?.name === "Exit")) return x.status;
  return null;
}

/** Locks the interpreter down and returns the runner for one program at a time. */
export function createPySession(py: PyodideLike): PySession {
  const setup = py.runPython(SETUP, { globals: py.toPy({}) }) as PyProxyLike;
  const [execFn, dictType, dictClear, dictUpdate, listClear, listExtend, gcCollect] = [0, 1, 2, 3, 4, 5, 6].map(
    (i) => setup.get(i) as PyProxyLike,
  ) as PyProxyLike[] as [PyProxyLike, PyProxyLike, PyProxyLike, PyProxyLike, PyProxyLike, PyProxyLike, PyProxyLike];
  const dictsProxy = setup.get(7) as PyProxyLike;
  const listsProxy = setup.get(8) as PyProxyLike;
  const callsProxy = setup.get(9) as PyProxyLike;
  const dicts = pairs(dictsProxy);
  const lists = pairs(listsProxy);
  const calls = pairs(callsProxy);
  dictsProxy.destroy();
  listsProxy.destroy();
  callsProxy.destroy();
  setup.destroy();
  const files = fsSnapshot(py);
  const discard = { write: (buf: Uint8Array) => buf.length };

  function restore() {
    for (const [live, copy] of dicts) {
      dictClear(live);
      dictUpdate(live, copy);
    }
    for (const [live, copy] of lists) {
      listClear(live);
      listExtend(live, copy);
    }
    for (const [fn, arg] of calls) fn(arg); // None arrives as undefined and goes back as None
  }

  /** Deletes what the last program wrote; true when it changed a file that was there. */
  function cleanFiles(): boolean {
    let changed = false;
    const now = fsSnapshot(py);
    const extra = [...now.keys()].filter((p) => !files.has(p)).sort((a, b) => b.length - a.length);
    for (const p of extra) {
      try {
        if (now.get(p) === "dir") py.FS.rmdir(p);
        else py.FS.unlink(p);
      } catch {
        changed = true;
      }
    }
    for (const [p, v] of files) if (now.get(p) !== v) changed = true;
    return changed;
  }

  function reset() {
    py.setStdout(discard);
    py.setStderr(discard);
    // twice: finalizers of what the first pass drops may write into module dicts again
    restore();
    gcCollect();
    restore();
  }

  reset();

  return {
    run(code, stdin, max, hooks) {
      // the interpreter was put back after the last run (and when the session was made), so
      // nothing of the clean-up runs inside the student's timed window
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
      // same line endings as the JavaScript runner: a teacher's CRLF input reads as "\n"
      const bytes = new TextEncoder().encode(stdin.replace(/\r\n?/g, "\n"));
      let pos = 0;
      py.setStdin({
        read(buf: Uint8Array) {
          const n = Math.min(buf.length, bytes.length - pos);
          buf.set(bytes.subarray(pos, pos + n));
          pos += n;
          return n;
        },
      });
      const ns = dictType() as PyProxyLike;
      let run: PyProxyLike | null = null;
      let rc = 1;
      let gone = false;
      let t0 = performance.now();
      let started = false;
      try {
        execFn(DRIVER, ns);
        run = ns.get("__fairplay_run") as PyProxyLike;
        hooks?.onStart?.();
        started = true;
        t0 = performance.now();
        rc = Number(run(code));
      } catch (e) {
        // os._exit() or a crash of the interpreter: keep what was printed, use a fresh worker next
        gone = true;
        const status = exitStatus(e);
        if (status === 0) rc = 0;
        else {
          const err = streams[1]!;
          if (err.text && !err.text.endsWith("\n")) err.text += "\n";
          err.text += (status !== null ? `Program exited with code ${status}.` : e instanceof Error ? e.message : String(e)) + "\n";
        }
      }
      const timeMs = performance.now() - t0;
      if (!started) hooks?.onStart?.();
      hooks?.onEnd?.();
      try {
        run?.destroy();
        ns.destroy();
      } catch {
        /* already gone */
      }
      let restart = gone;
      if (!restart) {
        try {
          restart = cleanFiles();
        } catch {
          restart = true;
        }
      }
      if (!restart) {
        try {
          reset();
        } catch {
          restart = true;
        }
      } else {
        try {
          py.setStdout(discard);
          py.setStderr(discard);
        } catch {
          /* the interpreter has exited */
        }
      }
      return { stdout: streams[0]!.text, stderr: streams[1]!.text, failed: rc !== 0, truncated: cut, timeMs, restart };
    },
  };
}
