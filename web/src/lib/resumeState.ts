const KEY = "fp.resume";

export function rememberResume(resume: boolean | undefined, name: string): void {
  try {
    if (resume) sessionStorage.setItem(KEY, JSON.stringify({ name }));
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage unavailable */
  }
}

export function resumeInfo(): { name: string } | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as { name?: unknown };
    return { name: typeof v.name === "string" ? v.name : "" };
  } catch {
    return null;
  }
}

export function welcomeBack(name: string): string {
  const who = name.trim().split(/\s+/)[0] ?? "";
  return `${who ? `Welcome back, ${who}.` : "Welcome back."} Your answers are saved. Continue where you stopped.`;
}
