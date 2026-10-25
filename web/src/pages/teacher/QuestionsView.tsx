import { Link } from "react-router-dom";
import { CanvasExamInfo } from "../../components/teacher/CanvasExamInfo";
import { describeEntry, effectiveAllowedSites } from "../../components/teacher/externalValidation";
import type { Exam } from "../../api/types";
import { KIND_LABEL, kindOf } from "./examDraft";
import { QuestionImages, TopicChips } from "../../components/QuestionImages";
import { LANGUAGE_LABEL, resolveLanguage } from "../../lib/code/languages";
import { fmtSeconds } from "../../lib/code/runner";
import "../../styles/code.css";
import { MarkdownContent } from "../../components/MarkdownContent";
import { formatSize, extensionList, normalizeExtensions } from "../../lib/answerTypes";
import { teacherAssetContext } from "../../lib/questionAssets";
import { isRichFormat } from "../../lib/markdown";

const LETTERS = "ABCDEFGH";

export function QuestionsView({ exam }: { exam: Exam }) {
  if (exam.mode === "external") {
    const sites = exam.allowed_domains ?? [];
    return (
      <section className="t-card" aria-labelledby="qv-h" style={{ gap: 14 }}>
        <h2 id="qv-h">{exam.external_kind === "canvas" ? "Canvas quiz" : "Exam website"}</h2>
        <CanvasExamInfo exam={exam} />
        <p style={{ margin: 0 }}>
          Students answer on{" "}
          <a href={exam.external_url ?? undefined} target="_blank" rel="noreferrer">
            {exam.external_url}
          </a>
          , opened inside the locked FairPlay app after they enter the start code.
        </p>
        <div>
          <strong style={{ fontSize: 14 }}>Websites students may open</strong>
          <ul style={{ margin: "6px 0 0", paddingLeft: 20 }}>
            {effectiveAllowedSites(exam.external_url, sites).map((d) => (
              <li key={d}>{describeEntry(d)}</li>
            ))}
          </ul>
        </div>
        <p className="t-small t-muted" style={{ margin: 0 }}>
          {!exam.finish_url_contains && exam.external_kind === "canvas" && exam.finish_url_regex
            ? "The exam finishes by itself when Canvas shows its submitted page. Students can also press Finish exam."
            : exam.finish_url_contains
            ? `The exam finishes by itself when the page address contains "${exam.finish_url_contains}". Students can also press Finish exam.`
            : "Students press Finish exam in the app when they are done."}
        </p>
      </section>
    );
  }
  return (
    <section className="t-card" aria-labelledby="qv-h" style={{ gap: 14 }}>
      <div className="t-card-head">
        <h2 id="qv-h">
          Questions{" "}
          <span className="t-muted" style={{ fontWeight: 500 }}>
            · {exam.questions.length}
          </span>
        </h2>
        {exam.status === "draft" && !exam.external?.read_only && (
          <Link to={`/teacher/exams/${exam.id}/edit`} className="t-card-link">
            Edit questions
          </Link>
        )}
      </div>
      {exam.shuffle_questions && (
        <p className="t-small t-muted" style={{ margin: 0 }}>
          Each student sees these in a shuffled order.
        </p>
      )}
      {exam.questions.length === 0 ? (
        <div className="t-empty">This exam has no questions.</div>
      ) : (
        <ol style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 12 }}>
          {exam.questions.map((q, i) => {
            const kind = kindOf(q);
            const rich = isRichFormat(q.prompt_format);
            const assets = rich ? teacherAssetContext(exam.id, q.asset_urls ?? null) : null;
            return (
              <li key={q.id} className="t-q">
                <div className="t-q-summary">
                  <span className="t-q-num">{String(i + 1).padStart(2, "0")}</span>
                  <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 8 }}>
                    <QuestionImages images={q.images} compact />
                    {rich && q.title && <strong className="t-small">{q.label ? `${q.label} · ` : ""}{q.title}</strong>}
                    {rich ? (
                      <MarkdownContent className="t-q-prompt" format={q.prompt_format} text={q.prompt} assets={assets} />
                    ) : (
                      <span className="t-q-prompt">{q.prompt}</span>
                    )}
                    <TopicChips topics={q.topics} />
                    {q.type === "mcq" && kind !== "tf" && (
                      <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 6 }}>
                        {q.options.map((o, j) => (
                          <li key={j} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 14 }}>
                            <span className="t-opt-letter">{LETTERS[j]}</span>
                            {rich ? <MarkdownContent format="markdown" text={o} inline assets={assets} /> : <span>{o}</span>}
                            {q.correct_index === j && <span className="t-pill green">Correct</span>}
                          </li>
                        ))}
                      </ul>
                    )}
                    {kind === "tf" && (
                      <span className="t-small">
                        Answer: <strong>{q.correct_index === 0 ? "True" : "False"}</strong>
                      </span>
                    )}
                    {q.type === "numeric" && (
                      <span className="t-small">
                        Answer <strong className="mono">{q.answer_number}</strong>
                        {q.tolerance ? (
                          <>
                            {" "}
                            (± <span className="mono">{q.tolerance}</span>)
                          </>
                        ) : (
                          " (exact)"
                        )}
                      </span>
                    )}
                    {q.type === "short" && (
                      <span className="t-small">
                        Accepted: <strong>{q.accepted_answers.join(", ")}</strong>
                      </span>
                    )}
                    {q.type === "essay" && (
                      <>
                        {q.rubric?.length > 0 && (
                          <ul className="t-small" style={{ margin: 0, paddingLeft: 18 }}>
                            {q.rubric.map((r, j) => (
                              <li key={j}>
                                {r.label} · {r.points} pts
                              </li>
                            ))}
                          </ul>
                        )}
                        {(q.min_words != null || q.max_words != null) && (
                          <span className="t-small t-muted">
                            {q.min_words != null && `At least ${q.min_words} words`}
                            {q.min_words != null && q.max_words != null && " · "}
                            {q.max_words != null && `At most ${q.max_words} words`}
                          </span>
                        )}
                      </>
                    )}
                    {q.type === "code" && !q.languages?.length && <CodeSummary q={q} examDefault={exam.default_language} />}
                    {rich && <RichSummary q={q} />}
                    <span className="t-q-meta">
                      {KIND_LABEL[kind]}
                      {q.type === "mcq" && q.multiple ? " · several answers" : ""} · {q.points} {q.points === 1 ? "pt" : "pts"}
                      {q.partial ? " · partial credit" : ""}
                    </span>
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

/** Phase 4: language, tests and starter code of a coding question (teacher view). */
function CodeSummary({ q, examDefault }: { q: Exam["questions"][number]; examDefault?: string }) {
  const tests = q.tests ?? [];
  const lang = resolveLanguage(q.language, examDefault);
  const visible = tests.filter((t) => t.visible).length;
  return (
    <div className="t-code-summary">
      <span className="t-small">
        <strong>{LANGUAGE_LABEL[lang]}</strong>
        {q.language ? "" : " (exam default)"} · {visible} visible, {tests.length - visible} hidden{" "}
        {tests.length === 1 ? "test" : "tests"} · time limit {fmtSeconds(q.time_limit_ms ?? 2000)}
        {q.write_only ? " · write only (no Run)" : ""}
      </span>
      {q.starter_code ? <pre className="t-code-pre">{q.starter_code}</pre> : null}
      {tests.length > 0 && (
        <table className="t-code-table">
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Input</th>
              <th scope="col">Expected output</th>
              <th scope="col">Shown</th>
              <th scope="col">Points</th>
            </tr>
          </thead>
          <tbody>
            {tests.map((t, i) => (
              <tr key={i}>
                <td>{i + 1}</td>
                <td>
                  <pre>{t.input}</pre>
                </td>
                <td>
                  <pre>{t.expected}</pre>
                </td>
                <td>{t.visible ? "Visible" : "Hidden"}</td>
                <td>{t.points}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/** What a question of the newer types asks for. */
function RichSummary({ q }: { q: Exam["questions"][number] }) {
  if (q.type === "fill_blank") {
    const n = q.blanks ?? 0;
    return (
      <span className="t-small">
        {n === 1 ? "1 blank" : `${n} blanks`}
        {q.blank_points?.length ? ` · ${q.blank_points.join(" / ")} pts` : ""}
      </span>
    );
  }
  if (q.type === "file_upload") {
    const exts = normalizeExtensions(q.allowed_extensions);
    return (
      <span className="t-small">
        {exts.length ? `${extensionList(exts)} files` : "Any file type"}
        {q.max_file_size ? ` · up to ${formatSize(q.max_file_size)}` : ""}
      </span>
    );
  }
  if (q.type === "code" && q.languages?.length) {
    const tests = q.tests ?? [];
    return (
      <div className="t-code-summary">
        <span className="t-small">
          <strong>{q.languages.map((l) => l.name).join(", ")}</strong> · {tests.length}{" "}
          {tests.length === 1 ? "sample" : "samples"} · time limit {fmtSeconds(q.time_limit_ms ?? 2000)}
          {q.languages.some((l) => !l.runner) ? " · some languages run only after the hand-in" : ""}
        </span>
      </div>
    );
  }
  return null;
}
