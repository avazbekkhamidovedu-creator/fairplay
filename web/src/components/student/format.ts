// Small formatting helpers for the student pages.

export function fmtNum(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return Number.isInteger(n) ? String(n) : String(parseFloat(n.toFixed(4)));
}

export function fmtPercent(p: number | null | undefined): string {
  if (p === null || p === undefined || !Number.isFinite(p)) return "—";
  return `${Math.round(p)}%`;
}

function date(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "Oct 2" */
export function shortDate(iso: string | null | undefined): string {
  const d = date(iso);
  return d ? d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "";
}

/** "14:00" */
export function shortTime(iso: string | null | undefined): string {
  const d = date(iso);
  return d ? d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "";
}

/** "15:17:22" */
export function clockTime(iso: string | null | undefined): string {
  const d = date(iso);
  return d ? d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
}

export function monthDay(iso: string | null | undefined): { m: string; d: string } | null {
  const d = date(iso);
  return d ? { m: d.toLocaleDateString("en-US", { month: "short" }), d: String(d.getDate()) } : null;
}

export function isToday(iso: string | null | undefined): boolean {
  const d = date(iso);
  if (!d) return false;
  const n = new Date();
  return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate();
}

/** "MATH 201 · Midterm" (title alone when there is no course). */
export function examName(course: string | null | undefined, title: string): string {
  return course ? `${course} · ${title}` : title;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

export function wordCount(text: string): number {
  const t = text.trim();
  return t ? t.split(/\s+/).length : 0;
}

export const LETTERS = "ABCDEFGH";

export function firstName(name: string | null | undefined): string {
  return (name ?? "").trim().split(/\s+/)[0] ?? "";
}

/** 2026-10-23: "Calculator (graphing) · Materials · 2 websites", or "" when none. */
export function toolsLine(tools: { calculator: string; open_book: boolean; websites: string[] } | undefined | null): string {
  if (!tools) return "";
  const parts: string[] = [];
  if (tools.calculator === "scientific" || tools.calculator === "graphing") parts.push(`Calculator (${tools.calculator})`);
  if (tools.open_book) parts.push("Materials (open book)");
  if (tools.websites.length > 0) parts.push(plural(tools.websites.length, "website"));
  return parts.join(" · ");
}

/** 2026-10-24: "Oct 26, 14:00" */
export function dateTime(iso: string | null | undefined): string {
  const d = shortDate(iso);
  return d ? `${d}, ${shortTime(iso)}` : "";
}

/** 2026-10-24: time left until `iso`: "2 days 3 h", "5 h 10 min", "12 min", "less than a minute"; "" when past. */
export function timeLeft(iso: string | null | undefined, now: number = Date.now()): string {
  const d = date(iso);
  if (!d) return "";
  const ms = d.getTime() - now;
  if (ms <= 0) return "";
  const min = Math.floor(ms / 60000);
  if (min < 1) return "less than a minute";
  const days = Math.floor(min / 1440);
  const h = Math.floor((min % 1440) / 60);
  const m = min % 60;
  if (days > 0) return h > 0 ? `${plural(days, "day")} ${h} h` : plural(days, "day");
  if (h > 0) return m > 0 ? `${h} h ${m} min` : `${h} h`;
  return `${m} min`;
}
