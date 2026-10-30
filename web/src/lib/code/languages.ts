// Phase 4: the languages a coding question can use. The teacher picks the language (exam
// default, or per question); students only see it as a label.
import type { CodeLanguage } from "../../api/types";

export const CODE_LANGUAGES: CodeLanguage[] = ["python", "javascript", "typescript", "sql", "c", "cpp", "java"];

export const LANGUAGE_LABEL: Record<CodeLanguage, string> = {
  python: "Python 3",
  javascript: "JavaScript",
  typescript: "TypeScript",
  sql: "SQL",
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

export type LanguageRuntime = "browser" | "none";

/** Where a language runs: inside the page ("browser") or nowhere yet ("none": read and grade by hand). */
export const LANGUAGE_RUNTIME: Record<CodeLanguage, LanguageRuntime> = {
  python: "browser",
  javascript: "browser",
  typescript: "browser",
  sql: "browser",
  c: "none",
  cpp: "none",
  java: "none",
};

/** Languages FairPlay can run inside the browser. */
export function canRunInBrowser(lang: CodeLanguage): boolean {
  return LANGUAGE_RUNTIME[lang] === "browser";
}

/** The languages that can run, in picker order. */
export function runnableLanguages(): CodeLanguage[] {
  return CODE_LANGUAGES.filter(canRunInBrowser);
}

/** Help text for the teacher: what the TypeScript runner does and does not check. */
export const TS_NOTE =
  "TypeScript runs with its types removed and not checked: a type error is not reported, only a syntax error stops the program.";

/** Indentation the editor inserts for Tab (spaces only). */
export function indentFor(lang: CodeLanguage): string {
  return lang === "javascript" || lang === "typescript" || lang === "sql" ? "  " : "    ";
}

export const DEFAULT_TIME_LIMIT_MS = 2000;
export const MIN_TIME_LIMIT_MS = 100;
export const MAX_TIME_LIMIT_MS = 10000;
/** Same cap as the server's answer size (characters). */
export const MAX_CODE_CHARS = 50000;
