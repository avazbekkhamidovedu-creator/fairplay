import { useEffect, useMemo, useState } from "react";
import { ApiError, studentApi } from "../../api/client";
import type { StudentGradeSummary } from "../../api/types";
import { fmtPercent } from "../../components/student/format";
import { GradeCard, cardState } from "../../components/student/GradeCard";
import { StudentLayout, StuLoading } from "../../components/student/StudentLayout";

const NO_COURSE = "Other exams";

export interface CourseGroup {
  course: string;
  items: StudentGradeSummary[];
  average: number | null;
  graded: number;
  waiting: number;
}

/** Group grades by course (in first-seen order) with the average over released exams. */
export function groupByCourse(grades: StudentGradeSummary[]): CourseGroup[] {
  const map = new Map<string, StudentGradeSummary[]>();
  for (const g of grades) {
    const key = g.course?.trim() || NO_COURSE;
    const list = map.get(key) ?? [];
    list.push(g);
    map.set(key, list);
  }
  return [...map.entries()].map(([course, items]) => {
    const released = items.filter((g) => g.released && g.percent !== null);
    const average = released.length ? released.reduce((s, g) => s + (g.percent ?? 0), 0) / released.length : null;
    // 2026-10-24: exams whose results the teacher keeps closed are handed in, not "being graded"
    const closed = items.filter((g) => !g.released && (g.review === "nothing" || g.review === "answers")).length;
    return { course, items, average, graded: released.length, waiting: items.length - released.length - closed };
  });
}

export default function GradesPage() {
  const [grades, setGrades] = useState<StudentGradeSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    studentApi.grades().then(
      (g) => alive && setGrades(g),
      (e) => {
        if (!alive) return;
        setError(e instanceof ApiError ? e.detail : "Cannot reach the server. Check the connection and reload.");
        setGrades([]);
      },
    );
    return () => {
      alive = false;
    };
  }, []);

  const groups = useMemo(() => (grades ? groupByCourse(grades) : []), [grades]);
  const current = groups.find((g) => g.course === selected) ?? null;
  const items = current ? current.items : grades ?? [];
  const sum = useMemo(() => gradeSummary(items), [items]);

  return (
    <StudentLayout>
      <main className="stu-main" style={{ gap: 24 }}>
        <div className="stu-grades-head">
          <h1 className="stu-h1">Grades</h1>
          {grades && grades.length > 0 && (
            <span className="stu-grades-sum" data-testid="grades-summary">
              {sum}
              {current && current.average !== null && (
                <>
                  {" · "}
                  <span className="stu-gavg">Average {fmtPercent(current.average)}</span>
                </>
              )}
            </span>
          )}
        </div>
        {error && (
          <div className="stu-alert error" role="alert">
            {error}
          </div>
        )}
        {grades === null ? (
          <StuLoading label="Loading grades…" />
        ) : groups.length === 0 ? (
          !error && (
            <div className="stu-card">
              <h2>No grades yet</h2>
              <p className="stu-muted">Exams you submit show up here. Scores appear when your teacher releases them.</p>
            </div>
          )
        ) : (
          <>
            {groups.length > 1 && (
              <div className="stu-gfilter" role="group" aria-label="Course">
                <button type="button" aria-pressed={current === null} onClick={() => setSelected(null)}>
                  All courses
                </button>
                {groups.map((g) => (
                  <button
                    key={g.course}
                    type="button"
                    aria-pressed={g === current}
                    onClick={() => setSelected(g.course)}
                    title={g.course}
                  >
                    {g.course}
                  </button>
                ))}
              </div>
            )}
            <section aria-label={current ? current.course : "All courses"} className="stu-gcard-grid" data-testid="grade-cards">
              {items.map((g) => (
                <GradeCard
                  key={g.exam_id}
                  headingLevel={2}
                  d={{
                    examId: g.exam_id,
                    course: g.course?.trim() || undefined,
                    title: g.title,
                    submittedAt: g.submitted_at,
                    released: g.released,
                    score: g.score,
                    total: g.total_points,
                    percent: g.percent,
                    review: g.review,
                    source: g.source,
                    breakdown: g.breakdown,
                  }}
                />
              ))}
            </section>
            <p className="stu-hint">Scores show once your teacher releases them.</p>
          </>
        )}
      </main>
    </StudentLayout>
  );
}

/** "4 graded · 1 being graded · 1 private" */
export function gradeSummary(items: StudentGradeSummary[]): string {
  let graded = 0,
    grading = 0,
    handed = 0;
  for (const g of items) {
    const s = cardState(g);
    if (s === "graded") graded++;
    else if (s === "grading") grading++;
    else handed++;
  }
  return [graded && `${graded} graded`, grading && `${grading} being graded`, handed && `${handed} handed in`]
    .filter(Boolean)
    .join(" · ");
}
