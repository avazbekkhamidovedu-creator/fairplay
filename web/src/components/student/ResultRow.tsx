import { Link } from "react-router-dom";
import type { StudentExamSummary, StudentGradeSummary } from "../../api/types";
import { canOpenReview, waitingLabel } from "../../lib/review";
import { examName, fmtNum, fmtPercent, shortDate } from "./format";

/** One past exam: percent + "See feedback" when released, else "Being graded" (or "Handed in"),
 *  with "See your answers" when the teacher lets students review them (2026-10-24).
 *  2026-10-24: with the grade (GET /api/student/grades) the score shows as "8 / 10" too. */
export function PastExamRow({ exam, grade }: { exam: StudentExamSummary; grade?: StudentGradeSummary }) {
  const a = exam.attempt;
  // the grade list and the exam list apply the same review rule on the server
  const released = !!a?.released || !!grade?.released;
  const review = a?.review ?? grade?.review;
  const open = canOpenReview(exam.source, review, released);
  const percent = a?.percent ?? grade?.percent ?? null;
  const score =
    released && grade && grade.score !== null && grade.score !== undefined
      ? `${fmtNum(grade.score)} / ${fmtNum(grade.total_points)}`
      : null;
  const handedIn = a?.submitted_at ?? grade?.submitted_at;
  const inner = (
    <>
      <span className="stu-result-title">
        <span className="stu-clamp-2" title={examName(exam.course, exam.title)}>
          {examName(exam.course, exam.title)}
        </span>
      </span>
      <span className="stu-result-date">{handedIn ? `Handed in ${shortDate(handedIn)}` : "Handed in"}</span>
      {released ? (
        <>
          <span className="stu-result-score" data-testid="result-score">
            {score && <span className="stu-result-points">{score}</span>}
            {fmtPercent(percent)}
          </span>
          <span className="stu-link">See feedback</span>
        </>
      ) : (
        <>
          <span className="stu-pill" data-testid="result-status">
            {waitingLabel(review)}
          </span>
          {open && <span className="stu-link">See your answers</span>}
        </>
      )}
    </>
  );
  return open ? (
    <Link to={`/student/grades/${exam.id}`} className="stu-result-row">
      {inner}
    </Link>
  ) : (
    <div className="stu-result-row">{inner}</div>
  );
}
