// 2026-10-24: the student's courses as cards on the home page (FairPlay classes and DKU OJ
// classes from GET /api/student/classes), and a course's own exam list.
import { forwardRef, useCallback, useEffect, useState } from "react";
import { ApiError, studentClassesApi } from "../../api/client";
import type { StudentClass, StudentExamSummary } from "../../api/types";
import { UpcomingExamCard } from "./ExamCard";
import { plural, shortDate, shortTime } from "./format";
import { PastExamRow } from "./ResultRow";

/** Courses beyond this many sit behind "Show all". */
export const COURSES_SHOWN = 9;
/** Finished exams of one course beyond this many sit behind "Show all". */
export const COURSE_PAST_SHOWN = 5;

export function classKey(k: StudentClass): string {
  return String(k.id);
}

/** The big line on a card: the course ("CS 101 Programming") or the class name. */
export function courseTitle(k: StudentClass): string {
  return (k.course || "").trim() || k.name;
}

/** The small line: the class/section name when it differs, the term, the teacher. */
export function courseSubtitle(k: StudentClass): string {
  const parts: string[] = [];
  if (k.course && k.course.trim() && k.name !== k.course) parts.push(k.name);
  if (k.term) parts.push(k.term);
  if (k.teacher) parts.push(k.teacher);
  return parts.join(" · ");
}

export function sourceLabel(k: StudentClass): string {
  return k.source === "dkuoj" ? "DKU OJ" : "FairPlay";
}

function norm(s: string | null | undefined): string {
  return (s ?? "").trim().replace(/\s+/g, " ").toLowerCase();
}

function sameOrPrefix(a: string, b: string): boolean {
  return !!a && !!b && (a === b || a.startsWith(`${b} `) || b.startsWith(`${a} `));
}

/**
 * Is this exam one of this course's? The server lists each exam's classes in `class_ids`
 * ("dkuoj:31" for DKU OJ, the number as text for FairPlay classes); that decides when present.
 * An older server without it: matched by the course text ("CS 101" ~ "CS 101 Programming").
 */
export function examInCourse(exam: StudentExamSummary, k: StudentClass): boolean {
  if (Array.isArray(exam.class_ids)) return exam.class_ids.map(String).includes(String(k.id));
  const c = norm(exam.course);
  if (!c) return false;
  return sameOrPrefix(c, norm(k.course)) || sameOrPrefix(c, norm(k.name));
}

function isLive(e: StudentExamSummary): boolean {
  return e.attempt?.status === "in_progress" || (e.status === "live" && e.attempt?.status !== "submitted");
}

/** Courses with something open first, then the soonest next exam, then by name. */
export function sortCourses(classes: StudentClass[], exams: StudentExamSummary[] | null): StudentClass[] {
  const live = (k: StudentClass) =>
    k.next_exam?.status === "live" || (exams ?? []).some((e) => isLive(e) && examInCourse(e, k)) ? 0 : 1;
  const next = (k: StudentClass) => k.next_exam?.starts_at ?? "￿";
  return [...classes].sort(
    (a, b) => live(a) - live(b) || next(a).localeCompare(next(b)) || courseTitle(a).localeCompare(courseTitle(b)),
  );
}

export function useStudentClasses() {
  const [classes, setClasses] = useState<StudentClass[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(async () => {
    try {
      const list = await studentClassesApi.list();
      setClasses(Array.isArray(list) ? list : []);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Cannot load your courses right now.");
      setClasses((c) => c ?? []);
    }
  }, []);
  useEffect(() => {
    void reload();
  }, [reload]);
  return { classes, error, reload };
}

function nextExamLine(k: StudentClass): { text: string; live: boolean } | null {
  const n = k.next_exam;
  if (!n || !n.title) return null;
  if (n.status === "live") return { text: n.title, live: true };
  const when = n.starts_at ? `${shortDate(n.starts_at)} ${shortTime(n.starts_at)}`.trim() : "";
  return { text: when ? `${n.title} · ${when}` : n.title, live: false };
}

export function CourseCard({
  k,
  upcoming,
  selected,
  onSelect,
}: {
  k: StudentClass;
  /** Upcoming exams: the server's count, else the open exams found in the list. */
  upcoming: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const title = courseTitle(k);
  const sub = courseSubtitle(k);
  const next = nextExamLine(k);
  return (
    <button
      type="button"
      className={`stu-course-card${selected ? " selected" : ""}`}
      aria-pressed={selected}
      onClick={onSelect}
      data-testid="course-card"
      data-source={k.source === "dkuoj" ? "dkuoj" : "fairplay"}
    >
      <span className="stu-course-top">
        <span className={`stu-source-badge ${k.source === "dkuoj" ? "dkuoj" : "fairplay"}`}>{sourceLabel(k)}</span>
        <span className="stu-course-count" data-testid="course-upcoming">
          {upcoming > 0 ? plural(upcoming, "upcoming exam") : "No upcoming exams"}
        </span>
      </span>
      <span className="stu-course-title stu-clamp-2" title={title}>
        {title}
      </span>
      <span className="stu-course-sub stu-ellipsis" title={sub || undefined}>
        {sub || " "}
      </span>
      <span className="stu-course-next" title={next?.text}>
        {next ? (
          <>
            <span className="stu-course-next-label">{next.live ? "Live now:" : "Next:"}</span>{" "}
            <span className="stu-ellipsis">{next.text}</span>
          </>
        ) : (
          <span className="stu-muted">No exam planned yet</span>
        )}
      </span>
    </button>
  );
}

export function CourseGrid({
  classes,
  exams,
  selectedKey,
  onSelect,
}: {
  classes: StudentClass[];
  exams: StudentExamSummary[] | null;
  selectedKey: string | null;
  onSelect: (k: StudentClass | null) => void;
}) {
  const [all, setAll] = useState(false);
  const sorted = sortCourses(classes, exams);
  // the selected course always stays visible
  let shown = all ? sorted : sorted.slice(0, COURSES_SHOWN);
  const sel = sorted.find((k) => classKey(k) === selectedKey);
  if (sel && !shown.includes(sel)) shown = [...shown, sel];
  return (
    <>
      <div className="stu-course-grid" role="list" aria-label="Your courses">
        {shown.map((k) => {
          const openHere = (exams ?? []).filter((e) => isLive(e) && examInCourse(e, k)).length;
          const upcoming = typeof k.upcoming_exams === "number" ? Math.max(k.upcoming_exams, openHere) : openHere;
          return (
            <div role="listitem" key={classKey(k)} className="stu-course-cell">
              <CourseCard
                k={k}
                upcoming={upcoming}
                selected={classKey(k) === selectedKey}
                onSelect={() => onSelect(classKey(k) === selectedKey ? null : k)}
              />
            </div>
          );
        })}
      </div>
      {sorted.length > COURSES_SHOWN && (
        <button type="button" className="stu-btn quiet stu-show-all" aria-expanded={all} onClick={() => setAll((v) => !v)}>
          {all ? "Show fewer courses" : `Show all ${sorted.length} courses`}
        </button>
      )}
    </>
  );
}

/** One course's exams: open now, coming up (from the class info), finished. */
export const CourseExams = forwardRef<HTMLElement, { k: StudentClass; exams: StudentExamSummary[]; onClose: () => void }>(
  function CourseExams({ k, exams, onClose }, ref) {
    const [allPast, setAllPast] = useState(false);
    const mine = exams.filter((e) => examInCourse(e, k));
    const live = mine.filter(isLive);
    const past = mine
      .filter((e) => e.attempt?.status === "submitted")
      .sort((a, b) => (b.attempt?.submitted_at ?? "").localeCompare(a.attempt?.submitted_at ?? ""));
    const coming = k.next_exam && k.next_exam.status !== "live" && k.next_exam.status !== "closed" ? k.next_exam : null;
    const headId = `course-exams-${classKey(k).replace(/[^A-Za-z0-9_-]/g, "-")}`;
    const empty = live.length === 0 && past.length === 0 && !coming;
    return (
      <section ref={ref} className="stu-card stu-course-exams" aria-labelledby={headId} id="course-exams" data-testid="course-exams">
        <div className="stu-row" style={{ alignItems: "flex-start" }}>
          <div style={{ flex: "1 1 200px", minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
            <h2 id={headId} tabIndex={-1} className="stu-clamp-2" title={courseTitle(k)}>
              {courseTitle(k)}
            </h2>
            {courseSubtitle(k) && <span className="stu-muted stu-small">{courseSubtitle(k)}</span>}
          </div>
          <button type="button" className="stu-btn quiet sm" onClick={onClose}>
            Show all courses
          </button>
        </div>
        {empty && (
          <p className="stu-muted" style={{ margin: 0 }}>
            No exams for this course yet. When your teacher opens one, it shows up here.
          </p>
        )}
        {live.length > 0 && (
          <div className="stu-exam-group">
            <h3 className="stu-group-title">Live now · {live.length}</h3>
            <div className="stu-exam-list">
              {live.map((e) => (
                <UpcomingExamCard key={e.id} exam={e} headingLevel={3} />
              ))}
            </div>
          </div>
        )}
        {coming && (
          <div className="stu-exam-group">
            <h3 className="stu-group-title">Coming up</h3>
            <p style={{ margin: 0 }} className="stu-ellipsis" title={coming.title}>
              <strong>{coming.title}</strong>
              {coming.starts_at && (
                <span className="stu-muted">
                  {" "}
                  · {shortDate(coming.starts_at)} {shortTime(coming.starts_at)}
                </span>
              )}
            </p>
          </div>
        )}
        {past.length > 0 && (
          <div className="stu-exam-group">
            <h3 className="stu-group-title">Finished · {past.length}</h3>
            <div className="stu-rows">
              {(allPast ? past : past.slice(0, COURSE_PAST_SHOWN)).map((e) => (
                <PastExamRow key={e.id} exam={e} />
              ))}
            </div>
            {past.length > COURSE_PAST_SHOWN && (
              <button type="button" className="stu-btn quiet sm stu-show-all" aria-expanded={allPast} onClick={() => setAllPast((v) => !v)}>
                {allPast ? "Show fewer" : `Show all ${past.length}`}
              </button>
            )}
          </div>
        )}
      </section>
    );
  },
);
