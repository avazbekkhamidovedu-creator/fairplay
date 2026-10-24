import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, NetworkError, studentApi } from "../../api/client";
import type { StudentFeedback } from "../../api/types";
import { FeedbackQuestionCard, ExternalFeedbackQuestionCard } from "../../components/student/FeedbackQuestion";
import { fmtNum, fmtPercent, shortDate } from "../../components/student/format";
import { StudentLayout, StuLoading } from "../../components/student/StudentLayout";
import { REVIEW_CLOSED } from "../../lib/review";

type Load =
  | { kind: "loading" }
  | { kind: "ok"; data: StudentFeedback }
  | { kind: "notReleased" }
  | { kind: "reviewClosed" }
  | { kind: "error"; message: string };

export default function FeedbackPage() {
  const { examId = "" } = useParams();
  const [state, setState] = useState<Load>({ kind: "loading" });

  useEffect(() => {
    let alive = true;
    setState({ kind: "loading" });
    studentApi.feedback(examId).then(
      (data) => alive && setState({ kind: "ok", data }),
      (e) => {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 403 && e.detail === REVIEW_CLOSED) setState({ kind: "reviewClosed" });
        else if (e instanceof ApiError && e.status === 403) setState({ kind: "notReleased" });
        else if (e instanceof ApiError && e.status === 404)
          setState({ kind: "error", message: "No submitted exam was found here." });
        else if (e instanceof NetworkError)
          setState({ kind: "error", message: "Cannot reach the server. Check the connection and reload." });
        else setState({ kind: "error", message: e instanceof ApiError ? e.detail : "Something went wrong." });
      },
    );
    return () => {
      alive = false;
    };
  }, [examId]);

  return (
    <StudentLayout>
      <main className="stu-main narrow">
        <Link to="/student/grades" className="stu-back">
          ← Grades
        </Link>
        {state.kind === "loading" && <StuLoading label="Loading feedback…" />}
        {state.kind === "notReleased" && (
          <section className="stu-card" aria-live="polite">
            <h1 style={{ margin: 0, fontSize: 24 }}>Not released yet</h1>
            <p className="stu-muted">
              Your teacher is still grading this exam. Your score and feedback appear here as soon as they release the grades.
            </p>
          </section>
        )}
        {state.kind === "reviewClosed" && (
          <section className="stu-card" aria-live="polite" data-testid="review-closed">
            <h1 style={{ margin: 0, fontSize: 24 }}>Handed in</h1>
            <p className="stu-muted">{REVIEW_CLOSED} You will see more here if they open it.</p>
          </section>
        )}
        {state.kind === "error" && (
          <div className="stu-alert error" role="alert">
            {state.message}
          </div>
        )}
        {state.kind === "ok" && <Feedback data={state.data} />}
      </main>
    </StudentLayout>
  );
}

function Feedback({ data }: { data: StudentFeedback }) {
  const eyebrow = [data.course, "Exam", shortDate(data.submitted_at)].filter(Boolean).join(" · ");
  const questions = [...data.questions].sort((a, b) => a.index - b.index);
  // graded in another system: its points and its feedback
  const external = !!data.source && data.source !== "fairplay";
  // 2026-10-24: only the student's own answers (no score, points or feedback)
  const answersOnly = data.review === "answers";
  return (
    <>
      <section className="stu-fb-head" aria-labelledby="fb-h">
        <div className="stu-fb-head-main">
          <span className="eyebrow">{eyebrow}</span>
          <h1 id="fb-h">{data.title}</h1>
          {data.overall_comment?.trim() && (
            <p className="stu-fb-comment">
              <strong>From your teacher:</strong> {data.overall_comment}
            </p>
          )}
          {data.receipt && (
            <span className="stu-small stu-muted">
              Receipt <span className="stu-mono">{data.receipt}</span>
            </span>
          )}
        </div>
        {!answersOnly && (
          <div className="stu-fb-score">
            <b>
              {fmtNum(data.score)}
              <small> / {fmtNum(data.total_points)}</small>
            </b>
            <span>
              {fmtPercent(data.percent)}
              {data.class_average_percent !== null && data.class_average_percent !== undefined
                ? ` · Class average ${fmtPercent(data.class_average_percent)}`
                : ""}
            </span>
          </div>
        )}
      </section>
      {answersOnly && (
        <p className="stu-small stu-muted" style={{ margin: 0 }} data-testid="answers-only-note">
          Your answers as you handed them in. Scores and feedback are not shown.
        </p>
      )}
      {external && !answersOnly && (
        <p className="stu-small stu-muted" style={{ margin: 0 }} data-testid="external-graded-note">
          Graded in {data.external_name || "another system"}.
        </p>
      )}
      {questions.map((q) =>
        external ? (
          <ExternalFeedbackQuestionCard key={q.question_id} q={q} examId={data.exam_id} />
        ) : (
          <FeedbackQuestionCard key={q.question_id} q={q} />
        ),
      )}
    </>
  );
}
