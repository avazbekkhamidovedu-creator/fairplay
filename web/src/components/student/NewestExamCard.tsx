import type { StudentExamSummary } from "../../api/types";

/** The exam in progress first, else the one open now. */
export function newestExam(exams: StudentExamSummary[]): StudentExamSummary | null {
  const running = exams.find((e) => e.attempt?.status === "in_progress");
  if (running) return running;
  return exams.find((e) => e.status === "live" && e.attempt?.status !== "submitted" && e.open_for_me !== false) ?? null;
}
