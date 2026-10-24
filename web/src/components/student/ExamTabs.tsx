// 2026-10-24: "Your tests  [✓ Active | Past]" (Bluebook-style), shared by the student home page
// (every course) and a course's own page. Active = open now + coming up; Past = handed-in
// exams with the grade when the teacher released it and the review setting shows it.
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { studentApi } from "../../api/client";
import type { StudentClass, StudentExamSummary, StudentGradeSummary } from "../../api/types";
import { UpcomingExamCard, UploadWindowCard } from "./ExamCard";
import { courseTitle, examInCourse } from "./Courses";
import { examName, monthDay, plural, shortDate, shortTime } from "./format";
import { CheckIcon } from "./icons";
import { PastExamRow } from "./ResultRow";

export type ExamTab = "active" | "past";

/** The tab, kept in the address (?tab=past) so Back from a review lands on Past again. */
export function useExamTab(): [ExamTab, (t: ExamTab) => void] {
  const [params, setParams] = useSearchParams();
  const tab: ExamTab = params.get("tab") === "past" ? "past" : "active";
  const set = useCallback(
    (t: ExamTab) =>
      setParams(
        (p) => {
          const next = new URLSearchParams(p);
          if (t === "past") next.set("tab", "past");
          else next.delete("tab");
          return next;
        },
        { replace: true },
      ),
    [setParams],
  );
  return [tab, set];
}

/** Released grades by exam id (GET /api/student/grades); empty until loaded or on error. */
export function useStudentGrades(): Map<string, StudentGradeSummary> {
  const [grades, setGrades] = useState<StudentGradeSummary[]>([]);
  useEffect(() => {
    let alive = true;
    studentApi.grades().then(
      (g) => alive && setGrades(Array.isArray(g) ? g : []),
      // not loaded: the rows still show the percent (or "Being graded") from the exam list
      () => undefined,
    );
    return () => {
      alive = false;
    };
  }, []);
  return useMemo(() => new Map(grades.map((g) => [g.exam_id, g])), [grades]);
}

/** Live exams the student can still take (or continue), in-progress first. */
export function isActiveExam(e: StudentExamSummary): boolean {
  return e.attempt?.status === "in_progress" || (e.status === "live" && e.attempt?.status !== "submitted");
}

/** A scheduled (not yet open) exam, known from a course's next exam. */
export interface ScheduledExam {
  key: string;
  title: string;
  starts_at: string | null;
  duration_min?: number | null;
  question_count?: number | null;
  course: StudentClass;
}

/** The courses' next exams that are not open yet, soonest first (no date last). */
export function scheduledExams(classes: StudentClass[]): ScheduledExam[] {
  const out: ScheduledExam[] = [];
  for (const k of classes) {
    const n = k.next_exam;
    if (!n || !n.title || n.status === "live" || n.status === "closed") continue;
    out.push({
      key: `${String(k.id)}:${n.title}:${n.starts_at ?? ""}`,
      title: n.title,
      starts_at: n.starts_at,
      duration_min: n.duration_min,
      question_count: n.question_count,
      course: k,
    });
  }
  return out.sort(
    (a, b) => (a.starts_at ?? "￿").localeCompare(b.starts_at ?? "￿") || a.title.localeCompare(b.title),
  );
}

/** More exams the server counts as upcoming in this course than the list shows. */
export function moreScheduled(k: StudentClass, exams: StudentExamSummary[]): number {
  if (typeof k.upcoming_exams !== "number") return 0;
  const live = exams.filter((e) => e.status === "live" && examInCourse(e, k)).length;
  const n = k.next_exam;
  const next = n && n.status !== "live" && n.status !== "closed" ? 1 : 0;
  // 2026-10-24: drafts in their upload window are listed already (the next exam counted once)
  const uploads = exams.filter((e) => e.status === "draft" && !!e.upload_window && examInCourse(e, k));
  const nextIsUpload =
    next === 1 && !!n && uploads.some((e) => sameExam(n, e));
  return Math.max(0, k.upcoming_exams - live - next - (uploads.length - (nextIsUpload ? 1 : 0)));
}

/** "Your tests  [✓ Active | Past]": a two-tab switch; arrows move between the tabs. */
export function ActivePastTabs({
  tab,
  onTab,
  active,
  past,
  idPrefix,
  label = "Your tests",
}: {
  tab: ExamTab;
  onTab: (t: ExamTab) => void;
  active: number;
  past: number;
  idPrefix: string;
  label?: string;
}) {
  const refs = useRef<Record<ExamTab, HTMLButtonElement | null>>({ active: null, past: null });
  const onKey = (e: KeyboardEvent) => {
    const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
    if (!keys.includes(e.key)) return;
    e.preventDefault();
    const next: ExamTab =
      e.key === "Home" ? "active" : e.key === "End" ? "past" : tab === "active" ? "past" : "active";
    onTab(next);
    refs.current[next]?.focus();
  };
  const tabs: { id: ExamTab; label: string; n: number }[] = [
    { id: "active", label: "Active", n: active },
    { id: "past", label: "Past", n: past },
  ];
  return (
    <div role="tablist" aria-label={label} className="stu-seg" onKeyDown={onKey} data-testid="exam-tabs">
      {tabs.map((t) => {
        const on = tab === t.id;
        return (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[t.id] = el;
            }}
            type="button"
            role="tab"
            id={`${idPrefix}-tab-${t.id}`}
            aria-selected={on}
            aria-controls={`${idPrefix}-panel`}
            tabIndex={on ? 0 : -1}
            className="stu-seg-btn"
            onClick={() => onTab(t.id)}
          >
            <span className="stu-seg-check" aria-hidden="true">
              {on && <CheckIcon size={15} color="currentColor" />}
            </span>
            {t.label}
            <span className="stu-seg-n" aria-label={plural(t.n, "exam")}>
              {t.n}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** A section with "Your tests" + the switch, and the chosen list under it. */
export function TestsSection({
  idPrefix,
  title = "Your tests",
  tab,
  onTab,
  activeCount,
  pastCount,
  children,
}: {
  idPrefix: string;
  title?: string;
  tab: ExamTab;
  onTab: (t: ExamTab) => void;
  activeCount: number;
  pastCount: number;
  children: ReactNode;
}) {
  return (
    <section className="stu-tests" aria-labelledby={`${idPrefix}-h`} data-testid="tests">
      <div className="stu-tests-head">
        <h2 id={`${idPrefix}-h`} className="stu-section-title">
          {title}
        </h2>
        <ActivePastTabs tab={tab} onTab={onTab} active={activeCount} past={pastCount} idPrefix={idPrefix} label={title} />
      </div>
      <div
        id={`${idPrefix}-panel`}
        role="tabpanel"
        aria-labelledby={`${idPrefix}-tab-${tab}`}
        className="stu-tests-panel"
        data-testid={`tests-${tab}`}
      >
        {children}
      </div>
    </section>
  );
}

/** One scheduled exam: date tile, title, when, length; no action until the teacher opens it. */
export function ScheduledExamCard({ item, showCourse }: { item: ScheduledExam; showCourse?: boolean }) {
  const md = monthDay(item.starts_at);
  const when = item.starts_at ? `${shortDate(item.starts_at)} · ${shortTime(item.starts_at)}` : "Date not set yet";
  const meta = [
    typeof item.duration_min === "number" && item.duration_min > 0 && `${item.duration_min} min`,
    typeof item.question_count === "number" && item.question_count > 0 && plural(item.question_count, "question"),
  ]
    .filter(Boolean)
    .join(" · ");
  const name = showCourse ? examName(courseTitle(item.course), item.title) : item.title;
  return (
    <article className="stu-exam-item scheduled" data-testid="scheduled-card">
      <div className="stu-date-tile" aria-hidden="true">
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
        <span className="stu-exam-item-when">Scheduled · {when}</span>
        <h3 className="stu-clamp-2" title={name}>
          {name}
        </h3>
        {meta && <span className="stu-exam-item-meta">{meta}</span>}
      </div>
      <span className="stu-pill" data-testid="scheduled-status">
        Not open yet
      </span>
    </article>
  );
}

/** Active: exams open now (own renderer allowed: the home page's big cards), then coming up. */
/** 2026-10-24: a course's next exam that is listed already as an upload-window exam. */
function sameExam(s: { title: string; starts_at: string | null }, e: StudentExamSummary): boolean {
  if (s.title !== e.title) return false;
  if (!s.starts_at || !e.scheduled_at) return !s.starts_at && !e.scheduled_at;
  return new Date(s.starts_at).getTime() === new Date(e.scheduled_at).getTime();
}

export function ActiveList({
  live,
  uploads = [],
  scheduled: scheduledAll,
  renderLive,
  scheduledShown = 5,
  more = 0,
  showCourse,
  empty,
}: {
  live: StudentExamSummary[];
  /** 2026-10-24: not open yet, early upload window open ("Upload your files") */
  uploads?: StudentExamSummary[];
  scheduled: ScheduledExam[];
  renderLive?: (live: StudentExamSummary[]) => ReactNode;
  scheduledShown?: number;
  /** exams the server counts but does not list (beyond each course's next one) */
  more?: number;
  showCourse?: boolean;
  empty: ReactNode;
}) {
  const [all, setAll] = useState(false);
  const scheduled = scheduledAll.filter((x) => !uploads.some((e) => sameExam(x, e)));
  if (live.length === 0 && uploads.length === 0 && scheduled.length === 0) {
    return (
      <div className="stu-tests-empty" data-testid="tests-empty">
        {empty}
        {more > 0 && <p className="stu-muted stu-small">{plural(more, "more exam")} planned; details appear closer to the date.</p>}
      </div>
    );
  }
  const shown = all ? scheduled : scheduled.slice(0, scheduledShown);
  return (
    <>
      {live.length > 0 && (
        <div className="stu-exam-group" data-testid="active-live">
          <h3 className="stu-group-title">Open now · {live.length}</h3>
          {renderLive ? (
            renderLive(live)
          ) : (
            <div className="stu-exam-list">
              {live.map((e) => (
                <UpcomingExamCard key={e.id} exam={e} headingLevel={3} showCourse={!!showCourse} />
              ))}
            </div>
          )}
        </div>
      )}
      {uploads.length > 0 && (
        <div className="stu-exam-group" data-testid="active-uploads">
          <h3 className="stu-group-title">Upload your files · {uploads.length}</h3>
          <div className="stu-exam-list">
            {uploads.map((e) => (
              <UploadWindowCard key={e.id} exam={e} showCourse={!!showCourse} />
            ))}
          </div>
        </div>
      )}
      {scheduled.length > 0 && (
        <div className="stu-exam-group" data-testid="active-scheduled">
          <h3 className="stu-group-title">Coming up · {scheduled.length}</h3>
          <div className="stu-exam-list">
            {shown.map((s) => (
              <ScheduledExamCard key={s.key} item={s} showCourse={showCourse} />
            ))}
          </div>
          {scheduled.length > scheduledShown && (
            <button type="button" className="stu-btn quiet sm stu-show-all" aria-expanded={all} onClick={() => setAll((v) => !v)}>
              {all ? "Show fewer" : `Show all ${scheduled.length} coming up`}
            </button>
          )}
        </div>
      )}
      {more > 0 && <p className="stu-muted stu-small" style={{ margin: 0 }}>{plural(more, "more exam")} planned; details appear closer to the date.</p>}
    </>
  );
}

/** Past: handed-in exams, newest first, with the grade when shown. */
export function PastList({
  past,
  grades,
  shown = 10,
  empty,
  footer,
  showCourse = true,
  expandable = true,
}: {
  showCourse?: boolean;
  /** false: no "Show all" here (the home page links to the Grades page instead) */
  expandable?: boolean;
  past: StudentExamSummary[];
  grades: Map<string, StudentGradeSummary>;
  shown?: number;
  empty: ReactNode;
  footer?: ReactNode;
}) {
  const [all, setAll] = useState(false);
  if (past.length === 0) {
    return (
      <div className="stu-tests-empty" data-testid="tests-empty">
        {empty}
      </div>
    );
  }
  return (
    <div className="stu-past-list">
      <div className="stu-gcard-grid" data-testid="past-cards">
        {(all ? past : past.slice(0, shown)).map((e) => (
          <PastExamRow key={e.id} exam={e} grade={grades.get(e.id)} showCourse={showCourse} />
        ))}
      </div>
      {expandable && past.length > shown && (
        <button type="button" className="stu-btn quiet sm stu-show-all" aria-expanded={all} onClick={() => setAll((v) => !v)}>
          {all ? "Show fewer" : `Show all ${past.length}`}
        </button>
      )}
      {footer}
    </div>
  );
}

/** 2026-10-24: the Active count: open now + upload windows + coming up (an upload-window exam
 *  that is also a course's next exam counts once). */
export function activeCount(live: StudentExamSummary[], uploads: StudentExamSummary[], scheduled: ScheduledExam[]): number {
  return live.length + uploads.length + scheduled.filter((x) => !uploads.some((e) => sameExam(x, e))).length;
}
