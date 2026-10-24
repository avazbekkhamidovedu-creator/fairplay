// 2026-10-24: one open exam as a compact card (Exams page, the home page when many exams
// are open, and a course's exam list). Moved out of ExamsPage.
import { Link, useNavigate } from "react-router-dom";
import type { StudentExamSummary } from "../../api/types";
import { NOT_OPEN_FOR_YOU } from "../../pages/student/useStudentExams";
import { examName, isToday, monthDay, plural, shortDate, shortTime } from "./format";

export function UpcomingExamCard({ exam, headingLevel = 2 }: { exam: StudentExamSummary; headingLevel?: 2 | 3 }) {
  const navigate = useNavigate();
  const inProgress = exam.attempt?.status === "in_progress";
  // 2026-10-23: opened for selected students only, and this student isn't one (yet)
  const notForMe = !inProgress && exam.open_for_me === false;
  const md = monthDay(exam.scheduled_at);
  const when = inProgress
    ? "In progress"
    : notForMe
      ? NOT_OPEN_FOR_YOU
      : exam.scheduled_at
        ? `Open now · ${isToday(exam.scheduled_at) ? "today" : shortDate(exam.scheduled_at)} ${shortTime(exam.scheduled_at)}`
        : "Open now";
  const meta = [exam.room && `Room ${exam.room}`, `${exam.duration_min} min`, plural(exam.question_count, "question")]
    .filter(Boolean)
    .join(" · ");
  const name = examName(exam.course, exam.title);
  const H = headingLevel === 3 ? "h3" : "h2";
  return (
    <article className="stu-exam-item live" data-testid="exam-card">
      <div className="stu-date-tile live" aria-hidden="true">
        {md ? (
          <>
            <span className="m">{md.m}</span>
            <span className="d">{md.d}</span>
          </>
        ) : (
          <span className="m">Live</span>
        )}
      </div>
      <div className="stu-exam-item-body">
        <span className="stu-exam-item-when">{when}</span>
        <H className="stu-clamp-2" title={name}>
          {name}
        </H>
        <span className="stu-exam-item-meta">{meta}</span>
      </div>
      {notForMe ? (
        <span className="stu-muted" data-testid="not-open-for-you">
          Wait for your teacher
        </span>
      ) : inProgress ? (
        <button
          type="button"
          className="stu-btn primary"
          onClick={() => navigate(`/student/exam/${encodeURIComponent(exam.attempt!.attempt_id)}`)}
        >
          Continue
        </button>
      ) : (
        <Link to={`/student/exams/${exam.id}/checkin`} className="stu-btn primary">
          Check in
        </Link>
      )}
    </article>
  );
}
