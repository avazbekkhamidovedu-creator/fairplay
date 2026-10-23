import { useEffect, useState } from "react";
import { urlHost } from "../../components/teacher/externalValidation";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ApiError, teacherApi } from "../../api/client";
import type { Exam, OpenTo } from "../../api/types";
import { Loading } from "../../components/Loading";
import { Pill } from "../../components/teacher/TeacherLayout";
import { errorText, examStatus, formatWhen, plural } from "../../components/teacher/util";
import { LiveRoom } from "./LiveRoom";
import { QuestionsView } from "./QuestionsView";
import { ResultsView } from "./ResultsView";
import { ScanSteps } from "./scan/ScanSteps";
import { ExternalSourceBanner } from "../../components/teacher/ExternalSource";
import { OpenToChoice, closesAtLabel } from "../../components/teacher/OpenTo";

type Tab = "live" | "questions" | "results" | "scan";

export function tabsFor(exam: Exam): { id: Tab; label: string }[] {
  const tabs: { id: Tab; label: string }[] = [];
  // slice 6 D: paper exams have no live room; their steps are Scan & Grade
  if (exam.delivery === "paper") tabs.push({ id: "scan", label: "Scan & Grade" });
  else if (exam.status === "live") tabs.push({ id: "live", label: "Live room" });
  tabs.push({ id: "results", label: "Results" });
  tabs.push({ id: "questions", label: "Questions" });
  return tabs;
}

function defaultTab(exam: Exam): Tab {
  if (exam.delivery === "paper") return exam.grades_released ? "results" : "scan";
  if (exam.status === "live") return "live";
  if (exam.status === "closed") return "results";
  return "questions";
}

export default function TeacherExamPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [exam, setExam] = useState<Exam | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  // 2026-10-23: who can start when the exam opens (everyone, or the ticked students)
  const [openTo, setOpenTo] = useState<OpenTo>("all");

  useEffect(() => {
    let alive = true;
    setExam(null);
    setLoadError(null);
    teacherApi.getExam(id).then(
      (e) => alive && setExam(e),
      (e) => alive && setLoadError(e instanceof ApiError && e.status === 404 ? "This exam does not exist." : errorText(e)),
    );
    return () => {
      alive = false;
    };
  }, [id]);

  if (loadError)
    return (
      <>
        <div className="alert alert-error" role="alert">
          {loadError}
        </div>
        <Link to="/teacher/exams" className="t-back">
          ← All exams
        </Link>
      </>
    );
  if (!exam) return <Loading label="Loading exam…" />;

  const tabs = tabsFor(exam);
  const requested = params.get("tab") as Tab | null;
  const tab: Tab = requested && tabs.some((t) => t.id === requested) ? requested : defaultTab(exam);
  const selectTab = (t: Tab) => setParams({ tab: t }, { replace: true });

  async function run(action: () => Promise<Exam | void>, after?: (e: Exam | void) => void) {
    setBusy(true);
    setActionError(null);
    try {
      const res = await action();
      after?.(res);
    } catch (e) {
      setActionError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const openExam = () =>
    run(
      () => (openTo === "all" ? teacherApi.openExam(exam.id) : teacherApi.openExam(exam.id, openTo)),
      (e) => {
        if (e) setExam(e);
        selectTab("live");
      },
    );
  const deleteExam = () => run(() => teacherApi.deleteExam(exam.id), () => navigate("/teacher/exams", { replace: true }));

  const st = examStatus(exam);
  // from another system: read-only questions and/or grades kept there
  const ext = exam.external ?? null;
  const readOnly = !!ext?.read_only;
  const gradedElsewhere = !!ext?.graded_externally;
  const pending = exam.pending_grading ?? 0;
  const meta = [
    ...(exam.mode === "external"
      ? [`On ${urlHost(exam.external_url) ?? "another website"}`]
      : [plural(exam.question_count, "question"), `${exam.total_points} points`]),
    `${exam.duration_min} min`,
    exam.room && `Room ${exam.room}`,
    exam.scheduled_at && `Planned ${formatWhen(exam.scheduled_at)}`,
    exam.status !== "closed" && closesAtLabel(exam),
    exam.status === "closed" && exam.auto_closed && "Closed automatically at its end time",
  ].filter(Boolean);
  const canOpen = exam.delivery !== "paper" && (exam.status === "draft" || exam.status === "closed");
  const noneSelected = openTo !== "all" && openTo.length === 0;

  return (
    <>
      <Link to="/teacher/exams" className="t-back">
        ← All exams
      </Link>
      <header className="t-page-head">
        <div className="t-head-text">
          <span className="t-eyebrow">
            {exam.course && <span className="t-course-chip">{exam.course}</span>}
            <Pill tone={st.tone} live={st.live}>
              {st.label}
            </Pill>
          </span>
          <h1>{exam.title}</h1>
          <p>{meta.join(" · ")}</p>
        </div>
        <div className="t-head-actions">
          {exam.status !== "draft" && pending > 0 && !gradedElsewhere && (
            <Link
              to={exam.delivery === "paper" ? `/teacher/exams/${exam.id}/scan?step=grade` : `/teacher/exams/${exam.id}/grading`}
              className="btn btn-secondary"
            >
              Grade {plural(pending, "answer")}
            </Link>
          )}
          {exam.status === "draft" && (
            <>
              {!readOnly && (
                <Link to={`/teacher/exams/${exam.id}/edit`} className="btn btn-secondary">
                  Edit
                </Link>
              )}
              {exam.delivery !== "paper" && (
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={openExam}
                  disabled={busy || ext?.package_ready === false || noneSelected}
                >
                  {busy ? "Opening…" : "Open exam"}
                </button>
              )}
            </>
          )}
          {exam.status === "closed" && exam.delivery !== "paper" && (
            <button type="button" className="btn btn-secondary" onClick={openExam} disabled={busy || noneSelected}>
              {busy ? "Opening…" : "Re-open exam"}
            </button>
          )}
          {exam.status !== "live" && !confirmDelete && !ext && (
            <button type="button" className="btn btn-danger-outline" onClick={() => setConfirmDelete(true)}>
              Delete
            </button>
          )}
        </div>
      </header>

      {confirmDelete && (
        <div className="t-confirm" role="alertdialog" aria-labelledby="del-q">
          <p id="del-q">
            <strong>Delete this exam and all its results?</strong> This cannot be undone.
          </p>
          <button type="button" className="btn btn-danger" onClick={deleteExam} disabled={busy}>
            Yes, delete
          </button>
          <button type="button" className="btn btn-secondary" onClick={() => setConfirmDelete(false)}>
            Cancel
          </button>
        </div>
      )}
      {actionError && (
        <div className="alert alert-error" role="alert">
          {actionError}
        </div>
      )}
      {ext && readOnly && <ExternalSourceBanner external={ext} kind="edit" />}
      {ext && ext.package_ready === false && (
        <div className="alert alert-info" role="status" data-testid="external-package-waiting">
          The questions arrive from {ext.name} 24 hours before the start. You can open the exam after that.
        </div>
      )}
      {exam.status === "draft" && exam.delivery !== "paper" && (
        <div className="alert alert-info">
          This exam is a draft. When the class is seated, press <strong>Open exam</strong> to get a start code.
        </div>
      )}

      {canOpen && ext?.package_ready !== false && (
        <section className="t-card" aria-label="Who can start" data-testid="open-to-choice">
          <OpenToChoice examId={exam.id} value={openTo} onChange={setOpenTo} />
          {noneSelected && <span className="t-small t-muted">Tick at least one student to open the exam.</span>}
        </section>
      )}

      <div className="t-tabs" role="tablist" aria-label="Exam sections">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            tabIndex={tab === t.id ? 0 : -1}
            className="t-tab"
            onClick={() => selectTab(t.id)}
            onKeyDown={(ev) => {
              const i = tabs.findIndex((x) => x.id === t.id);
              if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") {
                ev.preventDefault();
                const next = tabs[(i + (ev.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
                if (!next) return;
                selectTab(next.id);
                document.getElementById(`tab-${next.id}`)?.focus();
              }
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} style={{ display: "flex", flexDirection: "column", gap: 24 }}>
        {tab === "live" && exam.status === "live" && (
          <LiveRoom
            exam={exam}
            onExamChange={setExam}
            onClosed={(e) => {
              setExam(e);
              selectTab("results");
            }}
          />
        )}
        {tab === "scan" && (
          <ScanSteps exam={exam} onExamChanged={() => teacherApi.getExam(exam.id).then(setExam, () => undefined)} />
        )}
        {tab === "questions" && <QuestionsView exam={exam} />}
        {tab === "results" && <ResultsView exam={exam} onExamChange={setExam} />}
      </div>
    </>
  );
}
