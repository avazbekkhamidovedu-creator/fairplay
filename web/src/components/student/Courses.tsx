// 2026-10-24: the student's courses as cards on the home page (FairPlay classes and DKU OJ
// classes from GET /api/student/classes). A card opens the course's own page
// (/student/courses/:classId, CoursePage).
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, studentClassesApi } from "../../api/client";
import type { StudentClass, StudentExamSummary } from "../../api/types";
import { plural, shortDate, shortTime } from "./format";

/** Courses beyond this many sit behind "Show all" (two rows of three on a computer). */
export const COURSES_SHOWN = 6;
/** On a phone-width screen only this many show before "Show all" (CSS hides the rest). */
export const COURSES_SHOWN_NARROW = 3;

/** The course page of a course card. */
export function coursePath(k: StudentClass | string | number): string {
  const id = typeof k === "object" ? k.id : k;
  return `/student/courses/${encodeURIComponent(String(id))}`;
}

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

export function CourseCard({ k, upcoming }: { k: StudentClass; upcoming: number }) {
  const title = courseTitle(k);
  const sub = courseSubtitle(k);
  const next = nextExamLine(k);
  return (
    <Link
      to={coursePath(k)}
      className="stu-course-card"
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
        {sub || "\u00a0"}
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
    </Link>
  );
}

/** Upcoming exams of a course: the server's count, at least the open exams found in the list. */
export function upcomingCount(k: StudentClass, exams: StudentExamSummary[] | null): number {
  const openHere = (exams ?? []).filter((e) => isLive(e) && examInCourse(e, k)).length;
  return typeof k.upcoming_exams === "number" ? Math.max(k.upcoming_exams, openHere) : openHere;
}

export function CourseGrid({ classes, exams }: { classes: StudentClass[]; exams: StudentExamSummary[] | null }) {
  const [all, setAll] = useState(false);
  const sorted = sortCourses(classes, exams);
  const shown = all ? sorted : sorted.slice(0, COURSES_SHOWN);
  // phone width: fewer cards before "Show all" (the button then shows from 4 courses on)
  const onlyNarrow = sorted.length <= COURSES_SHOWN;
  return (
    <>
      <div className={`stu-course-grid${all ? "" : " limited"}`} role="list" aria-label="Your courses">
        {shown.map((k) => (
          <div role="listitem" key={classKey(k)} className="stu-course-cell">
            <CourseCard k={k} upcoming={upcomingCount(k, exams)} />
          </div>
        ))}
      </div>
      {sorted.length > COURSES_SHOWN_NARROW && (
        <button
          type="button"
          className={`stu-btn quiet stu-show-all${onlyNarrow ? " stu-narrow-only" : ""}`}
          aria-expanded={all}
          onClick={() => setAll((v) => !v)}
        >
          {all ? "Show fewer courses" : `Show all ${sorted.length} courses`}
        </button>
      )}
    </>
  );
}
