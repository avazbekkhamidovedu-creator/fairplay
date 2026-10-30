// Phase 4: comparing a program's output with the expected output of a test.
// Rules (same for students and teachers): line endings are unified, trailing spaces and
// tabs at the end of each line are ignored, and empty lines at the very end are ignored.
// Everything else must match exactly (case, spaces inside a line, blank lines in between).

export function normalizeOutput(s: string): string {
  const lines = s.replace(/\r\n?/g, "\n").split("\n").map((l) => l.replace(/[ \t\f\v]+$/, ""));
  while (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
  return lines.join("\n");
}

export function outputsMatch(actual: string, expected: string): boolean {
  return normalizeOutput(actual) === normalizeOutput(expected);
}

/** 1-based number of the first line that differs, or null when the outputs match. */
export function firstDifferentLine(actual: string, expected: string): number | null {
  const a = normalizeOutput(actual).split("\n");
  const e = normalizeOutput(expected).split("\n");
  const n = Math.max(a.length, e.length);
  for (let i = 0; i < n; i++) if (a[i] !== e[i]) return i + 1;
  return null;
}

/** SQL results: same header line and the same rows, in any order unless `ordered`. */
export function sqlOutputsMatch(actual: string, expected: string, mode: "ordered" | "unordered" = "unordered"): boolean {
  const a = normalizeOutput(actual);
  const e = normalizeOutput(expected);
  if (mode === "ordered" || a === e) return a === e;
  const [ah = "", ...ar] = a.split("\n");
  const [eh = "", ...er] = e.split("\n");
  if (ah !== eh || ar.length !== er.length) return false;
  return ar.sort().join("\n") === er.sort().join("\n");
}
