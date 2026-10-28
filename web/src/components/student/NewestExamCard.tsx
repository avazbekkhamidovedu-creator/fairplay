import type { StudentExamSummary } from "../../api/types";
import { isWaiting } from "../../pages/student/useStudentExams";

/** The exam open now first, else the one that is waiting to open. */
export function newestExam(exams: StudentExamSummary[]): StudentExamSummary | null {
  const running = exams.find((e) => e.attempt?.status === "in_progress");
  if (running) return running;
  const open = exams.find((e) => e.status === "live" && e.attempt?.status !== "submitted" && e.open_for_me !== false);
  return open ?? exams.find(isWaiting) ?? null;
}
