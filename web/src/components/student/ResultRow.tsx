import { Link } from "react-router-dom";
import type { StudentExamSummary } from "../../api/types";
import { canOpenReview, waitingLabel } from "../../lib/review";
import { examName, fmtPercent, shortDate } from "./format";

/** One past exam: percent + "See feedback" when released, else "Being graded" (or "Handed in"),
 *  with "See your answers" when the teacher lets students review them (2026-10-24). */
export function PastExamRow({ exam }: { exam: StudentExamSummary }) {
  const a = exam.attempt;
  const released = !!a?.released;
  const open = canOpenReview(exam.source, a?.review, released);
  const inner = (
    <>
      <span className="stu-result-title">{examName(exam.course, exam.title)}</span>
      <span className="stu-result-date">{shortDate(a?.submitted_at)}</span>
      {released ? (
        <>
          <span className="stu-result-score">{fmtPercent(a?.percent)}</span>
          <span className="stu-link">See feedback</span>
        </>
      ) : (
        <>
          <span className="stu-pill">{waitingLabel(a?.review)}</span>
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
