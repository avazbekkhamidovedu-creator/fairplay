// Canvas mode: what the teacher sees about a Canvas exam (type and where students can go).
import type { Exam } from "../../api/types";
import { CANVAS_QUIZ_TYPE_LABEL, canvasAccessLine } from "./externalValidation";

export function isCanvasExam(exam: Pick<Exam, "mode" | "external_kind"> | null | undefined): boolean {
  return exam?.mode === "external" && exam.external_kind === "canvas";
}

export function CanvasExamInfo({ exam }: { exam: Exam }) {
  if (!isCanvasExam(exam)) return null;
  const c = exam.canvas;
  return (
    <div className="t-kv-block" data-testid="canvas-exam-info" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div>
        <span className="t-pill blue">Canvas exam</span>{" "}
        {c && <span className="t-small t-muted">{CANVAS_QUIZ_TYPE_LABEL[c.quiz_type]}</span>}
      </div>
      <p className="t-small t-muted" data-testid="canvas-lock-line" style={{ margin: 0 }}>
        Students sign in to Canvas inside FairPlay and can only open this course and this quiz. Once the quiz is
        started they cannot leave until they submit.
      </p>
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
    </div>
  );
}
