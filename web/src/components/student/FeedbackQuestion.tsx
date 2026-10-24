import type { AnswerValue, FeedbackQuestion as FQ } from "../../api/types";
import { paperImage, paperValue } from "../../lib/paperAnswer";
import { LETTERS, fmtNum } from "./format";
import { QuestionImages } from "../QuestionImages";
import { LANGUAGE_LABEL } from "../../lib/code/languages";
import "../../styles/code.css";
import { MarkdownContent } from "../MarkdownContent";
import { isCodeAnswer } from "../../lib/answerTypes";
import { gradeAssetContext } from "../../lib/questionAssets";

export function typeLabel(q: Pick<FQ, "type" | "options">): string {
  if (q.type === "mcq") {
    const o = q.options.map((x) => x.trim().toLowerCase());
    return o.length === 2 && o[0] === "true" && o[1] === "false" ? "True or false" : "Multiple choice";
  }
  if (q.type === "numeric") return "Number";
  if (q.type === "short") return "Short answer";
  if (q.type === "code") return "Coding";
  if (q.type === "fill_blank") return "Fill in the blank";
  if (q.type === "file_upload") return "File upload";
  return "Essay";
}

const STATUS: Record<FQ["status"], { cls: string; word: string }> = {
  correct: { cls: "success", word: "correct" },
  partial: { cls: "warn", word: "partial" },
  wrong: { cls: "danger", word: "wrong" },
  unanswered: { cls: "", word: "not answered" },
  pending: { cls: "", word: "being graded" },   // not graded yet
  answered: { cls: "", word: "answered" },        // 2026-10-24: answers-only review
};

export function StatusPill({ q }: { q: Pick<FQ, "status" | "points" | "points_awarded"> }) {
  const s = STATUS[q.status] ?? STATUS.wrong;
  if (q.points_awarded === null || q.points_awarded === undefined) {
    // 2026-10-24: answers-only review: the question's points, never a score
    return (
      <span className="stu-pill" data-status={q.status}>
        {`${fmtNum(q.points)} ${q.points === 1 ? "point" : "points"} · ${s.word}`}
      </span>
    );
  }
  const score = `${fmtNum(q.points_awarded)} / ${fmtNum(q.points)}`;
  return (
    <span className={`stu-pill ${s.cls}`} data-status={q.status}>
      {q.status === "correct" ? (
        <>
          {score} <span aria-hidden="true">✓</span>
          <span className="sr-only">correct</span>
        </>
      ) : (
        `${score} · ${s.word}`
      )}
    </span>
  );
}

function show(q: FQ, raw: AnswerValue | null): string {
  const v = paperValue(raw) as AnswerValue | null;
  if (paperImage(raw) && (v === null || v === undefined)) return "Written on paper";
  if (v === null || v === undefined || v === "") return "No answer";
  if (q.type === "mcq" && typeof v === "number") {
    const text = q.options[v];
    return text === undefined ? `Option ${v + 1}` : `${LETTERS[v] ?? v + 1} · ${text}`;
  }
  return String(v);
}

function correctText(q: FQ): string | null {
  if (q.correct_answer === null || q.correct_answer === undefined) return null;
  if (q.type === "numeric" && typeof q.correct_answer === "number") {
    return q.tolerance ? `${fmtNum(q.correct_answer)} ± ${fmtNum(q.tolerance)}` : fmtNum(q.correct_answer);
  }
  return show(q, q.correct_answer);
}

/** One question on the feedback page: status, your answer vs correct, rubric, teacher comment. */
export function FeedbackQuestionCard({ q }: { q: FQ }) {
  const headId = `fbq-${q.question_id}`;
  const mono = q.type === "numeric";
  const yours = show(q, q.given);
  const correct = q.type === "essay" || q.type === "code" ? null : correctText(q);
  const rubric = q.rubric ?? [];

  return (
    <article className="stu-fbq" aria-labelledby={headId}>
      <div className="stu-fbq-head">
        <h2 id={headId}>
          Question {q.index + 1} · {typeLabel(q)}
        </h2>
        <StatusPill q={q} />
      </div>
      <QuestionImages images={q.images} compact />
      <div className="stu-fbq-prompt">{q.prompt}</div>
      {((q as { image?: string | null }).image || paperImage(q.given)) && (
        <img
          className="stu-fbq-scan"
          src={(q as { image?: string | null }).image || paperImage(q.given) || undefined}
          alt={`Your written answer for question ${q.index + 1}`}
        />
      )}

      {q.type === "code" ? (
        <div className="stu-ans code" data-testid="feedback-code">
          <span>YOUR CODE{q.language ? ` · ${LANGUAGE_LABEL[q.language] ?? q.language}` : ""}</span>
          {typeof q.given === "string" && q.given.trim() ? <pre className="stu-fbq-code">{q.given}</pre> : <span>No answer</span>}
        </div>
      ) : q.type === "essay" ? (
        <>
          {!((q as { image?: string | null }).image || paperImage(q.given)) && (
            <div className={`stu-ans essay`}>
              <span>YOUR ANSWER</span>
              <span>{yours}</span>
            </div>
          )}
          {rubric.length > 0 && (
            <section className="stu-rubric" aria-label="Rubric">
              <h2>Rubric</h2>
              {rubric.map((r, i) => {
                const got = q.rubric_scores?.[i];
                return (
                  <div key={i} className="stu-rubric-row">
                    <span>{r.label}</span>
                    <b>{got === undefined || got === null ? `– / ${fmtNum(r.points)}` : `${fmtNum(got)} / ${fmtNum(r.points)}`}</b>
                  </div>
                );
              })}
            </section>
          )}
        </>
      ) : q.status === "correct" || correct === null ? (
        <div className={`stu-ans ${q.status === "correct" ? "good" : q.status === "unanswered" ? "" : "bad"}`}>
          <span>YOUR ANSWER</span>
          <span className={mono ? "stu-mono" : undefined}>{yours}</span>
        </div>
      ) : (
        <div className="stu-ans-grid">
          <div className={`stu-ans ${q.status === "unanswered" ? "" : "bad"}`}>
            <span>YOUR ANSWER</span>
            <span className={mono ? "stu-mono" : undefined}>{yours}</span>
          </div>
          <div className="stu-ans good">
            <span>CORRECT ANSWER</span>
            <span className={mono ? "stu-mono" : undefined}>{correct}</span>
          </div>
        </div>
      )}

      {q.comment?.trim() && (
        <div className="stu-teacher-note">
          <strong>Teacher:</strong> {q.comment}
        </div>
      )}
    </article>
  );
}

// ---------------------------------------------------------------- graded in another system

/** An answer in words (the feedback page). */
export function describeRichAnswer(q: Pick<FQ, "type" | "options" | "multiple">, given: AnswerValue | null | undefined): string {
  if (given === null || given === undefined || given === "") return "No answer";
  if (isCodeAnswer(given)) return given.source.trim() ? given.source : "No answer";
  if (q.type === "mcq" && Array.isArray(given))
    return given.length
      ? given.map((i) => (typeof i === "number" ? `${LETTERS[i] ?? i + 1} · ${q.options[i] ?? ""}` : String(i))).join("\n")
      : "No answer";
  if (q.type === "mcq" && typeof given === "number") return `${LETTERS[given] ?? given + 1} · ${q.options[given] ?? ""}`;
  if (q.type === "fill_blank" && Array.isArray(given)) return given.map((s, i) => `${i + 1}. ${String(s) || "—"}`).join("\n");
  if (q.type === "file_upload" && Array.isArray(given)) return given.length === 1 ? "1 file handed in" : `${given.length} files handed in`;
  return String(given);
}

function richStatus(q: FQ): FQ["status"] {
  if (q.status) return q.status;
  const got = q.points_awarded ?? 0;
  if (got >= q.points && q.points > 0) return "correct";
  return got > 0 ? "partial" : "wrong";
}

/** One question of an exam graded elsewhere: points and that system's feedback. */
export function ExternalFeedbackQuestionCard({ q, examId }: { q: FQ; examId?: string }) {
  const headId = `fbq-${q.question_id}`;
  const label = q.label || `Question ${q.index + 1}`;
  const code = isCodeAnswer(q.given) || q.type === "code";
  return (
    <article className="stu-fbq" aria-labelledby={headId} data-testid="external-feedback-question">
      <div className="stu-fbq-head">
        <h2 id={headId}>
          {label}
          {q.title ? ` · ${q.title}` : ""}
        </h2>
        <StatusPill q={{ ...q, status: richStatus(q) }} />
      </div>
      <MarkdownContent
        className="stu-fbq-prompt"
        format={q.prompt_format ?? "markdown"}
        text={q.prompt}
        assets={examId ? gradeAssetContext(examId) : null}
      />
      <div className={`stu-ans${code ? " code" : ""}`}>
        <span>YOUR ANSWER{isCodeAnswer(q.given) ? ` · ${q.given.language}` : ""}</span>
        {code ? (
          <pre className="stu-fbq-code">{describeRichAnswer(q, q.given)}</pre>
        ) : (
          <span style={{ whiteSpace: "pre-wrap" }}>{describeRichAnswer(q, q.given)}</span>
        )}
      </div>
      {q.comment?.trim() && (
        <div className="fp-external-feedback" data-testid="external-feedback">
          <strong className="stu-small">Feedback</strong>
          {q.comment_format === "markdown" ? (
            <MarkdownContent format="markdown" text={q.comment} />
          ) : (
            <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{q.comment}</p>
          )}
        </div>
      )}
    </article>
  );
}
