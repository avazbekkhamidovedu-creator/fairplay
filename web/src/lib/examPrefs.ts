/**
 * 2026-10-23 exam screen: small things the student sets for one attempt, kept on this device only
 * (never sent to the server and never graded): crossed-out options, the cross-out switch,
 * the hidden timer, and the text size (one setting for every exam).
 */

const crossKey = (aid: string) => `fairplay:crossed:${aid}`;
const crossModeKey = (aid: string) => `fairplay:crossmode:${aid}`;
const timerKey = (aid: string) => `fairplay:timerhidden:${aid}`;
export const TEXT_SIZE_KEY = "fairplay:textsize";

/** Text size steps, in the order the More menu cycles them. */
export const TEXT_SIZES = [1, 1.15, 1.3] as const;
export type TextSize = (typeof TEXT_SIZES)[number];

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: the setting lives in memory only */
  }
}

/** question id -> crossed-out option indexes */
export type Crossed = Record<string, number[]>;

export function readCrossed(aid: string): Crossed {
  try {
    const v = JSON.parse(read(crossKey(aid)) ?? "{}") as unknown;
    if (!v || typeof v !== "object" || Array.isArray(v)) return {};
    const out: Crossed = {};
    for (const [qid, list] of Object.entries(v as Record<string, unknown>)) {
      if (Array.isArray(list)) {
        const nums = list.filter((n): n is number => Number.isInteger(n) && (n as number) >= 0);
        if (nums.length) out[qid] = [...new Set(nums)].sort((a, b) => a - b);
      }
    }
    return out;
  } catch {
    return {};
  }
}

export function writeCrossed(aid: string, crossed: Crossed): void {
  const clean = Object.fromEntries(Object.entries(crossed).filter(([, l]) => l.length > 0));
  write(crossKey(aid), Object.keys(clean).length ? JSON.stringify(clean) : null);
}

/** Toggle one option; returns a new map. */
export function toggleCrossed(crossed: Crossed, qid: string, index: number): Crossed {
  const cur = new Set(crossed[qid] ?? []);
  if (cur.has(index)) cur.delete(index);
  else cur.add(index);
  return { ...crossed, [qid]: [...cur].sort((a, b) => a - b) };
}

/** Remove one option from the crossed list (used when the student chooses it). */
export function uncross(crossed: Crossed, qid: string, index: number): Crossed {
  if (!(crossed[qid] ?? []).includes(index)) return crossed;
  return { ...crossed, [qid]: (crossed[qid] ?? []).filter((i) => i !== index) };
}

export const readCrossMode = (aid: string) => read(crossModeKey(aid)) === "1";
export const writeCrossMode = (aid: string, on: boolean) => write(crossModeKey(aid), on ? "1" : null);

export const readTimerHidden = (aid: string) => read(timerKey(aid)) === "1";
export const writeTimerHidden = (aid: string, hidden: boolean) => write(timerKey(aid), hidden ? "1" : null);

export function readTextSize(): TextSize {
  const n = Number(read(TEXT_SIZE_KEY));
  return (TEXT_SIZES as readonly number[]).includes(n) ? (n as TextSize) : 1;
}

export function writeTextSize(size: TextSize): void {
  write(TEXT_SIZE_KEY, size === 1 ? null : String(size));
}

export function nextTextSize(size: TextSize): TextSize {
  const i = TEXT_SIZES.indexOf(size);
  return TEXT_SIZES[(i + 1) % TEXT_SIZES.length] ?? 1;
}

/** The timer is shown again, whatever the student chose, in the last 5 minutes. */
export const TIMER_FORCE_SHOW_MS = 5 * 60 * 1000;
