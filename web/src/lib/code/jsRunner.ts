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
// 2026-10-23 (review): the run ends like Node's event loop: when no timer, interval or
// rl.question answer is pending and the input events (readline 'line' / 'close', stdin
// 'data' / 'end') have been delivered; a program that never goes idle hits the time limit.
// process.stdin supports the usual on('data') / on('end') templates, process.exit(code)
// stops the program (a non-zero code, or process.exitCode, is an error), and console.log
// formats values like Node's util.inspect for the common cases (one line only).
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
  var realSetTimeout = setTimeout;
  var realClearTimeout = typeof clearTimeout === "function" ? clearTimeout : function () {};
  var realSetInterval = typeof setInterval === "function" ? setInterval : null;
  var realClearInterval = typeof clearInterval === "function" ? clearInterval : function () {};
  function quote(s) {
    var q = s.indexOf("'") < 0 ? "'" : s.indexOf('"') < 0 ? '"' : s.indexOf("\\u0060") < 0 ? "\\u0060" : "'";
    var body = JSON.stringify(s).slice(1, -1).replace(/\\\\"/g, '"');
    if (q !== '"') body = body.split(q).join("\\\\" + q);
    return q + body + q;
  }
  function key(k) { return /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(k) ? k : quote(k); }
  function show(x, depth, seen) {
    depth = depth || 0;
    if (typeof x === "string") return depth ? quote(x) : x;
    if (typeof x === "number") return Object.is(x, -0) ? "-0" : String(x);
    if (typeof x === "bigint") return String(x) + "n";
    if (x === null || x === undefined || typeof x === "boolean") return String(x);
    if (typeof x === "function") {
      if (/^class[\\s{]/.test(Function.prototype.toString.call(x))) return "[class " + (x.name || "(anonymous)") + "]";
      return "[Function" + (x.name ? ": " + x.name : " (anonymous)") + "]";
    }
    if (typeof x === "symbol") return x.toString();
    if (x instanceof Error) return depth ? "[" + (x.stack || String(x)) + "]" : x.stack || String(x);
    if (x instanceof Date) return isNaN(x.getTime()) ? "Invalid Date" : x.toISOString();
    if (x instanceof RegExp) return String(x);
    seen = seen || [];
    if (seen.indexOf(x) >= 0) return "[Circular *1]";
    if (depth > 2) return Array.isArray(x) ? "[Array]" : "[Object]";
    seen = seen.concat([x]);
    function inner(v) { return show(v, depth + 1, seen); }
    function wrap(prefix, items, open, close) {
      return (prefix ? prefix + " " : "") + (items.length ? open + " " + items.join(", ") + " " + close : open + close);
    }
    try {
      if (Array.isArray(x)) {
        var items = x.slice(0, 100).map(inner);
        if (x.length > 100) items.push("... " + (x.length - 100) + " more item" + (x.length - 100 > 1 ? "s" : ""));
        return wrap("", items, "[", "]");
      }
      if (x instanceof Map) return wrap("Map(" + x.size + ")", Array.from(x).map(function (kv) { return inner(kv[0]) + " => " + inner(kv[1]); }), "{", "}");
      if (x instanceof Set) return wrap("Set(" + x.size + ")", Array.from(x).map(inner), "{", "}");
      var proto = Object.getPrototypeOf(x);
      var prefix = proto === null ? "[Object: null prototype]" : proto !== Object.prototype && proto.constructor && proto.constructor.name && proto.constructor.name !== "Object" ? proto.constructor.name : "";
      return wrap(prefix, Object.keys(x).map(function (k) { return key(k) + ": " + inner(x[k]); }), "{", "}");
    } catch (e) { return String(x); }
  }
  function line(args) { return Array.prototype.map.call(args, function (a) { return show(a, 0); }).join(" ") + "\\n"; }
  var text = stdin.replace(/\\r\\n?/g, "\\n");
  var lines = text.split("\\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  var next = 0;
  function readline() { return next < lines.length ? lines[next++] : null; }
  var done = false, failed = false, exitCode = null;
  // Work the program still waits for (timers, rl.question answers). The run ends when none
  // is left and the input events have been delivered, like Node's event loop.
  var pending = 0, idleQueued = false;
  function settle() { if (pending <= 0 && !idleQueued && !done) { idleQueued = true; realSetTimeout(idle, 0); } }
  function guard(cb, args) {
    if (done) return;
    try { cb.apply(null, args || []); } catch (e) { fail(e); }
  }
  var timers = {}, intervals = {};
  function setT(cb, ms) {
    var args = Array.prototype.slice.call(arguments, 2);
    if (typeof cb !== "function") return 0;
    pending++;
    var id = realSetTimeout(function () { if (!timers[id]) return; delete timers[id]; pending--; guard(cb, args); settle(); }, ms);
    timers[id] = true;
    return id;
  }
  function clearT(id) {
    if (timers[id]) { delete timers[id]; pending--; realClearTimeout(id); settle(); }
    else if (intervals[id]) clearI(id);
  }
  function setI(cb, ms) {
    var args = Array.prototype.slice.call(arguments, 2);
    if (typeof cb !== "function") return 0;
    if (!realSetInterval) return setT(cb, ms);
    pending++;
    var id = realSetInterval(function () { if (intervals[id]) guard(cb, args); }, ms);
    intervals[id] = true;
    return id;
  }
  function clearI(id) {
    if (intervals[id]) { delete intervals[id]; pending--; realClearInterval(id); settle(); }
    else if (timers[id]) clearT(id);
  }
  function emit(list, args) { var cbs = list.slice(); for (var i = 0; i < cbs.length && !done; i++) guard(cbs[i], args); }
  var listeners = { line: [], close: [] };
  var rlClosed = false;
  var rl = {
    on: function (ev, cb) { if (listeners[ev] && typeof cb === "function") listeners[ev].push(cb); return rl; },
    once: function (ev, cb) { return rl.on(ev, cb); },
    addListener: function (ev, cb) { return rl.on(ev, cb); },
    close: function () { if (rlClosed) return; rlClosed = true; emit(listeners.close, []); },
    question: function (q, opts, cb) {
      if (typeof opts === "function") cb = opts;
      put(0, q);
      var l = readline();
      pending++;
      realSetTimeout(function () { pending--; if (typeof cb === "function") guard(cb, [l === null ? "" : l]); settle(); }, 0);
    },
    setPrompt: function () {}, prompt: function () {}, write: function () {}, pause: function () { return rl; }, resume: function () { return rl; }
  };
  rl[Symbol.asyncIterator] = function () {
    return { next: function () { var l = readline(); return Promise.resolve(l === null ? { value: undefined, done: true } : { value: l, done: false }); } };
  };
  var inListeners = { data: [], end: [], readable: [], close: [] };
  var inEnded = false, inRead = false;
  var stdinObj = {
    fd: 0, isTTY: false,
    on: function (ev, cb) { if (inListeners[ev] && typeof cb === "function") inListeners[ev].push(cb); return stdinObj; },
    once: function (ev, cb) { return stdinObj.on(ev, cb); },
    addListener: function (ev, cb) { return stdinObj.on(ev, cb); },
    removeListener: function (ev, cb) { var l = inListeners[ev]; if (l && l.indexOf(cb) >= 0) l.splice(l.indexOf(cb), 1); return stdinObj; },
    off: function (ev, cb) { return stdinObj.removeListener(ev, cb); },
    removeAllListeners: function () { for (var k in inListeners) inListeners[k] = []; return stdinObj; },
    resume: function () { return stdinObj; }, pause: function () { return stdinObj; },
    setEncoding: function () { return stdinObj; }, unref: function () { return stdinObj; }, ref: function () { return stdinObj; },
    read: function () { if (inRead) return null; inRead = true; return text; }
  };
  stdinObj[Symbol.asyncIterator] = function () {
    var given = false;
    return { next: function () { var r = given ? { value: undefined, done: true } : { value: text, done: false }; given = true; return Promise.resolve(r); } };
  };
  // Input events (readline 'line' / 'close', stdin 'data' / 'end') arrive once the program's
  // own code has nothing left to do, as stdin does in Node after the main script.
  function deliverInput() {
    var any = false;
    if (!rlClosed && (listeners.line.length || listeners.close.length)) {
      any = true;
      var l;
      if (listeners.line.length) while (!done && !rlClosed && (l = readline()) !== null) emit(listeners.line, [l]);
      rl.close();
    }
    if (!done && !inEnded && (inListeners.data.length || inListeners.end.length || inListeners.readable.length)) {
      any = true;
      inEnded = true;
      if (text.length) emit(inListeners.data, [text]);
      if (!done) emit(inListeners.readable, []);
      if (!done) emit(inListeners.end, []);
      if (!done) emit(inListeners.close, []);
    }
    return any;
  }
  // 2026-10-23: before finishing, wait one more task so an unhandled promise rejection
  // (reported after the microtasks of the previous task) still fails the run, as in Node.
  var lastLook = false;
  function idle() {
    idleQueued = false;
    if (done || pending > 0) { lastLook = false; return; }
    if (deliverInput()) { lastLook = false; settle(); return; }
    if (!lastLook) { lastLook = true; idleQueued = true; realSetTimeout(idle, 0); return; }
    finish();
  }
  var exit = { fairplayExit: true };
  var fs = { readFileSync: function (f, enc) {
    if (f === 0 || f === "/dev/stdin") { var rest = lines.slice(next).join("\\n"); next = lines.length; return rest + (text.endsWith("\\n") ? "\\n" : ""); }
    throw new Error("ENOENT: files are not available in the exam: " + String(f));
  } };
  var readlineModule = { createInterface: function () { return rl; } };
  var con = {
    log: function () { put(0, line(arguments)); }, info: function () { put(0, line(arguments)); },
    debug: function () { put(0, line(arguments)); }, warn: function () { put(1, line(arguments)); },
    error: function () { put(1, line(arguments)); }, table: function (x) { put(0, show(x, 0) + "\\n"); },
    dir: function (x) { put(0, show(x, 1) + "\\n"); }, trace: function () { put(1, line(arguments)); },
    assert: function (c) { if (!c) put(1, "Assertion failed\\n"); }, time: function () {}, timeEnd: function () {}
  };
  var proc = {
    argv: ["node", "main.js"], env: {}, platform: "browser", exitCode: undefined,
    stdout: { write: function (s) { put(0, s); return true; } },
    stderr: { write: function (s) { put(1, s); return true; } },
    stdin: stdinObj,
    exit: function (code) { exitCode = code === undefined ? proc.exitCode : code; throw exit; },
    on: function () { return proc; },
    hrtime: { bigint: function () { return BigInt(Math.round(performance.now() * 1e6)); } },
    nextTick: function (cb) { var args = Array.prototype.slice.call(arguments, 1); Promise.resolve().then(function () { guard(cb, args); }); },
    memoryUsage: function () { return { heapUsed: 0, rss: 0 }; }
  };
  function req(name) {
    name = String(name).replace(/^node:/, "");
    if (name === "fs") return fs;
    if (name === "readline") return readlineModule;
    throw new Error("Cannot find module '" + name + "' (only fs and readline are available)");
  }
  function where(e) {
    var m = /:(\\d+):(\\d+)\\)?\\s*$/m.exec(String(e && e.stack || "").split("\\n").filter(function (l) { return l.indexOf("blob:") >= 0; })[0] || "");
    var n = m ? Number(m[1]) - __FP_OFFSET__ : 0;
    return n > 0 ? " (line " + n + ")" : "";
  }
  // An uncaught error or process.exit() ends the program at once, as in Node.
  function fail(e) {
    if (done) return;
    if (e !== exit) {
      failed = true;
      var t = e instanceof Error ? (e.name || "Error") + ": " + e.message + where(e) : "Uncaught " + show(e, 0);
      put(1, t + "\\n");
    }
    finish();
  }
  function finish() {
    if (done) return;
    done = true;
    var code = exitCode === null ? proc.exitCode : exitCode;
    var n = code === undefined || code === null ? 0 : Number(code);
    if (n !== 0 || n !== n) failed = true;
    postMessage({ type: "done", stdout: out.join(""), stderr: err.join(""), failed: failed, truncated: cut, timeMs: performance.now() - started });
  }
  self.addEventListener("error", function (ev) { ev.preventDefault(); fail(ev.error || new Error(ev.message)); });
  self.addEventListener("unhandledrejection", function (ev) { ev.preventDefault(); fail(ev.reason); });
  var started = performance.now();
  return {
    readline: readline, console: con, process: proc, require: req,
    setTimeout: setT, clearTimeout: clearT, setInterval: setI, clearInterval: clearI,
    setImmediate: function (cb) { return setT.apply(null, [cb, 0].concat(Array.prototype.slice.call(arguments, 1))); },
    clearImmediate: clearT,
    start: function (main) {
      started = performance.now();
      var p;
      try { p = main(); } catch (e) { fail(e); return; }
      Promise.resolve(p).then(function () { settle(); }, function (e) { fail(e); });
      settle();
    }
  };
})();
var readline = __fp.readline, input = __fp.readline, prompt = __fp.readline;
var console = __fp.console, process = __fp.process, require = __fp.require;
// assigned, not declared: a global var would hide the worker's own timers (they live on its
// prototype) before the runner has taken them
["setTimeout", "clearTimeout", "setInterval", "clearInterval", "setImmediate", "clearImmediate"].forEach(function (k) { globalThis[k] = __fp[k]; });
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
  /** ends the run in progress (dispose) */
  private stopCurrent: (() => void) | null = null;

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
        if (this.stopCurrent === stop) this.stopCurrent = null;
        resolve(r);
      };
      const stop = () => end(unavailable("Stopped."));
      this.stopCurrent = stop;
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

  /** Stops the run in progress (it resolves as "Stopped.") and frees the worker. */
  dispose(): void {
    this.stopCurrent?.();
    this.current?.terminate();
    this.current = null;
  }
}
