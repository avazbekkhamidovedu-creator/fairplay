// 2026-10-24: "Student review after hand-in" (server app/review.py). The exam's source (DKU OJ)
// decides what a student sees of a handed-in exam; the server enforces it, these helpers only
// keep the pages from offering links that would be refused. Five levels:
// nothing · score · breakdown · answers · answers_and_results.
import type { ExamSource, ReviewLevel } from "../api/types";

/** The server's 403 detail when the teacher has not opened the review. */
export const REVIEW_CLOSED = "Your teacher has not opened the review for this exam.";

/** May the student open the review / feedback page of a handed-in exam? */
export function canOpenReview(source: ExamSource | undefined, review: ReviewLevel | undefined, released: boolean): boolean {
  if (review === "nothing" || review === "score") return false;   // the card says everything
  if (review === "breakdown") return released;                     // the points per question
  if (review === "answers") return true;
  if (released) return true;
  // before a release only exams of another system show the answers (FairPlay exams: feedback only)
  const external = !!source && source !== "fairplay";
  return external && review === "answers_and_results";
}

/** Does the review page show feedback (answers next to points)? */
export function showsFeedback(review: ReviewLevel | undefined): boolean {
  return review === undefined || review === "answers_and_results";
}

/** The status word of a handed-in exam that shows no score. */
export function waitingLabel(review: ReviewLevel | undefined): string {
  return review === "nothing" || review === "answers" ? "Handed in" : "Being graded";
}

/** Ring colour of a percent (the number is always written too; colour is never the only signal). */
export function ringColor(percent: number): string {
  return percent >= 85 ? "#1d7a3d" : percent >= 65 ? "#3340d6" : "#b25e00";
}
