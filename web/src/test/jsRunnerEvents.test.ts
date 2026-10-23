// 2026-10-23 (review fixes): the JavaScript runner waits for timers / rl.question / input
// events like Node, supports process.stdin templates and process.exit codes, and formats
// console.log output like Node's util.inspect.
import { runInNewContext } from "node:vm";
import { afterEach, describe, expect, it } from "vitest";
import { buildJsWorkerSource } from "../lib/code/jsRunner";

interface Done {
  stdout: string;
  stderr: string;
  failed: boolean;
  truncated: boolean;
}

const liveIntervals = new Set<ReturnType<typeof setInterval>>();
const liveTimeouts = new Set<ReturnType<typeof setTimeout>>();
afterEach(() => {
  for (const id of liveIntervals) clearInterval(id);
  for (const id of liveTimeouts) clearTimeout(id);
  liveIntervals.clear();
  liveTimeouts.clear();
});

/** Runs the worker script in a vm context; resolves with the "done" message, or null when
 *  none arrives within `waitMs` (the page's time limit would stop such a program). */
function runJs(code: string, stdin = "", waitMs = 1500): Promise<Done | null> {
  const { source } = buildJsWorkerSource(code, stdin);
  return new Promise((resolve, reject) => {
    const listeners: Record<string, ((e: unknown) => void)[]> = {};
    let finished = false;
    const ctx: Record<string, unknown> = {
      postMessage: (m: Done) => {
        if (finished) return reject(new Error("done posted twice"));
        finished = true;
        resolve(m);
      },
      setTimeout: (cb: () => void, ms: number) => {
        const id = setTimeout(() => {
          liveTimeouts.delete(id);
          cb();
        }, ms);
        liveTimeouts.add(id);
        return id;
      },
      clearTimeout: (id: ReturnType<typeof setTimeout>) => {
        liveTimeouts.delete(id);
        clearTimeout(id);
      },
      setInterval: (cb: () => void, ms: number) => {
        const id = setInterval(cb, ms);
        liveIntervals.add(id);
        return id;
      },
      clearInterval: (id: ReturnType<typeof setInterval>) => {
        liveIntervals.delete(id);
        clearInterval(id);
      },
      performance,
      Promise,
      BigInt,
    };
    ctx.self = { addEventListener: (t: string, f: (e: unknown) => void) => (listeners[t] ??= []).push(f) };
    try {
      runInNewContext(source, ctx, { filename: "blob:http://x/1" });
    } catch (e) {
      reject(e);
    }
    setTimeout(() => {
      if (!finished) {
        finished = true;
        resolve(null);
      }
    }, waitMs);
  });
}

async function out(code: string, stdin = ""): Promise<Done> {
  const r = await runJs(code, stdin);
  if (!r) throw new Error("no done message");
  return r;
}

describe("JavaScript runner: pending work", () => {
  it("waits for a chain of rl.question callbacks", async () => {
    const code = [
      "const rl = require('readline').createInterface({ input: process.stdin, output: process.stdout });",
      "rl.question('a? ', (a) => { rl.question('b? ', (b) => { rl.question('c? ', (c) => {",
      "  rl.question('d? ', (d) => { console.log(a, b, c, d); rl.close(); });",
      "}); }); });",
    ].join("\n");
    expect(await out(code, "1\n2\n3\n4\n")).toMatchObject({ stdout: "a? b? c? d? 1 2 3 4\n", failed: false });
  });

  it("waits for timers, nested timers and intervals that are cleared", async () => {
    expect((await out("setTimeout(() => console.log('late'), 30); console.log('early');")).stdout).toBe("early\nlate\n");
    const nested = "setTimeout(() => { console.log(1); setTimeout(() => { console.log(2); setTimeout(() => console.log(3), 5); }, 5); }, 5);";
    expect((await out(nested)).stdout).toBe("1\n2\n3\n");
    const iv = "let n = 0; const id = setInterval(() => { n++; console.log('tick', n); if (n === 3) clearInterval(id); }, 5);";
    expect((await out(iv)).stdout).toBe("tick 1\ntick 2\ntick 3\n");
    expect((await out("const t = setTimeout(() => console.log('no'), 50); clearTimeout(t); console.log('yes');")).stdout).toBe("yes\n");
    expect((await out("await new Promise((r) => setTimeout(r, 20)); console.log('done');")).stdout).toBe("done\n");
    expect((await out("setImmediate(() => console.log('i')); process.nextTick(() => console.log('t'));")).stdout).toBe("t\ni\n");
  });

  it("a timer after the input events still prints", async () => {
    const code = [
      "const rl = require('readline').createInterface({ input: process.stdin });",
      "const L = [];",
      "rl.on('line', (l) => L.push(l));",
      "rl.on('close', () => setTimeout(() => console.log(L.join('+')), 10));",
    ].join("\n");
    expect((await out(code, "1\r\n2\r\n3")).stdout).toBe("1+2+3\n");
  });

  it("awaiting the 'close' event works (input is delivered while the main code waits)", async () => {
    const code = [
      "const rl = require('readline').createInterface({ input: process.stdin });",
      "const L = [];",
      "rl.on('line', (l) => L.push(Number(l)));",
      "await new Promise((r) => rl.on('close', r));",
      "console.log(L.reduce((a, b) => a + b, 0));",
    ].join("\n");
    expect((await out(code, "4\n5\n")).stdout).toBe("9\n");
  });

  it("an interval that never stops does not finish (the time limit ends it)", async () => {
    expect(await runJs("setInterval(() => {}, 5);", "", 200)).toBeNull();
  });

  it("an error in a timer ends the run with what was printed so far", async () => {
    const r = await out("console.log('a'); setTimeout(() => { null.f(); }, 0); setTimeout(() => console.log('never'), 30);");
    expect(r.failed).toBe(true);
    expect(r.stdout).toBe("a\n");
    expect(r.stderr).toMatch(/^TypeError/);
  });
});

describe("JavaScript runner: process.stdin templates", () => {
  it("resume / setEncoding / on('data') / on('end')", async () => {
    const code = [
      "process.stdin.resume();",
      "process.stdin.setEncoding('utf8');",
      "let input = '';",
      "process.stdin.on('data', (c) => { input += c; });",
      "process.stdin.on('end', () => { const xs = input.trim().split('\\n').map(Number); console.log(xs.reduce((a, b) => a + b)); });",
    ].join("\n");
    expect(await out(code, "1\r\n2\r\n3\r\n")).toMatchObject({ stdout: "6\n", failed: false });
  });

  it(".once, 'readable' + read(), for await and readFileSync('/dev/stdin')", async () => {
    expect((await out("process.stdin.once('data', (d) => console.log(JSON.stringify(d)));", "a\nb\n")).stdout).toBe('"a\\nb\\n"\n');
    const readable = "let s = ''; process.stdin.on('readable', () => { let c; while ((c = process.stdin.read()) !== null) s += c; }); process.stdin.on('end', () => console.log(s.length));";
    expect((await out(readable, "abc\n")).stdout).toBe("4\n");
    expect((await out("let s = ''; for await (const c of process.stdin) s += c; console.log(s.trim());", "xy\n")).stdout).toBe("xy\n");
    expect((await out("console.log(require('fs').readFileSync('/dev/stdin', 'utf8').split('\\n').length)", "1\n2\n")).stdout).toBe("3\n");
    expect((await out("console.log(require('fs').readFileSync(0).toString().trim())", "hi\n")).stdout).toBe("hi\n");
  });

  it("no input: 'end' still fires", async () => {
    expect((await out("process.stdin.on('data', () => console.log('d')); process.stdin.on('end', () => console.log('end'));")).stdout).toBe("end\n");
  });
});

describe("JavaScript runner: process.exit", () => {
  it("exit(0) stops the program and keeps the output so far", async () => {
    expect(await out("console.log('a'); process.exit(0); console.log('b');")).toMatchObject({ stdout: "a\n", failed: false });
    expect(await out("console.log('a'); process.exit(); console.log('b');")).toMatchObject({ stdout: "a\n", failed: false });
  });

  it("exit inside a callback stops pending work", async () => {
    const r = await out("setTimeout(() => { console.log('x'); process.exit(0); }, 0); setTimeout(() => console.log('y'), 30);");
    expect(r).toMatchObject({ stdout: "x\n", failed: false });
  });

  it("a non-zero code or process.exitCode is a runtime error", async () => {
    expect(await out("console.log('a'); process.exit(3); console.log('b');")).toMatchObject({ stdout: "a\n", failed: true });
    expect(await out("setTimeout(() => process.exit(1), 0);")).toMatchObject({ failed: true });
    expect(await out("console.log('a'); process.exitCode = 2;")).toMatchObject({ stdout: "a\n", failed: true });
    expect(await out("process.exitCode = 0; console.log('ok');")).toMatchObject({ stdout: "ok\n", failed: false });
  });
});

describe("JavaScript runner: console.log like Node", () => {
  // the expected strings were printed by Node 22's console.log
  const cases: Array<[string, string]> = [
    ["[1, 'a']", "[ 1, 'a' ]"],
    ["{ a: 1, b: 'x' }", "{ a: 1, b: 'x' }"],
    ["[1, 'a', [2]], { x: 1 }, null, undefined, 2n, true", "[ 1, 'a', [ 2 ] ] { x: 1 } null undefined 2n true"],
    ["[[1, [2, [3, [4]]]]]", "[ [ 1, [ 2, [Array] ] ] ]"],
    ["{ a: { b: { c: { d: 1 } } } }", "{ a: { b: { c: [Object] } } }"],
    ["[undefined, null, -0, 'str']", "[ undefined, null, -0, 'str' ]"],
    ["[], {}, [[]], { a: [] }", "[] {} [ [] ] { a: [] }"],
    ["{ 'a-b': \"it's\", c: 'x\"y' }", "{ 'a-b': \"it's\", c: 'x\"y' }"],
    ["['a\\nb']", "[ 'a\\nb' ]"],
    ["new Map([['a', 1], ['b', [2]]])", "Map(2) { 'a' => 1, 'b' => [ 2 ] }"],
    ["new Set([1, 'x']), new Map(), new Set()", "Set(2) { 1, 'x' } Map(0) {} Set(0) {}"],
    ["[function g() {}, () => 1]", "[ [Function: g], [Function (anonymous)] ]"],
    ["'plain', 5, 'text'", "plain 5 text"],
    ["[\"a'b\\\"c\"]", "[ `a'b\"c` ]"],
  ];
  for (const [args, expected] of cases) {
    it(`console.log(${args})`, async () => {
      expect((await out(`console.log(${args});`)).stdout).toBe(expected + "\n");
    });
  }
});
