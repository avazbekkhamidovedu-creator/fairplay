import { useNavigate } from "react-router-dom";
import { clearToken } from "../../api/client";
import type { StudentExamSummary } from "../../api/types";
import { LogoMark, Wordmark } from "../ui/Icon";
import { clearExamDataOnSignOut } from "../../lib/offline";
import { useStudentExams } from "../../pages/student/useStudentExams";
import { UpcomingExamCard } from "./ExamCard";
import "../../styles/dashboard.css";
import "../../styles/stu-shell.css";

const GROUPS: { title: string; status: StudentExamSummary["status"] }[] = [
  { title: "Open now", status: "live" },
  { title: "Upcoming", status: "draft" },
  { title: "Closed", status: "closed" },
];

/** Operator preview sign-in: every exam in one list, nothing can be started. */
export function AdminPreview() {
  const navigate = useNavigate();
  const { exams, error } = useStudentExams(10000);
  const signOut = () => {
    clearExamDataOnSignOut();
    clearToken("student");
    navigate("/student/login");
  };
  return (
    <div className="sd">
      <header className="sd-top">
        <span style={{ display: "inline-flex", alignItems: "center", gap: 10 }} aria-label="FairPlay">
          <LogoMark size={30} />
          <Wordmark />
        </span>
        <span className="sd-pill">Admin Tester</span>
      </header>
      <h1 className="sd-hi">All exams</h1>
      {error && (
        <div className="sd-empty" role="alert">
          {error}
        </div>
      )}
      {exams && exams.length === 0 && <div className="sd-empty">There are no exams yet.</div>}
      {GROUPS.map((g) => {
        const list = (exams ?? []).filter((e) => e.status === g.status);
        if (list.length === 0) return null;
        return (
          <section key={g.status} aria-label={g.title}>
            <h2 className="sd-sub">{g.title}</h2>
            <div className="stu-exam-list">
              {list.map((e) => (
                <UpcomingExamCard key={e.id} exam={e} headingLevel={3} />
              ))}
            </div>
          </section>
        );
      })}
      <div className="sd-foot">
        <button type="button" className="sd-link" onClick={signOut}>
          Sign out now
        </button>
      </div>
    </div>
  );
}
