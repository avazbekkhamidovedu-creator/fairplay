// Times the runtimes behind the code runners, in Node: sqlite-wasm start-up and 100 queries,
// a sucrase transform of a 200-line TypeScript file, Pyodide load and a run.
// Usage: node scripts/bench-runners.mjs   (browser numbers differ: measure there too)
import { performance } from "node:perf_hooks";

const rows = [];
async function time(name, detail, fn) {
  const t0 = performance.now();
  const extra = await fn();
  rows.push({ step: name, ms: (performance.now() - t0).toFixed(1), detail: extra ?? detail });
}

const quiet = () => undefined;

const { default: sqliteInit } = await import("@sqlite.org/sqlite-wasm");
let sqlite3;
await time("sqlite-wasm init", "wasm compile + instantiate", async () => {
  sqlite3 = await sqliteInit({ print: quiet, printErr: quiet });
});
await time("sqlite 100 queries", "fresh :memory: db, 20 rows setup, 1 select each", async () => {
  for (let i = 0; i < 100; i++) {
    const db = new sqlite3.oo1.DB(":memory:", "c");
    db.exec("create table t(a integer, b text); with recursive c(n) as (select 1 union all select n + 1 from c where n < 20) insert into t select n, 'v' || n from c;");
    db.exec({ sql: "select a, b from t where a % 3 = 0 order by b", rowMode: "array" });
    db.close();
  }
});

const { transform } = await import("sucrase");
const line = (i) =>
  `export function f${i}<T extends { id: number }>(items: T[], k: number): Array<T & { rank: number }> { const r: number[] = []; for (const x of items) r.push(x.id * k); return items.map((v, j) => ({ ...v, rank: r[j] as number })); }`;
const tsSource = Array.from({ length: 200 }, (_, i) => line(i)).join("\n");
await time("sucrase 200 lines", `${tsSource.length} chars, typescript transform`, async () => {
  transform(tsSource, { transforms: ["typescript"], disableESTransforms: true });
});
await time("sucrase 200 lines (2nd)", "warm", async () => {
  transform(tsSource, { transforms: ["typescript"], disableESTransforms: true });
});

const { loadPyodide } = await import("pyodide");
let py;
await time("pyodide load", "loadPyodide() from node_modules", async () => {
  py = await loadPyodide();
});
await time("pyodide run", "print(sum(range(1000)))", async () => {
  py.runPython("print(sum(range(1000)))");
});
await time("pyodide run 100x", "100 small runs", async () => {
  for (let i = 0; i < 100; i++) py.runPython("x = [i * i for i in range(100)]");
});

console.log(`node ${process.version}, ${process.platform}/${process.arch}`);
console.table(rows);
