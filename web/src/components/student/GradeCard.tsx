// 2026-10-24: a grade card, style A "score ring" (approved design): the ring with the percent,
// the course, the title, "x / y points · date" and "Feedback →" when the review allows it.
// Not graded yet: a dashed ring with a clock ("being graded"); handed in with nothing shared:
// a check. Review "breakdown": the points per question as chips under the line. Used by the
// Grades page, a course's Past tab and the home page's Past tab.
import { Link } from "react-router-dom";
import type { BreakdownPoint, ExamSource, ReviewLevel } from "../../api/types";
import { canOpenReview, ringColor, showsFeedback } from "../../lib/review";
import { fmtNum, shortDate } from "./format";
import "../../styles/grades.css";

export interface GradeCardData {
  examId: string;
  course?: string;
  title: string;
  submittedAt: string | null | undefined;
  released: boolean;
  score: number | null | undefined;
  total: number | null | undefined;
  percent: number | null | undefined;
  review?: ReviewLevel;
  source?: ExamSource;
  breakdown?: BreakdownPoint[];
}

/** Chips shown on a card; more than this -> "All N questions →" opens the grade page. */
export const MAX_CHIPS = 12;

type CardState = "graded" | "grading" | "private" | "answers";

export function cardState(d: Pick<GradeCardData, "released" | "percent" | "review">): CardState {
  if (d.released && typeof d.percent === "number") return "graded";
  if (d.review === "nothing") return "private";
  if (d.review === "answers") return "answers";
  return "grading";
}

export function ScoreRing({ percent, size = 72 }: { percent: number; size?: number }) {
  const p = Math.max(0, Math.min(100, Math.round(percent)));
  const c = 2 * Math.PI * 32;
  return (
    <svg
      className="stu-ring"
      width={size}
      height={size}
      viewBox="0 0 76 76"
      role="img"
      aria-label={`${p} percent`}
      data-testid="score-ring"
    >
      <circle cx="38" cy="38" r="32" fill="none" stroke="#e7e9f3" strokeWidth="8" />
      {p > 0 && (
        <circle
          cx="38"
          cy="38"
          r="32"
          fill="none"
          stroke={ringColor(p)}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={`${((c * p) / 100).toFixed(1)} ${c.toFixed(1)}`}
          transform="rotate(-90 38 38)"
        />
      )}
      <text x="38" y="43.5" textAnchor="middle" fontSize={p === 100 ? 15 : 17} fontWeight="700" fill="#161a2c">
        {p}%
      </text>
    </svg>
  );
}

function GradingRing({ size = 72 }: { size?: number }) {
  return (
    <svg className="stu-ring" width={size} height={size} viewBox="0 0 76 76" aria-hidden="true" data-testid="ring-grading">
      <circle cx="38" cy="38" r="32" fill="none" stroke="#e7e9f3" strokeWidth="8" strokeDasharray="6 8" />
      <path d="M38 24v15l9 6" fill="none" stroke="#5a6075" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

function HandedInRing({ size = 72 }: { size?: number }) {
  return (
    <svg className="stu-ring" width={size} height={size} viewBox="0 0 76 76" aria-hidden="true" data-testid="ring-handed-in">
      <circle cx="38" cy="38" r="32" fill="#f1f8f3" stroke="#cfe6d6" strokeWidth="2" />
      <path d="M27 39l7 7 15-16" fill="none" stroke="#1d7a3d" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function chipTone(got: number | null, max: number): string {
  if (got === null || !(max > 0)) return "pending";
  const r = got / max;
  return r >= 0.85 ? "good" : r >= 0.5 ? "mid" : "low";
}

export function BreakdownChips({ items, limit = MAX_CHIPS }: { items: BreakdownPoint[]; limit?: number }) {
  const shown = items.slice(0, limit);
  return (
    <ul className="stu-gchips" aria-label="Points per question" data-testid="breakdown-chips">
      {shown.map((b) => (
        <li key={`${b.index}-${b.label}`} className={`stu-gchip ${chipTone(b.points_awarded, b.points)}`}>
          <span aria-hidden="true">
            {b.label} · {b.points_awarded === null ? "–" : fmtNum(b.points_awarded)}/{fmtNum(b.points)}
          </span>
          <span className="sr-only">
            {b.label}: {b.points_awarded === null ? "not graded yet" : `${fmtNum(b.points_awarded)} of ${fmtNum(b.points)} points`}
          </span>
        </li>
      ))}
      {items.length > shown.length && <li className="stu-gchip more">+{items.length - shown.length} more</li>}
    </ul>
  );
}

export function GradeCard({ d, headingLevel = 3 }: { d: GradeCardData; headingLevel?: 2 | 3 }) {
  const state = cardState(d);
  const date = shortDate(d.submittedAt ?? null);
  const H = headingLevel === 2 ? "h2" : "h3";
  const chips = state === "graded" && d.review === "breakdown" && d.breakdown?.length ? d.breakdown : null;
  const open = canOpenReview(d.source, d.review, state === "graded");
  // what the link says (the whole card is the link)
  let action: string | null = null;
  if (open) {
    if (state === "graded" && showsFeedback(d.review)) action = "Feedback";
    else if (state === "graded" && d.review === "breakdown")
      action = chips && chips.length > MAX_CHIPS ? `All ${chips.length} questions` : null;
    else if (state !== "graded") action = "Your answers";
  }
  const points =
    typeof d.score === "number" && typeof d.total === "number" ? `${fmtNum(d.score)} / ${fmtNum(d.total)} points` : null;
  const line =
    state === "graded"
      ? [points, date && (points ? date : `Handed in ${date}`)].filter(Boolean).join(" · ") || "Graded"
      : `Handed in${date ? ` ${date}` : ""} · ${
          state === "grading"
            ? "being graded"
            : state === "private"
              ? "your teacher keeps this one private"
              : "scores are not shared"
        }`;
  const inner = (
    <>
      {state === "graded" ? (
        <ScoreRing percent={d.percent as number} />
      ) : state === "grading" ? (
        <GradingRing />
      ) : (
        <HandedInRing />
      )}
      <div className="stu-gcard-body">
        {d.course && <span className="stu-gcard-course">{d.course}</span>}
        <H className="stu-gcard-title" title={d.title}>
          {d.title}
        </H>
        <span className="stu-gcard-line" data-testid="grade-line">
          {line}
        </span>
        {chips && <BreakdownChips items={chips} />}
      </div>
      {action && (
        <span className="stu-gcard-action">
          {action} <span aria-hidden="true">→</span>
        </span>
      )}
    </>
  );
  const cls = `stu-gcard ${state}${chips ? " has-chips" : ""}`;
  return action ? (
    <Link to={`/student/grades/${d.examId}`} className={cls} data-testid="grade-card" data-state={state}>
      {inner}
    </Link>
  ) : (
    <article className={cls} data-testid="grade-card" data-state={state}>
      {inner}
    </article>
  );
}
