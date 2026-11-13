// 2026-10-24: one open exam as a compact card (Exams page, the home page when many exams
// are open, and a course's exam list). Moved out of ExamsPage.
import { Link, useNavigate } from "react-router-dom";
import type { StudentExamSummary } from "../../api/types";
import { NOT_OPEN_FOR_YOU } from "../../pages/student/useStudentExams";
import { isExited } from "../../lib/examExit";
import { ResumeExam } from "./ResumeExam";
import { dateTime, examName, isToday, monthDay, plural, shortDate, shortTime } from "./format";

export function UpcomingExamCard({
  exam,
  headingLevel = 2,
  showCourse = true,
}: {
  exam: StudentExamSummary;
  headingLevel?: 2 | 3;
  /** 2026-10-24: false on a course's own page */
  showCourse?: boolean;
}) {
  const navigate = useNavigate();
  const inProgress = exam.attempt?.status === "in_progress";
  // 2026-10-23: opened for selected students only, and this student isn't one (yet)
  const notForMe = !inProgress && exam.open_for_me === false;
  const md = monthDay(exam.scheduled_at);
  const preview = exam.preview === true;
  const when = preview
    ? exam.status === "live"
      ? "Open now"
      : exam.status === "closed"
        ? "Closed"
        : "Upcoming"
    : inProgress
    ? "In progress"
    : notForMe
      ? NOT_OPEN_FOR_YOU
      : exam.scheduled_at
      ? `Open now · ${isToday(exam.scheduled_at) ? "today" : shortDate(exam.scheduled_at)} ${shortTime(exam.scheduled_at)}`
      : "Open now";
  const meta = [exam.room && `Room ${exam.room}`, `${exam.duration_min} min`, exam.question_count != null && plural(exam.question_count, "question")]
    .filter(Boolean)
    .join(" · ");
  const name = showCourse ? examName(exam.course, exam.title) : exam.title;
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
      {preview ? (
        <span className="stu-muted" data-testid="preview-only">
          Preview only
        </span>
      ) : notForMe ? (
        <span className="stu-muted" data-testid="not-open-for-you">
          Wait for your teacher
        </span>
      ) : inProgress && isExited(exam.attempt?.attempt_id) ? (
        <ResumeExam examId={exam.id} className="stu-btn primary" />
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

/** 2026-10-24: an exam not open yet whose early upload window is open: "Upcoming — upload your
 *  files". Only "Upload files" (no start, nothing about the questions). */
export function UploadWindowCard({
  exam,
  headingLevel = 3,
  showCourse = true,
}: {
  exam: StudentExamSummary;
  headingLevel?: 2 | 3;
  showCourse?: boolean;
}) {
  const md = monthDay(exam.scheduled_at);
  const name = showCourse ? examName(exam.course, exam.title) : exam.title;
  const H = headingLevel === 2 ? "h2" : "h3";
  const meta = [
    exam.scheduled_at && `Exam ${shortDate(exam.scheduled_at)} · ${shortTime(exam.scheduled_at)}`,
    exam.duration_min > 0 && `${exam.duration_min} min`,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <article className="stu-exam-item upload" data-testid="upload-card">
      <div className="stu-date-tile upload" aria-hidden="true">
        {md ? (
          <>
            <span className="m">{md.m}</span>
            <span className="d">{md.d}</span>
          </>
        ) : (
          <span className="m">Soon</span>
        )}
      </div>
      <div className="stu-exam-item-body">
        <span className="stu-exam-item-when">Upcoming — upload your files</span>
        <H className="stu-clamp-2" title={name}>
          {name}
        </H>
        <span className="stu-exam-item-meta">
          {meta}
          {exam.upload_window && (
            <>
              {meta ? " · " : ""}
              <b data-testid="upload-closes">Uploads close {dateTime(exam.upload_window.closes_at)}</b>
            </>
          )}
        </span>
      </div>
      <Link to={`/student/exams/${encodeURIComponent(exam.id)}/upload`} className="stu-btn primary">
        Upload files
      </Link>
    </article>
  );
}
