// SQL runs: one fresh in-memory SQLite database per run, the test's setup script first, then
// the student's SQL. The output is the result of the last statement that returns rows (see
// formatRows). Pure over an initialised sqlite-wasm object, so it runs in the worker and in
// Node tests alike.

interface StmtLike {
  readonly columnCount: number;
  step(): boolean;
  get(target: unknown[]): unknown[];
  getColumnNames(target: string[]): string[];
  finalize(): void;
}

interface DbLike {
  prepare(sql: string): StmtLike;
  close(): void;
}

export interface SqliteLike {
  oo1: { DB: new (filename: string, flags: string) => DbLike };
  capi: {
    SQLITE_LIMIT_ATTACHED: number;
    SQLITE_LIMIT_LENGTH: number;
    SQLITE_LIMIT_SQL_LENGTH: number;
    sqlite3_limit(db: unknown, id: number, value: number): number;
    sqlite3_complete(sql: string): number;
  };
}

export interface SqlResult {
  stdout: string;
  stderr: string;
  failed: boolean;
  truncated: boolean;
}

/** Longest string or blob SQLite may build, and longest statement (bytes). */
const MAX_VALUE_BYTES = 8 * 1024 * 1024;
const MAX_SQL_BYTES = 1024 * 1024;

export function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "NULL";
  if (v instanceof Uint8Array) return Array.from(v, (b) => b.toString(16).padStart(2, "0")).join("");
  return String(v);
}

/** First line the column names, then one line per row, values joined by TAB. */
export function formatRows(columns: string[], rows: unknown[][]): string {
  const lines = [columns.join("\t")];
  for (const r of rows) lines.push(r.map(formatValue).join("\t"));
  return lines.join("\n") + "\n";
}

function splitStatements(sqlite3: SqliteLike, text: string): string[] {
  const out: string[] = [];
  const parts = text.split(";");
  let cur = "";
  for (let i = 0; i < parts.length; i++) {
    const last = i === parts.length - 1;
    cur += parts[i] + (last ? "" : ";");
    if (!last && sqlite3.capi.sqlite3_complete(cur)) {
      out.push(cur);
      cur = "";
    }
  }
  if (cur.trim() !== "") out.push(cur);
  return out;
}

export function sqlErrorText(e: unknown): string {
  const m = e instanceof Error ? e.message : String(e);
  return m.replace(/^SQLITE_\w+: sqlite3 result code \d+: /, "").replace(/^SQLite3Error: /, "");
}

function cap(s: string, max: number): { text: string; cut: boolean } {
  return s.length > max ? { text: s.slice(0, max), cut: true } : { text: s, cut: false };
}

/** Runs `setup` then `code` on a new database that is closed afterwards. */
export function runSql(sqlite3: SqliteLike, setup: string, code: string, maxOutput: number): SqlResult {
  let db: DbLike | null = null;
  let cols: string[] | null = null;
  let rows: unknown[][] = [];
  let cut = false;
  try {
    db = new sqlite3.oo1.DB(":memory:", "c");
    const { capi } = sqlite3;
    // ATTACH reaches other databases (files); there are none to open, so refuse it outright
    capi.sqlite3_limit(db, capi.SQLITE_LIMIT_ATTACHED, 0);
    capi.sqlite3_limit(db, capi.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES);
    capi.sqlite3_limit(db, capi.SQLITE_LIMIT_SQL_LENGTH, MAX_SQL_BYTES);
    for (const [text, student] of [[setup, false] as const, [code, true] as const]) {
      for (const piece of splitStatements(sqlite3, text)) {
        let stmt: StmtLike;
        try {
          stmt = db.prepare(piece);
        } catch (e) {
          if (/empty SQL/i.test(String(e instanceof Error ? e.message : e))) continue; // only comments
          throw e;
        }
        try {
          if (stmt.columnCount > 0) {
            const names = stmt.getColumnNames([]);
            const got: unknown[][] = [];
            let size = 0;
            while (stmt.step()) {
              if (cut) continue;
              const row = stmt.get([]);
              got.push(row);
              size += row.length * 2;
              for (const v of row) size += typeof v === "string" ? v.length : v instanceof Uint8Array ? v.length * 2 : 8;
              if (size > maxOutput) cut = true;
            }
            if (student) {
              cols = names;
              rows = got;
            }
          } else {
            while (stmt.step()) {
              /* a statement without columns has no rows */
            }
          }
        } finally {
          stmt.finalize();
        }
      }
    }
  } catch (e) {
    return { stdout: "", stderr: `Error: ${sqlErrorText(e)}\n`, failed: true, truncated: false };
  } finally {
    try {
      db?.close();
    } catch {
      /* the worker is discarded on trouble anyway */
    }
  }
  if (!cols) return { stdout: "", stderr: "", failed: false, truncated: false };
  const c = cap(formatRows(cols, rows), maxOutput);
  return { stdout: c.text, stderr: "", failed: false, truncated: c.cut || cut };
}
