import type { StudentExamSummary, StudentGradeSummary } from "../../api/types";
import { GradeCard } from "./GradeCard";

/** One past exam as a grade card (style A, score ring): the percent once released and the
 *  review level shows it, else "being graded" or "handed in" (2026-10-24). With the grade
 *  (GET /api/student/grades) the points ("8 / 10 points") and, for review "breakdown", the
 *  points per question show too. */
export function PastExamRow({
  exam,
  grade,
  showCourse = true,
}: {
  exam: StudentExamSummary;
  grade?: StudentGradeSummary;
  /** false on a course's own page (the course is the page) */
  showCourse?: boolean;
}) {
  const a = exam.attempt;
  // the grade list and the exam list apply the same review rule on the server
  const released = !!a?.released || !!grade?.released;
  return (
    <GradeCard
      d={{
        examId: exam.id,
        course: showCourse ? exam.course : undefined,
        title: exam.title,
        submittedAt: a?.submitted_at ?? grade?.submitted_at,
        released,
        score: released ? grade?.score : null,
        total: grade?.total_points,
        percent: released ? (a?.percent ?? grade?.percent ?? null) : null,
        review: a?.review ?? grade?.review,
        source: exam.source,
        breakdown: grade?.breakdown,
      }}
    />
  );
}
