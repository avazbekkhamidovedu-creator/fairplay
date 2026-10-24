// 2026-10-24: "Student review after hand-in" (server app/review.py). The exam's source (DKU OJ)
// decides what a student sees of a handed-in exam; the server enforces it, these helpers only
// keep the pages from offering links that would be refused.
import type { ExamSource, ReviewLevel } from "../api/types";

/** The server's 403 detail when the teacher has not opened the review. */
export const REVIEW_CLOSED = "Your teacher has not opened the review for this exam.";

/** May the student open the review / feedback page of a handed-in exam? */
export function canOpenReview(source: ExamSource | undefined, review: ReviewLevel | undefined, released: boolean): boolean {
  if (review === "nothing") return false;
  if (released) return true;
  // before a release only exams of another system show the answers (FairPlay exams: feedback only)
  const external = !!source && source !== "fairplay";
  return external && (review === "answers" || review === "answers_and_results");
}

/** The status word of a handed-in exam that shows no score. */
export function waitingLabel(review: ReviewLevel | undefined): string {
  return review === "nothing" || review === "answers" ? "Handed in" : "Being graded";
}
