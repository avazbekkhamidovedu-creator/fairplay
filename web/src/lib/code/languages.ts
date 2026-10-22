// Phase 4: the languages a coding question can use. The teacher picks the language (exam
// default, or per question); students only see it as a label.
import type { CodeLanguage } from "../../api/types";

export const CODE_LANGUAGES: CodeLanguage[] = ["python", "javascript", "c", "cpp", "java"];

export const LANGUAGE_LABEL: Record<CodeLanguage, string> = {
  python: "Python 3",
  javascript: "JavaScript",
  c: "C",
  cpp: "C++",
  java: "Java",
};

export function isCodeLanguage(v: unknown): v is CodeLanguage {
  return typeof v === "string" && (CODE_LANGUAGES as string[]).includes(v);
}

/** The language a question runs in: its own, else the exam default, else Python. */
export function resolveLanguage(own: unknown, examDefault?: unknown): CodeLanguage {
  if (isCodeLanguage(own)) return own;
  return isCodeLanguage(examDefault) ? examDefault : "python";
}

/** Languages FairPlay can run inside the browser (no server runner yet). */
export function canRunInBrowser(lang: CodeLanguage): boolean {
  return lang === "python" || lang === "javascript";
}

/** Indentation the editor inserts for Tab (spaces only). */
export function indentFor(lang: CodeLanguage): string {
  return lang === "javascript" ? "  " : "    ";
}

export const DEFAULT_TIME_LIMIT_MS = 2000;
export const MIN_TIME_LIMIT_MS = 100;
export const MAX_TIME_LIMIT_MS = 10000;
/** Same cap as the server's answer size (characters). */
export const MAX_CODE_CHARS = 50000;
