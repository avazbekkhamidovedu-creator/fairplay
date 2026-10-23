// Answer helpers for the newer question types (several answers, true/false, fill in the
// blank, file upload, coding with several languages), and the small local notes that go with them:
// - the names of files a student uploaded (the answer itself is only the upload ids),
// - the code a student wrote in the other languages of a coding question (the answer is
//   only the current language's code).
// Both are kept on this computer per attempt, and dropped after the hand-in.
import type { AnswerUpload, AnswerValue, CodeAnswer, CodeLanguageOption, StudentQuestion } from "../api/types";

export const ANSWER_FILES_PREFIX = "fairplay:answer-files:";
export const CODE_DRAFTS_PREFIX = "fairplay:code-drafts:";

function readJson<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function writeJson(key: string, v: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(v));
  } catch {
    /* full or unavailable: the answer itself is still in the answer queue */
  }
}

// ---------------------------------------------------------------- answer shapes

export function isCodeAnswer(v: unknown): v is CodeAnswer {
  return !!v && typeof v === "object" && !Array.isArray(v) && typeof (v as CodeAnswer).language === "string" &&
    typeof (v as CodeAnswer).source === "string";
}

/** Selected option indexes of a multiple-answer question (sorted, unique, in range). */
export function selectedIndexes(v: AnswerValue | undefined, count: number): number[] {
  if (!Array.isArray(v)) return [];
  const out = new Set<number>();
  for (const x of v) if (typeof x === "number" && Number.isInteger(x) && x >= 0 && x < count) out.add(x);
  return [...out].sort((a, b) => a - b);
}

/** The answer for a set of chosen options: sorted list, or undefined when none. */
export function multiAnswer(indexes: Iterable<number>): number[] | undefined {
  const list = [...new Set(indexes)].sort((a, b) => a - b);
  return list.length ? list : undefined;
}

/** One string per blank (missing ones ""). */
export function blankValues(v: AnswerValue | undefined, count: number): string[] {
  const src = Array.isArray(v) ? v : [];
  return Array.from({ length: count }, (_, i) => (typeof src[i] === "string" ? (src[i] as string) : ""));
}

/** The answer for the blanks: undefined while every blank is empty. */
export function blankAnswer(values: string[]): string[] | undefined {
  return values.some((s) => s.trim() !== "") ? [...values] : undefined;
}

export function uploadIds(v: AnswerValue | undefined): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x !== "") : [];
}

// ---------------------------------------------------------------- file upload checks

export function fileExtension(name: string): string {
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(i + 1).toLowerCase() : "";
}

export function normalizeExtensions(list: string[] | undefined | null): string[] {
  return (list ?? []).map((e) => e.trim().toLowerCase().replace(/^\*?\./, "")).filter(Boolean);
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  const mb = bytes / (1024 * 1024);
  return `${mb >= 10 ? Math.round(mb) : Math.round(mb * 10) / 10} MB`;
}

/** "PDF, PNG or JPG" */
export function extensionList(exts: string[]): string {
  const up = exts.map((e) => e.toUpperCase());
  if (up.length <= 1) return up.join("");
  return `${up.slice(0, -1).join(", ")} or ${up[up.length - 1]}`;
}

/** Why a file cannot be handed in for this question, or null when it can. */
export function fileProblem(q: Pick<StudentQuestion, "allowed_extensions" | "max_file_size">, file: { name: string; size: number }): string | null {
  const exts = normalizeExtensions(q.allowed_extensions);
  if (exts.length && !exts.includes(fileExtension(file.name)))
    return `Only ${extensionList(exts)} files can be handed in for this question.`;
  if (file.size <= 0) return "This file is empty.";
  const max = q.max_file_size ?? 0;
  if (max > 0 && file.size > max) return `This file is ${formatSize(file.size)}. The limit is ${formatSize(max)}.`;
  return null;
}

// ---------------------------------------------------------------- uploaded file names

type FileNotes = Record<string, { name: string; size: number }>;

export function uploadedFile(aid: string, id: string): AnswerUpload | null {
  const n = readJson<FileNotes>(ANSWER_FILES_PREFIX + aid)?.[id];
  return n && typeof n.name === "string" ? { id, name: n.name, size: Number(n.size) || 0 } : null;
}

export function rememberUpload(aid: string, f: AnswerUpload): void {
  const notes = readJson<FileNotes>(ANSWER_FILES_PREFIX + aid) ?? {};
  notes[f.id] = { name: f.name, size: f.size };
  writeJson(ANSWER_FILES_PREFIX + aid, notes);
}

// ---------------------------------------------------------------- code in other languages

type Drafts = Record<string, Record<string, string>>;

export function codeDrafts(aid: string, qid: string): Record<string, string> {
  return readJson<Drafts>(CODE_DRAFTS_PREFIX + aid)?.[qid] ?? {};
}

export function saveCodeDraft(aid: string, qid: string, lang: string, source: string): void {
  const all = readJson<Drafts>(CODE_DRAFTS_PREFIX + aid) ?? {};
  all[qid] = { ...(all[qid] ?? {}), [lang]: source };
  writeJson(CODE_DRAFTS_PREFIX + aid, all);
}

/** After the hand-in: forget file names and drafts of the attempt. */
export function forgetAnswerNotes(aid: string): void {
  try {
    localStorage.removeItem(ANSWER_FILES_PREFIX + aid);
    localStorage.removeItem(CODE_DRAFTS_PREFIX + aid);
  } catch {
    /* ignore */
  }
}

/** The language a multi-language answer is in (the answer's, else the first). */
export function currentLanguage(langs: CodeLanguageOption[], v: AnswerValue | undefined): CodeLanguageOption | null {
  if (langs.length === 0) return null;
  if (isCodeAnswer(v)) {
    const l = langs.find((x) => x.key === v.language);
    if (l) return l;
  }
  return langs[0]!;
}

/** The code shown for a language: the answer (same language), else the draft, else the template. */
export function sourceFor(
  q: Pick<StudentQuestion, "templates" | "starter_code">,
  lang: string,
  v: AnswerValue | undefined,
  drafts: Record<string, string>,
  first?: string,
): string {
  if (isCodeAnswer(v) && v.language === lang) return v.source;
  // a bare string is the first language's code (server rule)
  if (typeof v === "string" && first !== undefined && lang === first) return v;
  if (typeof drafts[lang] === "string") return drafts[lang]!;
  return q.templates?.[lang] ?? "";
}

/** Is this answer present for the review counts (same rule as the answer queue)? */
export function hasAnswer(v: AnswerValue | undefined): boolean {
  if (v === undefined || v === "") return false;
  if (Array.isArray(v)) return v.length > 0;
  return true;
}

// ---------------------------------------------------------------- shuffled options

function hash32(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** Display order of n options, the same every time for one seed (student + exam + question).
 *  Only the display changes: answers keep the original option indexes. */
export function optionOrder(n: number, seed: string): number[] {
  const order = Array.from({ length: n }, (_, i) => i);
  let x = hash32(seed) || 1;
  const rand = () => {
    // mulberry32
    x = (x + 0x6d2b79f5) >>> 0;
    let t = x;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  for (let i = n - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [order[i], order[j]] = [order[j]!, order[i]!];
  }
  return order;
}
