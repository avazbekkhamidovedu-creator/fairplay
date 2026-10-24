// 2026-10-24: "Upload your files" before the exam (/student/exams/:examId/upload). The teacher
// lets students upload their open-book materials some days before the exam's date; until the
// exam opens this is the only thing the student can do with it (no questions, no start). The
// server decides (GET /api/student/exams/{id}/materials: can_change, the window's close time);
// uploads close at the exam's date or when the teacher opens it, whichever comes first.
import { Link, useParams } from "react-router-dom";
import { NotLockedBanner } from "../../components/LockBanner";
import { MaterialsUpload } from "../../components/student/MaterialsUpload";
import { StudentLayout, StuLoading } from "../../components/student/StudentLayout";
import { examName, isToday, shortDate, shortTime } from "../../components/student/format";
import { ArrowLeftIcon } from "../../components/student/icons";
import { useStudentExams } from "./useStudentExams";

export default function UploadFilesPage() {
  const { examId = "" } = useParams();
  const { exams, error } = useStudentExams();
  const exam = exams?.find((e) => e.id === examId) ?? null;
  const live = exam?.status === "live";
  const when = exam?.scheduled_at
    ? `${isToday(exam.scheduled_at) ? "Today" : shortDate(exam.scheduled_at)} at ${shortTime(exam.scheduled_at)}`
    : null;

  return (
    <>
      <NotLockedBanner />
      <StudentLayout>
        <main className="stu-main stu-upload-page">
          <Link to="/student" className="stu-back">
            <ArrowLeftIcon size={16} />
            Home
          </Link>
          {error && (
            <div className="stu-alert warn" role="alert">
              {error}
            </div>
          )}
          {exams === null ? (
            <StuLoading label="Loading the exam…" />
          ) : !exam || (!exam.upload_window && !live) ? (
            <section className="stu-card" data-testid="upload-missing">
              <h1 className="stu-h1">Uploads are not open</h1>
              <p className="stu-muted" style={{ margin: 0 }}>
                This exam does not take files right now. Your teacher decides when uploads open.
              </p>
              <Link to="/student" className="stu-btn primary" style={{ alignSelf: "flex-start" }}>
                Back to Home
              </Link>
            </section>
          ) : (
            <>
              <header className="stu-upload-intro">
                <span className="stu-eyebrow">{live ? "Open now" : "Upcoming — upload your files"}</span>
                <h1 className="stu-h1">{examName(exam.course, exam.title)}</h1>
                <p className="stu-muted" style={{ margin: 0 }}>
                  {[when && `Exam ${when}`, exam.duration_min > 0 && `${exam.duration_min} minutes`].filter(Boolean).join(" · ")}
                </p>
                {!live && (
                  <p style={{ margin: 0 }}>
                    Add the files you may use in this open-book exam. You will read them inside FairPlay during the exam. The questions
                    and the start code come on exam day.
                  </p>
                )}
              </header>
              <section className="stu-card">
                <MaterialsUpload examId={exam.id} />
              </section>
              {live && (
                <Link to={`/student/exams/${encodeURIComponent(exam.id)}/checkin`} className="stu-btn primary" style={{ alignSelf: "flex-start" }}>
                  Go to check-in
                </Link>
              )}
            </>
          )}
        </main>
      </StudentLayout>
    </>
  );
}
