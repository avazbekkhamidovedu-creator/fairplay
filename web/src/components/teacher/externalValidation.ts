// Slice 3: client-side checks for external-website exams. Mirrors the server rules in
// server/app/schemas.py (ExamIn / normalize_domain) so teachers see problems before saving.
import type {
  CanvasInfo,
  CanvasInput,
  CanvasQuizType,
  ExamMode,
  ExternalKind,
} from "../../api/types";

export const MAX_EXTERNAL_URL = 2000;
export const MAX_ALLOWED_DOMAINS = 30;
export const MAX_FINISH_CONTAINS = 200;

/** Same pattern as the server: letters, digits, hyphens, dot-separated labels. */
const DOMAIN_RE = /^[a-z0-9-]+(\.[a-z0-9-]+)*$/;

export interface ExternalSettingsValue {
  mode: ExamMode;
  external_url: string | null;
  allowed_domains: string[];
  finish_url_contains: string | null;
  /** Canvas mode. Missing or null = any other website. */
  external_kind?: ExternalKind | null;
  /** null = detected from the link. */
  canvas_quiz_type?: CanvasQuizType | null;
  /** null = the pattern FairPlay picks for the quiz type. */
  finish_url_regex?: string | null;
  /** Sign-in sites the teacher removed from the automatic list. */
  canvas_removed_hosts?: string[];
  /** Sign-in sites the server adds to Canvas exams (from the exam view); the built-in list when missing. */
  canvas_sign_in_hosts?: string[];
  /** Students may also open the course's pages, modules and files. */
  canvas_open_book?: boolean;
  /** Only in the request built by externalExamInput. */
  canvas?: CanvasInput | null;
}

export type ExternalSettingsErrors = Partial<
  Record<"external_url" | "allowed_domains" | "finish_url_contains" | "finish_url_regex", string>
>;

export const EMPTY_EXTERNAL_SETTINGS: ExternalSettingsValue = {
  mode: "questions",
  external_url: null,
  allowed_domains: [],
  finish_url_contains: null,
};

/** Lowercase host of an http(s) URL, or null if it is not one. */
export function urlHost(url: string | null | undefined): string | null {
  const s = (url ?? "").trim();
  if (!/^https?:\/\//i.test(s)) return null;
  try {
    const u = new URL(s);
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    return u.hostname ? u.hostname.toLowerCase().replace(/^\[|\]$/g, "") : null;
  } catch {
    return null;
  }
}

/** Error text for the exam website address, or null when fine. */
export function validateExternalUrl(url: string | null | undefined): string | null {
  const s = (url ?? "").trim();
  if (!s) return "Paste the address of the exam website.";
  if (s.length > MAX_EXTERNAL_URL) return `The address is too long (max ${MAX_EXTERNAL_URL} characters).`;
  if (!/^https?:\/\//i.test(s)) return "Use the full address, starting with https://";
  if (!urlHost(s)) return "This does not look like a website address.";
  return null;
}

/**
 * Normalize one allowed-website entry (audit 2026-10-12 #10, same rules as the server):
 * an EXACT host ("canvas.dku.edu"), "*.host" for the host and every subdomain, optionally
 * with a path prefix ("docs.google.com/forms/d/e/1FAIpQL/"). Accepts a pasted address
 * (scheme, query and port are dropped); the host is lowercased, the path keeps its case.
 * Does not validate.
 */
export function normalizeDomainInput(raw: string): string {
  let s = raw.trim();
  if (!s) return "";
  s = s.replace(/^[a-z][a-z0-9+.-]*:\/\//i, "");
  s = s.split(/[?#]/)[0] ?? "";
  const slash = s.indexOf("/");
  let host = slash === -1 ? s : s.slice(0, slash);
  let path = slash === -1 ? "" : s.slice(slash);
  const wildcard = host.startsWith("*.");
  if (wildcard) host = host.slice(2);
  host = host.toLowerCase().replace(/:\d*$/, "").replace(/\.+$/, "");
  if (path === "/") path = "";
  return (wildcard ? "*." : "") + host + path;
}

/** Parts of a normalized entry. */
export function parseEntry(entry: string): { wildcard: boolean; host: string; path: string } {
  const wildcard = entry.startsWith("*.");
  const rest = wildcard ? entry.slice(2) : entry;
  const slash = rest.indexOf("/");
  return { wildcard, host: slash === -1 ? rest : rest.slice(0, slash), path: slash === -1 ? "" : rest.slice(slash) };
}

const PATH_RE = /^\/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$/;

/** True when the (already normalized) entry is a valid allowed website on the server. */
export function isValidDomain(entry: string): boolean {
  const { host, path } = parseEntry(entry);
  const hostOk = host.length > 0 && host.length <= 253 && DOMAIN_RE.test(host) && (host.includes(".") || host === "localhost");
  return hostOk && (!path || (path.length <= 500 && PATH_RE.test(path)));
}

/**
 * Hosts that serve many unrelated things: allowing the whole host (or a wildcard over a
 * big company) lets students reach search, AI assistants or any shared document.
 */
const BROAD = [
  "google.com", "docs.google.com", "drive.google.com", "sites.google.com", "googleusercontent.com",
  "microsoft.com", "office.com", "live.com", "sharepoint.com", "bing.com", "github.com",
  "notion.so", "dropbox.com", "wikipedia.org", "youtube.com", "openai.com", "chatgpt.com",
  "baidu.com", "qq.com", "yandex.ru", "apple.com", "icloud.com", "amazon.com",
];

/** Warning texts for entries that open far more than the exam (shown, not blocking). */
export function broadDomainWarnings(entries: string[]): string[] {
  const out: string[] = [];
  for (const e of normalizeDomains(entries)) {
    const { wildcard, host, path } = parseEntry(e);
    const broad = BROAD.some((b) => host === b || host.endsWith("." + b));
    if (wildcard && broad) out.push(`${e} opens every ${host} site, including search and AI tools.`);
    else if (broad && !path)
      out.push(`${e} opens all of ${host}. Add the exact path instead, e.g. ${host}/forms/d/e/…/`);
    else if (wildcard) out.push(`${e} also opens every sub-site of ${host}.`);
  }
  return out;
}

/** Warning for an exam address that will not work as the only allowed page. */
export function externalUrlWarning(url: string | null | undefined): string | null {
  const host = urlHost(url);
  if (host === "forms.gle") return "Short forms.gle links redirect to docs.google.com: paste the full form address.";
  return null;
}

/** Split textarea text (one per line; commas and spaces also work) into raw entries. */
export function splitDomainText(text: string): string[] {
  return text
    .split(/[\n,;\s]+/)
    .map((x) => x.trim())
    .filter(Boolean);
}

/** Normalized, de-duplicated list (blank entries dropped), order kept. */
export function normalizeDomains(entries: string[]): string[] {
  const out: string[] = [];
  for (const e of entries) {
    const d = normalizeDomainInput(e);
    if (d && !out.includes(d)) out.push(d);
  }
  return out;
}

/** Error text for the allowed-websites list, or null when fine. */
export function validateDomains(entries: string[]): string | null {
  const list = normalizeDomains(entries);
  const bad = list.filter((d) => !isValidDomain(d));
  if (bad.length === 1) return `“${bad[0]}” is not a website like canvas.dku.edu or *.dku.edu.`;
  if (bad.length > 1) return `These are not websites like canvas.dku.edu or *.dku.edu: ${bad.join(", ")}.`;
  if (list.length > MAX_ALLOWED_DOMAINS) return `Add at most ${MAX_ALLOWED_DOMAINS} websites.`;
  return null;
}

/**
 * True when the URL parts are allowed by one entry (the same rule the Mac app applies):
 * exact host, or any subdomain for "*.host"; with a path, the URL path must start with it.
 */
export function hostAllowed(host: string, entry: string, urlPath = "/"): boolean {
  const { wildcard, host: d, path } = parseEntry(entry);
  const h = host.toLowerCase();
  const dd = d.toLowerCase();
  if (!(h === dd || (wildcard && h.endsWith("." + dd)))) return false;
  return !path || (urlPath || "/").startsWith(path);
}

/** All errors for the settings; {} when valid (and always {} for "questions" mode). */
export function validateExternalSettings(v: ExternalSettingsValue): ExternalSettingsErrors {
  if (v.mode !== "external") return {};
  const errors: ExternalSettingsErrors = {};
  const urlErr = validateExternalUrl(v.external_url);
  if (urlErr) errors.external_url = urlErr;
  const domErr = validateDomains(v.allowed_domains);
  if (domErr) errors.allowed_domains = domErr;
  if (v.external_kind !== "canvas" && (v.finish_url_contains ?? "").length > MAX_FINISH_CONTAINS)
    errors.finish_url_contains = `Keep this under ${MAX_FINISH_CONTAINS} characters.`;
  if (v.external_kind === "canvas") {
    if (!errors.external_url && !isHttpsUrl(v.external_url)) errors.external_url = "A Canvas link must start with https://";
    else if (!errors.external_url && canvasLinkKind(v.external_url) === null) errors.external_url = CANVAS_NO_SCOPE_HINT;
    const reErr = validateFinishRegex(v.finish_url_regex);
    if (reErr) errors.finish_url_regex = reErr;
  }
  return errors;
}

/**
 * Fields to send in POST/PUT /api/exams (CONTRACT.md slice 3). For "questions" mode the
 * external fields are null/[] (the caller sends its questions); for "external" mode the
 * caller must send `questions: []`.
 */
export function externalExamInput(v: ExternalSettingsValue): ExternalSettingsValue {
  if (v.mode !== "external") return { ...EMPTY_EXTERNAL_SETTINGS };
  const finish = (v.finish_url_contains ?? "").trim();
  const base: ExternalSettingsValue = {
    mode: "external",
    external_url: (v.external_url ?? "").trim() || null,
    allowed_domains: normalizeDomains(v.allowed_domains),
    finish_url_contains: finish || null,
  };
  if (v.external_kind !== "canvas") return v.external_kind === null ? { ...base, external_kind: null } : base;
  const type = effectiveQuizType(v);
  const regex = (v.finish_url_regex ?? "").trim();
  const removed = v.canvas_removed_hosts ?? [];
  const canvas: CanvasInput = {
    quiz_type: type,
    removed_hosts: [...removed],
    open_book: v.canvas_open_book === true,
  };
  return {
    ...base,
    finish_url_contains: null,
    external_kind: "canvas",
    canvas,
    ...(regex && regex !== defaultFinishRegex(type) ? { finish_url_regex: regex } : {}),
  };
}

/** Settings from an Exam (or anything with the optional slice-3 fields). */
export function externalSettingsFrom(
  e: (Partial<Omit<ExternalSettingsValue, "external_kind">> & { external_kind?: ExternalKind | null; canvas?: CanvasInfo | null }) | null | undefined,
): ExternalSettingsValue {
  const base: ExternalSettingsValue = {
    mode: e?.mode === "external" ? "external" : "questions",
    external_url: e?.external_url ?? null,
    allowed_domains: [...(e?.allowed_domains ?? [])],
    finish_url_contains: e?.finish_url_contains ?? null,
  };
  if (e?.mode !== "external" || e.external_kind !== "canvas") return base;
  const signIn = e.canvas?.sign_in_hosts;
  base.allowed_domains = base.allowed_domains.filter((d) => !isAutoCanvasHost(d, e.external_url, signIn));
  const type = e.canvas?.quiz_type ?? detectCanvasQuizType(e.external_url) ?? "classic";
  const regex = e.finish_url_regex ?? null;
  return {
    ...base,
    external_kind: "canvas",
    canvas_quiz_type: type,
    canvas_removed_hosts: [...(e.canvas?.removed_hosts ?? [])],
    ...(signIn ? { canvas_sign_in_hosts: [...signIn] } : {}),
    canvas_open_book: e.canvas?.open_book === true,
    finish_url_regex: regex && regex !== defaultFinishRegex(type) ? regex : null,
  };
}

// ---------- Canvas mode ----------

export const CANVAS_QUIZ_TYPE_LABEL: Record<CanvasQuizType, string> = {
  classic: "Classic Quiz",
  new: "New Quiz",
  assignment: "Assignment",
};

export const MAX_FINISH_REGEX = 300;

/** Sign-in sites the server adds to every Canvas exam (its default FAIRPLAY_CANVAS_SSO_HOSTS). */
export const CANVAS_SIGN_IN_HOSTS = [
  "*.duosecurity.com",
  "login.microsoftonline.com",
  "login.microsoft.com",
  "shib.oit.duke.edu",
  "go.canvas.duke.edu",
  "sso.canvaslms.com",
];

const QUIZ_LTI_HOST = /^\*\.quiz-lti-[a-z0-9-]+-prod\.instructure\.com$/;

/** True for a site the server adds by itself: the Canvas host, New Quizzes hosts, sign-in hosts. */
export function isAutoCanvasHost(entry: string, canvasUrl: string | null | undefined, signIn?: string[]): boolean {
  const d = normalizeDomainInput(entry);
  return d === urlHost(canvasUrl) || QUIZ_LTI_HOST.test(d) || (signIn ?? CANVAS_SIGN_IN_HOSTS).includes(d);
}
export const CANVAS_NO_SCOPE_HINT =
  "The link must open one quiz or assignment, like https://canvas.dku.edu/courses/123/quizzes/456";

const SCOPE_PATH = /^\/courses\/[0-9]{1,20}\/(quizzes|assignments)\/[0-9]{1,20}(?:\/.*)?$/;

/** "quizzes" or "assignments" when the link opens one quiz or assignment, otherwise null (same rule as the server). */
export function canvasLinkKind(url: string | null | undefined): "quizzes" | "assignments" | null {
  let path: string;
  try {
    path = new URL((url ?? "").trim()).pathname.split(";")[0] ?? "";
  } catch {
    return null;
  }
  const m = SCOPE_PATH.exec(path);
  return m ? (m[1] as "quizzes" | "assignments") : null;
}

/** Plain-language list of what students can open in Canvas during the exam. */
export function canvasAccessLine(
  openBook: boolean | null | undefined,
  itemKind?: "quizzes" | "assignments" | null,
  quizType?: CanvasQuizType | null,
): string {
  const noun = itemKind ? (itemKind === "assignments" ? "assignment" : "quiz") : quizType === "assignment" ? "assignment" : "quiz";
  const base = `Students can sign in to Canvas and open the dashboard, the course home and the quiz and assignment lists, then this ${noun}. Once it starts they can only open that ${noun}`;
  return openBook
    ? `${base}, plus this course's pages, modules and files (view only; downloads stay blocked).`
    : `${base}.`;
}

const FINISH_REGEX_DEFAULT: Record<CanvasQuizType, string> = {
  classic: "/quizzes/\\d+/(submissions/\\d+|history)",
  new: "submission-confirmation|submission-screen",
  assignment: "/assignments/\\d+/submissions/\\d+",
};

/** The finish pattern FairPlay uses for a quiz type (the server fills the same one). */
export function defaultFinishRegex(type: CanvasQuizType): string {
  return FINISH_REGEX_DEFAULT[type];
}

function isHttpsUrl(url: string | null | undefined): boolean {
  return /^https:\/\//i.test((url ?? "").trim());
}

/** Quiz type read from a Canvas link, or null when the link is not a quiz or assignment link. */
export function detectCanvasQuizType(url: string | null | undefined): CanvasQuizType | null {
  const s = (url ?? "").trim();
  let path: string;
  try {
    path = new URL(s).pathname;
  } catch {
    return null;
  }
  if (/\/courses\/\d+\/quizzes\/\d+/.test(path)) return "classic";
  if (/\/courses\/\d+\/assignments\/\d+/.test(path)) return "new";
  return null;
}

/** Override first, then the detected type, then Classic. */
export function effectiveQuizType(v: Pick<ExternalSettingsValue, "external_url" | "canvas_quiz_type">): CanvasQuizType {
  return v.canvas_quiz_type ?? detectCanvasQuizType(v.external_url) ?? "classic";
}

export const FINISH_REGEX_RULE =
  "Leave empty for the pattern above, or write a plain pattern of up to 300 characters with no look-ahead, look-behind, back-references or named groups, and no repeated group that contains a choice (|), another repeat or another group.";

export const MAX_REPEAT_COUNT = 100;
export const MAX_UNBOUNDED_QUANTIFIERS = 2;

type RegexAtom = { kind: "lit"; ch: string } | { kind: "cls"; ch: "d" | "w" | "s" } | { kind: "any" };

function escapeAtom(ch: string): RegexAtom {
  if (ch === "d" || ch === "w" || ch === "s") return { kind: "cls", ch };
  if ("DWSbB".includes(ch)) return { kind: "any" };
  return { kind: "lit", ch };
}

const CLASS_TESTS: Record<"d" | "w" | "s", RegExp> = { d: /^[0-9]$/, w: /^[A-Za-z0-9_]$/, s: /^[ \t\n\r\f\v]$/ };

/** Same rule as the server: only single literals and \d, \w, \s can be told apart. */
function atomsOverlap(a: RegexAtom, b: RegexAtom): boolean {
  if (a.kind === "any" || b.kind === "any") return true;
  if (a.kind === "lit" && b.kind === "lit") return a.ch.toLowerCase() === b.ch.toLowerCase();
  if (a.kind === "cls" && b.kind === "cls") return a.ch === b.ch || (a.ch !== "s" && b.ch !== "s");
  const lit = (a.kind === "lit" ? a : b) as { kind: "lit"; ch: string };
  const cls = (a.kind === "cls" ? a : b) as { kind: "cls"; ch: "d" | "w" | "s" };
  if (!/^[\x00-\x7f]$/.test(lit.ch)) return true;
  return CLASS_TESTS[cls.ch].test(lit.ch);
}

interface GroupFrame {
  alt: boolean;
  quant: boolean;
  nested: boolean;
  before: RegexAtom | null;
}

/**
 * Error text for a finish pattern the server would refuse, or null. A hand-written scan
 * of the same plain subset the server accepts; the JS RegExp engine is not used to decide.
 */
export function validateFinishRegex(pattern: string | null | undefined): string | null {
  const p = (pattern ?? "").trim();
  if (!p) return null;
  if (p.length > MAX_FINISH_REGEX) return `Keep this under ${MAX_FINISH_REGEX} characters.`;
  const n = p.length;
  const stack: GroupFrame[] = [];
  let unbounded = 0;
  let prevQuant = false;
  let lastClosed: GroupFrame | null = null;
  let pending: RegexAtom | null = null;
  let cur: RegexAtom = { kind: "any" };
  let curPrev: RegexAtom | null = null;
  const takeAtom = (a: RegexAtom) => {
    curPrev = pending;
    cur = a;
    pending = null;
  };
  let i = 0;
  while (i < n) {
    const c = p[i]!;
    if (c === "\\") {
      const nx = p[i + 1];
      if (nx === undefined) return "The pattern ends with a lone backslash.";
      if (/[1-9kg]/.test(nx)) return "Back-references are not supported.";
      takeAtom(escapeAtom(nx));
      i += 2;
      prevQuant = false;
      lastClosed = null;
      continue;
    }
    if (c === "[") {
      let j = i + 1;
      if (p[j] === "^") j++;
      if (p[j] === "]") j++;
      while (j < n && p[j] !== "]") j += p[j] === "\\" ? 2 : 1;
      if (j >= n) return "A [ in the pattern is not closed.";
      i = j + 1;
      takeAtom({ kind: "any" });
      prevQuant = false;
      lastClosed = null;
      continue;
    }
    if (c === "(") {
      const flags = i === 0 ? /^\(\?[ims]+\)/.exec(p) : null;
      if (flags) {
        i += flags[0].length;
        pending = null;
        prevQuant = false;
        lastClosed = null;
        continue;
      }
      if (p[i + 1] === "?") {
        const rest = p.slice(i + 2, i + 4);
        if (rest.startsWith("P<")) return "Named groups are not supported.";
        if (rest.startsWith("<=") || rest.startsWith("<!")) return "Look-behind patterns are not supported.";
        if (rest.startsWith("<")) return "Named groups are not supported.";
        if (rest.startsWith("=") || rest.startsWith("!")) return "Look-ahead patterns are not supported.";
        if (!rest.startsWith(":")) return "Only plain (...) and (?:...) groups are supported.";
        i += 3;
      } else {
        i += 1;
      }
      const parentFrame = stack[stack.length - 1];
      if (parentFrame) parentFrame.nested = true;
      stack.push({ alt: false, quant: false, nested: false, before: pending });
      pending = null;
      prevQuant = false;
      lastClosed = null;
      continue;
    }
    if (c === ")") {
      const frame = stack.pop();
      if (!frame) return "A ) in the pattern has no matching (.";
      const parent = stack[stack.length - 1];
      if (parent) {
        parent.alt = parent.alt || frame.alt;
        parent.quant = parent.quant || frame.quant;
      }
      lastClosed = frame;
      curPrev = frame.before;
      cur = { kind: "any" };
      pending = pending ? { kind: "any" } : null;
      prevQuant = false;
      i += 1;
      continue;
    }
    if (c === "|") {
      const top = stack[stack.length - 1];
      if (top) top.alt = true;
      pending = null;
      i += 1;
      prevQuant = false;
      lastClosed = null;
      continue;
    }
    let end = 0;
    let repeats = false;
    let open = false;
    if (c === "*" || c === "+") {
      end = i + 1;
      repeats = true;
      open = true;
    } else if (c === "?") {
      end = i + 1;
    } else if (c === "{") {
      if (/^\{,\d*\}/.test(p.slice(i))) return "Write repeat counts as {n}, {n,} or {n,m}, not {,n}.";
      const m = /^\{(\d+)(?:(,)(\d*))?\}/.exec(p.slice(i));
      if (m) {
        const lo = Number(m[1]);
        const hi = m[2] ? (m[3] === "" ? null : Number(m[3])) : lo;
        if (lo > MAX_REPEAT_COUNT || (hi !== null && hi > MAX_REPEAT_COUNT))
          return `Repeat counts can be at most ${MAX_REPEAT_COUNT}.`;
        end = i + m[0].length;
        repeats = hi === null || hi > 1;
        open = hi === null;
      }
    }
    if (end > 0) {
      if (prevQuant) {
        if (c === "?") {
          i = end;
          prevQuant = false;
          continue;
        }
        return c === "+" ? "Possessive repeats are not supported." : "A repeat cannot follow another repeat.";
      }
      if ((repeats || c === "{") && lastClosed && (lastClosed.alt || lastClosed.quant || lastClosed.nested))
        return "A group that is repeated cannot contain a choice (|), another repeat or another group.";
      if (open) {
        if (++unbounded > MAX_UNBOUNDED_QUANTIFIERS)
          return `Use at most ${MAX_UNBOUNDED_QUANTIFIERS} open-ended repeats (*, + or {n,}).`;
        if (curPrev && atomsOverlap(curPrev, cur))
          return "Two repeats in a row that can match the same characters (like .*.* or \\w+\\w+) are not supported.";
        pending = cur;
      }
      const top = stack[stack.length - 1];
      if (top && repeats) top.quant = true;
      i = end;
      prevQuant = true;
      lastClosed = null;
      continue;
    }
    takeAtom(c === "." ? { kind: "any" } : c === "^" || c === "$" ? { kind: "any" } : { kind: "lit", ch: c });
    i += 1;
    prevQuant = false;
    lastClosed = null;
  }
  if (stack.length > 0) return "A ( in the pattern is not closed.";
  return null;
}



// ---------- audit W-09: describe allowed sites exactly as the server and app apply them ----------

/** Hosts where the exam address opens only its own folder (server services.BROAD_HOSTS). */
export const SERVER_BROAD_HOSTS = new Set([
  "docs.google.com", "drive.google.com", "sites.google.com", "www.google.com", "google.com",
  "forms.office.com", "forms.microsoft.com", "onedrive.live.com", "www.notion.so", "notion.so",
  "github.com", "gist.github.com", "www.dropbox.com", "dropbox.com", "sharepoint.com",
  "www.bing.com", "bing.com",
]);

/** The entry the exam address always gets (server services.start_url_entry). */
export function startUrlEntry(url: string | null | undefined): string | null {
  if (!url) return null;
  let u: URL;
  try {
    u = new URL(url.trim());
  } catch {
    return null;
  }
  const host = u.hostname.toLowerCase();
  if (!host) return null;
  if (!SERVER_BROAD_HOSTS.has(host) && !host.endsWith(".sharepoint.com")) return host;
  const path = u.pathname || "/";
  const folder = path.slice(0, path.lastIndexOf("/") + 1) || "/";
  return folder === "/" ? host : host + folder;
}

/** What the students' app will allow: the exam address entry first, then the teacher's list. */
export function effectiveAllowedSites(url: string | null | undefined, entries: string[]): string[] {
  const start = startUrlEntry(url);
  const list = normalizeDomains(entries);
  return start && !list.includes(start) ? [start, ...list] : list;
}

/** One line in plain words for an entry, per parseEntry. */
export function describeEntry(entry: string): string {
  const { wildcard, host, path } = parseEntry(entry);
  if (path) return `${host}${path} — only pages under this folder`;
  if (wildcard) return `${host} and every site under it (*.${host})`;
  return `${host} — this site only, not its sub-sites`;
}
