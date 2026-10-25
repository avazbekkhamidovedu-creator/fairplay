// Canvas mode: what the teacher sees about a Canvas exam (type, code source and status).
// The access code itself is never available here.
import type { Exam } from "../../api/types";
import { CANVAS_QUIZ_TYPE_LABEL, CODE_STATUS_LABEL, canvasAccessLine } from "./externalValidation";

export function isCanvasExam(exam: Pick<Exam, "mode" | "external_kind"> | null | undefined): boolean {
  return exam?.mode === "external" && exam.external_kind === "canvas";
}

function codeLine(exam: Pick<Exam, "canvas">): string {
  const c = exam.canvas;
  if (!c) return "Access code: not reported";
  const status = c.code_status;
  if (c.access_code_source === "manual")
    return status === "not_set"
      ? "Access code: you type it. No code is saved yet."
      : "Access code: you set it in Canvas and typed it into FairPlay.";
  if (status === "failed") return "Access code: FairPlay could not set it in Canvas.";
  if (status === "set") return "Access code: set in Canvas by FairPlay for this exam.";
  return "Access code: FairPlay sets it in Canvas when you open the exam.";
}

export function CanvasExamInfo({ exam }: { exam: Exam }) {
  if (!isCanvasExam(exam)) return null;
  const c = exam.canvas;
  const status = c?.code_status;
  const tone = status === "set" ? "green" : status === "failed" ? "red" : status === "not_set" ? "orange" : "";
  return (
    <div className="t-kv-block" data-testid="canvas-exam-info" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div>
        <span className="t-pill blue">Canvas exam</span>{" "}
        {c && <span className="t-small t-muted">{CANVAS_QUIZ_TYPE_LABEL[c.quiz_type]}</span>}
      </div>
      <div data-testid="canvas-code-status">
        {status && <span className={`t-pill${tone ? ` ${tone}` : ""}`}>Code {CODE_STATUS_LABEL[status].toLowerCase()}</span>}{" "}
        <span className="t-small t-muted">{codeLine(exam)}</span>
      </div>
      {c?.scope && (
        <p className="t-small t-muted" data-testid="canvas-access-line" style={{ margin: 0 }}>
          {canvasAccessLine(c.open_book, c.scope.item_kind, c.quiz_type)}
        </p>
      )}
      {exam.canvas_notice && (
        <p className="t-small" role="note" data-testid="canvas-notice" style={{ margin: 0 }}>
          {exam.canvas_notice}
        </p>
      )}
      {exam.status === "closed" && c?.remove_code_in_canvas && (
        <p className="t-small" role="alert" data-testid="canvas-remove-code" style={{ margin: 0 }}>
          Remove the access code in Canvas. FairPlay could not clear it when the exam closed, so the quiz still has a
          code on it.
        </p>
      )}
      {status === "failed" && (
        <p className="t-small" role="alert" style={{ margin: 0 }}>
          Link your Canvas account again, or edit the exam and choose “I will type the code I set in Canvas”.
        </p>
      )}
    </div>
  );
}
