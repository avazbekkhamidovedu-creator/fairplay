// 2026-10-24: the password rules for a new student/teacher password, in one place.
// The server checks the same rules (server/app/integrations/dkuoj/setup.py) and answers
// 400 "Password needs …" when one fails; this copy drives the live checklist.

export const PASSWORD_MIN = 10;

export interface PasswordContext {
  username?: string | null;
  netid?: string | null;
}

export interface PasswordRule {
  id: "length" | "lower" | "upper" | "digit" | "symbol" | "personal";
  /** Checklist text. */
  label: string;
  /** Used in "Your password needs …". */
  need: string;
  test: (pw: string, ctx: PasswordContext) => boolean;
}

const LOWER = /\p{Ll}/u;
const UPPER = /\p{Lu}/u;
const DIGIT = /\p{Nd}/u;
/** Anything that is not a letter, a digit or white space. */
const SYMBOL = /[^\p{L}\p{N}\s]/u;

function isPersonal(pw: string, ctx: PasswordContext): boolean {
  const low = pw.toLowerCase();
  return [ctx.username, ctx.netid].some((x) => !!x && x.trim() !== "" && low === x.trim().toLowerCase());
}

export const PASSWORD_RULES: readonly PasswordRule[] = [
  { id: "length", label: `At least ${PASSWORD_MIN} characters`, need: `at least ${PASSWORD_MIN} characters`, test: (pw) => [...pw].length >= PASSWORD_MIN },
  { id: "lower", label: "A lower-case letter (a–z)", need: "a lower-case letter", test: (pw) => LOWER.test(pw) },
  { id: "upper", label: "An upper-case letter (A–Z)", need: "an upper-case letter", test: (pw) => UPPER.test(pw) },
  { id: "digit", label: "A digit (0–9)", need: "a digit", test: (pw) => DIGIT.test(pw) },
  { id: "symbol", label: "A symbol, like ! ? # - or _", need: "a symbol", test: (pw) => SYMBOL.test(pw) },
  {
    id: "personal",
    label: "Not your username or NetID",
    need: "to be different from your username and NetID",
    test: (pw, ctx) => pw.length > 0 && !isPersonal(pw, ctx),
  },
];

export interface RuleResult {
  id: PasswordRule["id"];
  label: string;
  ok: boolean;
}

export interface PasswordCheck {
  ok: boolean;
  results: RuleResult[];
  /** null when ok; otherwise one sentence for an error line. */
  message: string | null;
}

function joinNeeds(needs: string[]): string {
  if (needs.length <= 1) return needs.join("");
  return `${needs.slice(0, -1).join(", ")} and ${needs[needs.length - 1]}`;
}

/** Every rule against `pw`. The message matches the server's wording style ("Password needs …"). */
export function checkPassword(pw: string, ctx: PasswordContext = {}): PasswordCheck {
  const results = PASSWORD_RULES.map((r) => ({ id: r.id, label: r.label, ok: r.test(pw, ctx) }));
  const failed = PASSWORD_RULES.filter((_, i) => !results[i]!.ok);
  let message: string | null = null;
  if (failed.length) {
    if (pw.length > 0 && isPersonal(pw, ctx)) message = "Your password can't be your username or NetID.";
    else message = `Password needs ${joinNeeds(failed.filter((r) => r.id !== "personal").map((r) => r.need))}.`;
  }
  return { ok: failed.length === 0, results, message };
}

export type StrengthLevel = 0 | 1 | 2 | 3 | 4;
export const STRENGTH_LABELS: Record<StrengthLevel, string> = {
  0: "",
  1: "Weak",
  2: "Fair",
  3: "Good",
  4: "Strong",
};

const COMMON = ["password", "passw0rd", "qwerty", "letmein", "welcome", "iloveyou", "admin", "dku", "fairplay"];

/** Runs like 1234, abcd, 4321 or dcba (4 or more). */
export function hasSequence(pw: string, run = 4): boolean {
  const s = pw.toLowerCase();
  let up = 1;
  let down = 1;
  for (let i = 1; i < s.length; i++) {
    const d = s.charCodeAt(i) - s.charCodeAt(i - 1);
    const alnum = /[a-z0-9]/.test(s[i]!) && /[a-z0-9]/.test(s[i - 1]!);
    up = alnum && d === 1 ? up + 1 : 1;
    down = alnum && d === -1 ? down + 1 : 1;
    if (up >= run || down >= run) return true;
  }
  return false;
}

/** The same character 3 or more times in a row (aaa, 111). */
export function hasRepeats(pw: string): boolean {
  return /(.)\1\1/u.test(pw);
}

/**
 * Rough strength, 0 (empty) to 4 (strong): length and character classes, minus simple
 * penalties (repeats, sequences, containing the username/NetID or a common word).
 * A password that breaks a rule is never more than "Weak": it would not be accepted.
 */
export function strength(pw: string, ctx: PasswordContext = {}): { level: StrengthLevel; label: string } {
  if (!pw) return { level: 0, label: STRENGTH_LABELS[0] };
  const len = [...pw].length;
  let score = len >= 16 ? 4 : len >= 12 ? 3 : len >= 10 ? 2 : len >= 8 ? 1 : 0;
  const classes = [LOWER, UPPER, DIGIT, SYMBOL].filter((re) => re.test(pw)).length;
  score += Math.max(0, classes - 1);
  if (hasRepeats(pw)) score -= 1;
  if (hasSequence(pw)) score -= 1;
  const low = pw.toLowerCase();
  const personal = [ctx.username, ctx.netid]
    .map((x) => (x ?? "").trim().toLowerCase())
    .filter((x) => x.length >= 3);
  if (personal.some((x) => low.includes(x))) score -= 2;
  if (COMMON.some((w) => low.includes(w))) score -= 2;
  // 0..7 -> 1..4
  let level: StrengthLevel = score >= 7 ? 4 : score >= 5 ? 3 : score >= 3 ? 2 : 1;
  if (!checkPassword(pw, ctx).ok) level = 1;
  return { level, label: STRENGTH_LABELS[level] };
}
