// Phase 4: JavaScript runner. The student's program is embedded in the text of a worker
// script (Blob URL, allowed by worker-src blob:), so no eval is needed anywhere. The worker
// gets stdin up front and reports stdout/stderr once; it is terminated at the time limit.
//
// Inside the program: readline() / input() return the next input line (null at the end),
// console.log / console.error print, require("fs").readFileSync(0, "utf8") returns all of
// stdin, and require("readline").createInterface(...) gives "line" / "close" events and
// `for await (const line of rl)`, like Node.
// 2026-10-23: every run is a new Blob worker that is terminated when it ends, so one run
// never sees another. Before the program, the script removes fetch / XMLHttpRequest /
// WebSocket / indexedDB / importScripts / ... from the worker (sandboxGlobals.ts).
import { MAX_OUTPUT_CHARS, SerialQueue, timedOut, unavailable, type RunResult, type Runner } from "./runner";
import { lockGlobalsSource } from "./sandboxGlobals";

export interface JsWorkerSource {
  source: string;
  /** lines before the student's first line (to report the student's own line numbers) */
  lineOffset: number;
}

/** The worker script for one run. Pure (tested without a browser). */
export function buildJsWorkerSource(code: string, stdin: string, maxOutput = MAX_OUTPUT_CHARS): JsWorkerSource {
  const head = `${lockGlobalsSource("self")}
var __fp = (function () {
  var stdin = ${JSON.stringify(stdin)};
  var max = ${Math.max(1, Math.floor(maxOutput))};
  var out = [], err = [], sizes = [0, 0], cut = false;
  function put(which, s) {
    var arr = which ? err : out;
    s = String(s);
    var room = max - sizes[which];
    if (room <= 0) { cut = true; return; }
    if (s.length > room) { s = s.slice(0, room); cut = true; }
    sizes[which] += s.length;
    arr.push(s);
  }
  function show(x, depth) {
    if (typeof x === "string") return depth ? JSON.stringify(x) : x;
    if (x === null || x === undefined || typeof x === "number" || typeof x === "boolean" || typeof x === "bigint")
      return typeof x === "bigint" ? String(x) + (depth ? "n" : "") : String(x);
    if (typeof x === "function") return "[Function" + (x.name ? ": " + x.name : " (anonymous)") + "]";
    if (typeof x === "symbol") return x.toString();
    if (x instanceof Error) return x.stack || String(x);
    if ((depth || 0) > 2) return Array.isArray(x) ? "[Array]" : "[Object]";
    try {
      if (Array.isArray(x)) return "[ " + x.map(function (v) { return show(v, (depth || 0) + 1); }).join(", ") + " ]";
      if (x instanceof Map) return "Map(" + x.size + ") { " + Array.from(x).map(function (kv) { return show(kv[0], 1) + " => " + show(kv[1], (depth || 0) + 1); }).join(", ") + " }";
      if (x instanceof Set) return "Set(" + x.size + ") { " + Array.from(x).map(function (v) { return show(v, (depth || 0) + 1); }).join(", ") + " }";
      var keys = Object.keys(x);
      if (!keys.length) return "{}";
      return "{ " + keys.map(function (k) { return k + ": " + show(x[k], (depth || 0) + 1); }).join(", ") + " }";
    } catch (e) { return String(x); }
  }
  function line(args) { return Array.prototype.map.call(args, function (a) { return show(a, 0); }).join(" ") + "\\n"; }
  var lines = stdin.replace(/\\r\\n?/g, "\\n").split("\\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  var next = 0;
  function readline() { return next < lines.length ? lines[next++] : null; }
  var listeners = { line: [], close: [] };
  var iterators = [];
  var rl = {
    on: function (ev, cb) { if (listeners[ev]) listeners[ev].push(cb); return rl; },
    once: function (ev, cb) { return rl.on(ev, cb); },
    close: function () {},
    question: function (q, cb) { put(0, q); var l = readline(); setTimeout(function () { cb(l === null ? "" : l); }, 0); },
    setPrompt: function () {}, prompt: function () {}, pause: function () { return rl; }, resume: function () { return rl; }
  };
  rl[Symbol.asyncIterator] = function () {
    var it = { next: function () { var l = readline(); return Promise.resolve(l === null ? { value: undefined, done: true } : { value: l, done: false }); } };
    iterators.push(it);
    return it;
  };
  var exit = { fairplayExit: true };
  var fs = { readFileSync: function (f) {
    if (f === 0 || f === "/dev/stdin") { var rest = lines.slice(next).join("\\n"); next = lines.length; return rest + (stdin.endsWith("\\n") ? "\\n" : ""); }
    throw new Error("ENOENT: files are not available in the exam: " + String(f));
  } };
  var readlineModule = { createInterface: function () { return rl; } };
  var con = {
    log: function () { put(0, line(arguments)); }, info: function () { put(0, line(arguments)); },
    debug: function () { put(0, line(arguments)); }, warn: function () { put(1, line(arguments)); },
    error: function () { put(1, line(arguments)); }, table: function (x) { put(0, show(x, 0) + "\\n"); },
    dir: function (x) { put(0, show(x, 0) + "\\n"); }, trace: function () { put(1, line(arguments)); },
    assert: function (c) { if (!c) put(1, "Assertion failed\\n"); }, time: function () {}, timeEnd: function () {}
  };
  var proc = {
    argv: ["node", "main.js"], env: {}, platform: "browser",
    stdout: { write: function (s) { put(0, s); return true; } },
    stderr: { write: function (s) { put(1, s); return true; } },
    stdin: {},
    exit: function () { throw exit; },
    hrtime: { bigint: function () { return BigInt(Math.round(performance.now() * 1e6)); } },
    nextTick: function (cb) { Promise.resolve().then(cb); }
  };
  function req(name) {
    name = String(name).replace(/^node:/, "");
    if (name === "fs") return fs;
    if (name === "readline") return readlineModule;
    throw new Error("Cannot find module '" + name + "' (only fs and readline are available)");
  }
  var done = false, failed = false;
  function where(e) {
    var m = /:(\\d+):(\\d+)\\)?\\s*$/m.exec(String(e && e.stack || "").split("\\n").filter(function (l) { return l.indexOf("blob:") >= 0; })[0] || "");
    var n = m ? Number(m[1]) - __FP_OFFSET__ : 0;
    return n > 0 ? " (line " + n + ")" : "";
  }
  function fail(e) {
    if (e === exit) return;
    failed = true;
    var text = e instanceof Error ? (e.name || "Error") + ": " + e.message + where(e) : "Uncaught " + show(e, 0);
    put(1, text + "\\n");
  }
  function finish() {
    if (done) return;
    done = true;
    postMessage({ type: "done", stdout: out.join(""), stderr: err.join(""), failed: failed, truncated: cut, timeMs: performance.now() - started });
  }
  function drain() {
    try {
      var l;
      if (listeners.line.length) while ((l = readline()) !== null) listeners.line.forEach(function (cb) { cb(l); });
      listeners.close.forEach(function (cb) { cb(); });
    } catch (e) { fail(e); }
    setTimeout(finish, 0);
  }
  self.addEventListener("error", function (ev) { fail(ev.error || new Error(ev.message)); ev.preventDefault(); finish(); });
  self.addEventListener("unhandledrejection", function (ev) { fail(ev.reason); ev.preventDefault(); finish(); });
  var started = performance.now();
  return {
    readline: readline, console: con, process: proc, require: req,
    start: function (main) {
      started = performance.now();
      var p;
      try { p = main(); } catch (e) { fail(e); drain(); return; }
      Promise.resolve(p).then(function () { setTimeout(drain, 0); }, function (e) { fail(e); setTimeout(drain, 0); });
    }
  };
})();
var readline = __fp.readline, input = __fp.readline, prompt = __fp.readline;
var console = __fp.console, process = __fp.process, require = __fp.require;
__fp.start(__fpMain);
async function __fpMain() {
`;
  const lineOffset = head.split("\n").length - 1;
  const source = head.replace("__FP_OFFSET__", String(lineOffset)) + code + "\n}\n";
  return { source, lineOffset };
}

/** Message for a worker that failed to start (a syntax error in the student's code). */
export function syntaxErrorText(message: string, lineno: number | undefined, lineOffset: number): string {
  const msg = message.replace(/^Uncaught\s+/, "");
  const n = (lineno ?? 0) - lineOffset;
  return msg + (n > 0 ? ` (line ${n})` : "");
}

interface DoneMessage {
  type: "done";
  stdout: string;
  stderr: string;
  failed: boolean;
  truncated: boolean;
  timeMs: number;
}

export class JsRunner implements Runner {
  readonly language = "javascript" as const;
  private queue = new SerialQueue();
  private current: Worker | null = null;

  prepare(): Promise<void> {
    return Promise.resolve();
  }

  run(code: string, stdin: string, timeLimitMs: number): Promise<RunResult> {
    return this.queue.push(() => this.runOne(code, stdin, timeLimitMs));
  }

  private runOne(code: string, stdin: string, timeLimitMs: number): Promise<RunResult> {
    if (typeof Worker === "undefined" || typeof Blob === "undefined" || typeof URL.createObjectURL !== "function")
      return Promise.resolve(unavailable("Run is not available in this browser."));
    const { source, lineOffset } = buildJsWorkerSource(code, stdin);
    const url = URL.createObjectURL(new Blob([source], { type: "text/javascript" }));
    let worker: Worker;
    try {
      worker = new Worker(url);
    } catch {
      URL.revokeObjectURL(url);
      return Promise.resolve(unavailable("Run is not available in this browser."));
    }
    this.current = worker;
    const startedAt = performance.now();
    return new Promise<RunResult>((resolve) => {
      let settled = false;
      const end = (r: RunResult) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        worker.terminate();
        URL.revokeObjectURL(url);
        if (this.current === worker) this.current = null;
        resolve(r);
      };
      const timer = setTimeout(() => end(timedOut(timeLimitMs)), timeLimitMs);
      worker.onmessage = (ev: MessageEvent<DoneMessage>) => {
        const d = ev.data;
        if (!d || d.type !== "done") return;
        end({
          status: d.failed ? "error" : "ok",
          stdout: String(d.stdout ?? ""),
          stderr: String(d.stderr ?? ""),
          timeMs: Math.round(Number(d.timeMs) || performance.now() - startedAt),
          truncated: !!d.truncated,
        });
      };
      worker.onerror = (ev: ErrorEvent) => {
        // only reached when the script cannot start: a syntax error in the program
        ev.preventDefault();
        end({ status: "error", stdout: "", stderr: syntaxErrorText(ev.message || "SyntaxError", ev.lineno, lineOffset) + "\n", timeMs: 0 });
      };
    });
  }

  dispose(): void {
    this.current?.terminate();
    this.current = null;
  }
}
