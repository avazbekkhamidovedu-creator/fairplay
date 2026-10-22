// Phase 4: grading coding answers. The server never runs code; the tests (sample and
// hidden) run here in the teacher's browser with the same runners the students use, and
// the points of the passed tests fill the score (the teacher can still change it).
import { useState } from "react";
import { ApiError, teacherApi } from "../../api/client";
import type { CodeTest, GradingItem, GradingQuestion } from "../../api/types";
import { CodeEditor } from "../code/LazyCodeEditor";
import { DEFAULT_TIME_LIMIT_MS, LANGUAGE_LABEL, canRunInBrowser, resolveLanguage } from "../../lib/code/languages";
import { fmtSeconds } from "../../lib/code/runner";
import { getRunner } from "../../lib/code/runners";
import { STATUS_LABEL, runTests, scoreOutcomes, testSummary, type TestOutcome } from "../../lib/code/runTests";
import { isBlankAnswer } from "../../pages/teacher/gradingLogic";
import { errorText } from "./util";
import "../../styles/code.css";

/** The score the tests give one answer: points and a comment for the student. */
export interface CodeScore {
  points: number;
  comment: string;
  outcomes: TestOutcome[];
}

export function canAutoRun(q: Pick<GradingQuestion, "type" | "language" | "tests">): boolean {
  return q.type === "code" && canRunInBrowser(resolveLanguage(q.language)) && (q.tests?.length ?? 0) > 0;
}

export async function scoreCode(q: GradingQuestion, code: string, onProgress?: (o: TestOutcome[]) => void): Promise<CodeScore> {
  const runner = getRunner(resolveLanguage(q.language));
  if (!runner) throw new Error("This language cannot run in the browser.");
  const r = await runner;
  await r.prepare();
  const tests: CodeTest[] = q.tests ?? [];
  const outcomes = await runTests(r, code, tests, q.time_limit_ms ?? DEFAULT_TIME_LIMIT_MS, onProgress);
  if (outcomes.some((o) => o.status === "unavailable")) throw new Error(outcomes.find((o) => o.stderr)?.stderr || "The tests could not run.");
  const s = scoreOutcomes(outcomes, q.points);
  return { points: s.points, comment: testSummary(outcomes), outcomes };
}

export function CodeAnswerPanel({
  question,
  code,
  onScored,
}: {
  question: GradingQuestion;
  code: string;
  onScored: (score: CodeScore) => void;
}) {
  const lang = resolveLanguage(question.language);
  const tests = question.tests ?? [];
  const hidden = tests.filter((t) => !t.visible).length;
  const [outcomes, setOutcomes] = useState<TestOutcome[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function runAll() {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    setOutcomes([]);
    try {
      const s = await scoreCode(question, code, setOutcomes);
      setOutcomes(s.outcomes);
      onScored(s);
    } catch (e) {
      setProblem(e instanceof Error && !(e instanceof ApiError) ? e.message : errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const blank = isBlankAnswer(code);
  return (
    <div className="t-code-answer" data-testid="code-answer">
      <div className="t-code-answer-bar">
        <strong>{LANGUAGE_LABEL[lang]}</strong>
        <span className="t-muted t-small">
          {tests.length} {tests.length === 1 ? "test" : "tests"}
          {hidden ? ` (${hidden} hidden)` : ""} · time limit {fmtSeconds(question.time_limit_ms ?? DEFAULT_TIME_LIMIT_MS)}
        </span>
        <span style={{ flex: 1 }} />
        {canAutoRun(question) && !blank && (
          <button type="button" className="btn btn-primary" onClick={() => void runAll()} disabled={busy}>
            {busy ? (outcomes && outcomes.length ? `Running ${outcomes.length + 1} of ${tests.length}…` : "Loading…") : "Run all tests"}
          </button>
        )}
      </div>
      {blank ? (
        <p className="t-essay empty">No answer was given.</p>
      ) : (
        <div className="t-code-editor readonly">
          <CodeEditor key={code} initialValue={code} language={lang} readOnly label="Student's code" />
        </div>
      )}
      {!canRunInBrowser(lang) && !blank && (
        <p className="t-small t-muted" style={{ margin: 0 }}>
          {LANGUAGE_LABEL[lang]} cannot run inside FairPlay yet. Read the code and enter the score.
        </p>
      )}
      {problem && (
        <span className="t-inline-error" role="alert">
          {problem}
        </span>
      )}
      {outcomes && outcomes.length > 0 && <TestResultsTable outcomes={outcomes} />}
    </div>
  );
}

export function TestResultsTable({ outcomes }: { outcomes: TestOutcome[] }) {
  return (
    <ol className="t-code-results" aria-label="Test results">
      {outcomes.map((o) => (
        <li key={o.index} className={o.status === "pass" ? "pass" : "fail"}>
          <details open={o.status !== "pass"}>
            <summary>
              <span>Test {o.index + 1}</span>
              <span className="t-muted">{o.visible ? "visible" : "hidden"}</span>
              <span className={`t-code-pill ${o.status}`}>{STATUS_LABEL[o.status]}</span>
              {o.points ? <span className="t-muted">{o.status === "pass" ? o.points : 0} / {o.points} pts</span> : null}
              {o.status !== "timeout" && <span className="t-muted">{o.timeMs} ms</span>}
            </summary>
            <div className="t-code-io">
              <div>
                <span>Input</span>
                <pre>{o.input || " "}</pre>
              </div>
              <div>
                <span>Expected</span>
                <pre>{o.expected || " "}</pre>
              </div>
              <div>
                <span>Output</span>
                <pre>{o.stdout || " "}</pre>
              </div>
            </div>
            {o.stderr && <pre className="t-code-stderr">{o.stderr}</pre>}
          </details>
        </li>
      ))}
    </ol>
  );
}

/** "Run all code tests": score every ungraded coding answer with its tests and save it. */
export function BulkCodeRun({
  examId,
  questions,
  items,
  onItemGraded,
}: {
  examId: string;
  questions: GradingQuestion[];
  items: GradingItem[];
  onItemGraded: (item: GradingItem) => void;
}) {
  const byId = new Map(questions.map((q) => [q.question_id, q] as const));
  const todo = items.filter((it) => {
    const q = byId.get(it.question_id);
    return q && canAutoRun(q) && !it.grade && !isBlankAnswer(it.answer);
  });
  const [asking, setAsking] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!questions.some((q) => canAutoRun(q))) return null;

  async function go() {
    setAsking(false);
    setBusy(true);
    let done = 0;
    let failed = 0;
    const list = [...todo];
    for (const it of list) {
      const q = byId.get(it.question_id)!;
      setProgress(`Running tests: answer ${done + failed + 1} of ${list.length}…`);
      try {
        const s = await scoreCode(q, String(it.answer ?? ""));
        const input = { points: s.points, comment: s.comment, rubric_scores: null };
        const res = await teacherApi.saveGrade(examId, it.attempt_id, it.question_id, input);
        onItemGraded({ ...it, grade: res.grade ?? { ...input, graded_at: new Date().toISOString() } });
        done += 1;
      } catch {
        failed += 1;
      }
    }
    setBusy(false);
    setProgress(
      `Scored ${done} ${done === 1 ? "answer" : "answers"} from the tests${failed ? `, ${failed} could not run (grade those by hand)` : ""}. You can still change each score.`,
    );
  }

  return (
    <div className="t-bulk-code" role="group" aria-label="Coding answers">
      {asking ? (
        <>
          <span className="t-small">
            Runs every ungraded coding answer ({todo.length}) against all its tests here in your browser and saves the points of
            the passed tests.
          </span>
          <button type="button" className="btn btn-primary" onClick={() => void go()}>
            Run and save
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => setAsking(false)}>
            Cancel
          </button>
        </>
      ) : (
        <button type="button" className="t-btn-outline" disabled={busy || todo.length === 0} onClick={() => setAsking(true)}>
          Run all code tests{todo.length ? ` (${todo.length})` : ""}
        </button>
      )}
      {progress && (
        <span className="t-small t-muted" role="status">
          {progress}
        </span>
      )}
    </div>
  );
}
