import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { clearToken, getToken, tokenScope } from "../../api/client";
import type { StudentExamSummary } from "../../api/types";
import { NotLockedBanner } from "../../components/LockBanner";
import { firstName, examName, plural, shortTime } from "../../components/student/format";
import { newestExam } from "../../components/student/NewestExamCard";
import { LogoMark, Wordmark } from "../../components/ui/Icon";
import { clearExamDataOnSignOut } from "../../lib/offline";
import { useMe } from "../../lib/useMe";
import { ResumeExam } from "../../components/student/ResumeExam";
import { EXIT_NOTICE, isExited, takeExitNotice } from "../../lib/examExit";
import { useStudentExams } from "./useStudentExams";
import "../../styles/dashboard.css";

const POLL_MS = 5000;

const mmss = (s: number) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

function useSecondsUntil(iso: string | null | undefined): number | null {
  const at = iso ? new Date(iso).getTime() : NaN;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!Number.isFinite(at)) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [at]);
  return Number.isFinite(at) ? Math.max(0, Math.ceil((at - now) / 1000)) : null;
}

function ExamCard({ exam }: { exam: StudentExamSummary }) {
  const navigate = useNavigate();
  const inProgress = exam.attempt?.status === "in_progress";
  const open = inProgress || exam.status === "live";
  const left = useSecondsUntil(exam.scheduled_at);
  const started = exam.scheduled_at && new Date(exam.scheduled_at).getTime() <= Date.now();
  const meta = [
    exam.course,
    exam.scheduled_at && `${started ? "Started" : "Starts"} ${shortTime(exam.scheduled_at)}`,
    `${exam.duration_min} minutes`,
    exam.question_count != null && plural(exam.question_count, "question"),
  ].filter(Boolean);
  return (
    <section className="sd-card" aria-labelledby="sd-exam" data-testid="newest-exam">
      <div>
        {open ? (
          <span className="sd-badge">Open now</span>
        ) : (
          <span className="sd-badge wait" role="timer">
            {left !== null && left > 0 ? `Opens in ${mmss(left)}` : "Opens soon"}
          </span>
        )}
        <h2 id="sd-exam" className="sd-title">
          {examName(exam.course, exam.title)}
        </h2>
        <div className="sd-meta">
          {meta.map((m) => (
            <span key={String(m)}>{m}</span>
          ))}
        </div>
      </div>
      {!open ? (
        <button type="button" className="sd-btn" disabled>
          Waiting for the exam to open
        </button>
      ) : inProgress && isExited(exam.attempt?.attempt_id) ? (
        <ResumeExam examId={exam.id} className="sd-btn" />
      ) : inProgress ? (
        <button type="button" className="sd-btn" onClick={() => navigate(`/student/exam/${encodeURIComponent(exam.attempt!.attempt_id)}`)}>
          Continue exam
        </button>
      ) : (
        <Link to={`/student/exams/${encodeURIComponent(exam.id)}/start`} className="sd-btn">
          Start exam
        </Link>
      )}
    </section>
  );
}

/** The student's page: the newest exam as one card (open now, else the next one). */
export default function Dashboard() {
  const navigate = useNavigate();
  const me = useMe("student");
  const { exams, error } = useStudentExams(POLL_MS);
  const [exitNotice, setExitNotice] = useState(takeExitNotice);
  const scoped = !!tokenScope(getToken("student"));
  const exam = exams ? newestExam(exams) : null;
  const open = !!exam && (exam.attempt?.status === "in_progress" || exam.status === "live");
  const name = firstName(me?.name);
  const signOut = () => {
    clearExamDataOnSignOut();
    clearToken("student");
    navigate("/student/login");
  };
  return (
    <>
      <NotLockedBanner />
      <div className="sd">
        <header className="sd-top">
          <span style={{ display: "inline-flex", alignItems: "center", gap: 10 }} aria-label="FairPlay">
            <LogoMark size={30} />
            <Wordmark />
          </span>
          {me && <span className="sd-pill">{[me.name, me.netid ?? me.username].join(" · ")}</span>}
        </header>
        <h1 className="sd-hi">{name ? `Hi ${name}` : "Hi"}</h1>
        <p className="sd-sub" aria-live="polite">
          {exam ? (open ? "Your exam is open now." : "Your exam opens in a few minutes.") : exams ? "You have no exam to start." : ""}
        </p>
        {exitNotice && (
          <div className="sd-empty" role="status" data-testid="exit-notice">
            {EXIT_NOTICE}{" "}
            <button type="button" className="sd-btn" onClick={() => setExitNotice(false)}>
              OK
            </button>
          </div>
        )}
        {error && (
          <div className="sd-empty" role="alert">
            {error}
          </div>
        )}
        {exam ? <ExamCard exam={exam} /> : exams && <div className="sd-empty">Your exam shows up here when it is ready.</div>}
        <div className="sd-foot">
          {scoped && "After you hand in, you are signed out after 5 minutes and asked for the next exam's code. "}
          <button type="button" className="sd-link" onClick={signOut}>
            Sign out now
          </button>
        </div>
      </div>
    </>
  );
}
