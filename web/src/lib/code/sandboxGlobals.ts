// 2026-10-23: what a student's program may not reach from a runner worker. A worker has the
// page's origin, so fetch / XMLHttpRequest could call the exam server as the student (the
// CSP only allows 'self'), and storage / channels / nested workers are ways out too. They
// are removed from the worker's global scope and from its prototypes (WorkerGlobalScope.
// prototype.fetch) before the program runs. Dynamic import() of the server's own scripts
// cannot be removed this way; it gives the program nothing it cannot already do.

export const BLOCKED_GLOBALS = [
  "fetch",
  "XMLHttpRequest",
  "WebSocket",
  "WebSocketStream",
  "WebTransport",
  "EventSource",
  "indexedDB",
  "caches",
  "importScripts",
  "Worker",
  "SharedWorker",
  "BroadcastChannel",
  "Request",
  "Notification",
  "requestFileSystem",
  "webkitRequestFileSystem",
  "requestFileSystemSync",
  "webkitRequestFileSystemSync",
  "cookieStore",
] as const;

// Plain loops only: this text is also pasted into the JavaScript runner's worker script.
const LOCK_BODY = `
  for (var i = 0; i < names.length; i++) {
    var name = names[i];
    var o = scope;
    while (o) {
      try { if (Object.prototype.hasOwnProperty.call(o, name)) delete o[name]; } catch (e) {}
      o = Object.getPrototypeOf(o);
    }
    try { Object.defineProperty(scope, name, { value: undefined, writable: false, configurable: false }); } catch (e) {}
  }
  var nav = scope.navigator;
  if (nav) {
    var keys = ["storage", "locks", "serviceWorker", "sendBeacon"];
    for (var j = 0; j < keys.length; j++) {
      var p = nav;
      while (p) {
        try { if (Object.prototype.hasOwnProperty.call(p, keys[j])) delete p[keys[j]]; } catch (e) {}
        p = Object.getPrototypeOf(p);
      }
    }
  }
`;

/** Source of a statement that locks `scope` (for the JavaScript worker script). */
export function lockGlobalsSource(scopeExpr: string): string {
  return `(function (scope, names) {${LOCK_BODY}})(${scopeExpr}, ${JSON.stringify(BLOCKED_GLOBALS)});`;
}

/** The same, for the Python worker (after Pyodide has loaded). */
export function lockGlobals(scope: object): void {
  const names: readonly string[] = BLOCKED_GLOBALS;
  for (const name of names) {
    let o: object | null = scope;
    while (o) {
      try {
        if (Object.prototype.hasOwnProperty.call(o, name)) delete (o as Record<string, unknown>)[name];
      } catch {
        /* not removable: shadowed below */
      }
      o = Object.getPrototypeOf(o) as object | null;
    }
    try {
      Object.defineProperty(scope, name, { value: undefined, writable: false, configurable: false });
    } catch {
      /* already locked */
    }
  }
  const nav = (scope as { navigator?: object }).navigator;
  if (nav) {
    for (const key of ["storage", "locks", "serviceWorker", "sendBeacon"]) {
      let p: object | null = nav;
      while (p) {
        try {
          if (Object.prototype.hasOwnProperty.call(p, key)) delete (p as Record<string, unknown>)[key];
        } catch {
          /* ignore */
        }
        p = Object.getPrototypeOf(p) as object | null;
      }
    }
  }
}
