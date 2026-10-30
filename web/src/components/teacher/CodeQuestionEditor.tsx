// Phase 4: the teacher's form for a coding question: language (exam default or its own),
// starter code, test cases (visible = sample shown to students, hidden = only for grading),
// write-only mode and the time limit.
import type { CodeLanguage, SqlCompare } from "../../api/types";
import { CodeEditor } from "../code/LazyCodeEditor";
import { Icon } from "../ui/Icon";
import { Switch } from "../ui/Switch";
import { CODE_LANGUAGES, LANGUAGE_LABEL, TS_NOTE, canRun } from "../../lib/code/languages";
import { useServerRunnerLanguages } from "../../lib/code/useServerRunner";
import { MAX_SQL_SETUP_CHARS, newTestRow, testPointsSum, type DraftQuestion, type DraftTest, type QuestionErrors } from "../../pages/teacher/examDraft";

interface Props {
  q: DraftQuestion;
  id: (s: string) => string;
  errors: QuestionErrors;
  onChange: (patch: Partial<DraftQuestion>) => void;
  defaultLanguage: CodeLanguage;
}

export function CodeQuestionEditor({ q, id, errors: e, onChange, defaultLanguage }: Props) {
  useServerRunnerLanguages();
  const lang = q.language || defaultLanguage;
  const isSql = lang === "sql";
  const setTest = (key: string, patch: Partial<DraftTest>) =>
    onChange({ tests: q.tests.map((t) => (t.key === key ? { ...t, ...patch } : t)) });
  const sum = testPointsSum(q.tests);
  const target = Number(q.points) || 0;
  const visible = q.tests.filter((t) => t.visible).length;

  return (
    <div className="t-code-q">
      <div className="t-grid-2">
        <div className="t-field">
          <label htmlFor={id("language")}>Language</label>
          <select
            id={id("language")}
            className="t-select"
            value={q.language}
            onChange={(ev) => onChange({ language: ev.target.value as CodeLanguage | "" })}
          >
            <option value="">Exam default ({LANGUAGE_LABEL[defaultLanguage]})</option>
            {CODE_LANGUAGES.map((l) => (
              <option key={l} value={l} disabled={!canRun(l) && l !== q.language}>
                {canRun(l) ? LANGUAGE_LABEL[l] : `${LANGUAGE_LABEL[l]} (runner coming soon)`}
              </option>
            ))}
          </select>
          <span className="hint">Students see the language as a label; they cannot change it.</span>
          {lang === "typescript" && <span className="hint">{TS_NOTE}</span>}
          {!canRun(lang) && (
            <span className="hint">
              No runner is available for this language yet: students cannot run code, answers are graded by you
            </span>
          )}
        </div>
        <div className="t-field">
          <label htmlFor={id("time-limit")}>Time limit per test (milliseconds)</label>
          <input
            id={id("time-limit")}
            className="input mono"
            inputMode="numeric"
            value={q.timeLimit}
            onChange={(ev) => onChange({ timeLimit: ev.target.value })}
            aria-invalid={!!e.timeLimit}
            style={{ maxWidth: 160 }}
          />
          {e.timeLimit ? <span className="field-error">{e.timeLimit}</span> : <span className="hint">100 to 10000. 2000 is 2 seconds.</span>}
        </div>
      </div>

      <div className="t-setting">
        <span className="t-setting-text">
          <label className="t-setting-title" htmlFor={id("write-only")}>
            Write only (no Run)
          </label>
          <span className="t-setting-sub" id={id("write-only-sub")}>
            Like writing code on paper: students get the editor only. You check the code after the exam.
          </span>
        </span>
        <Switch id={id("write-only")} checked={q.writeOnly} onChange={(v) => onChange({ writeOnly: v })} describedBy={id("write-only-sub")} />
      </div>

      {isSql && (
        <div className="t-grid-2">
          <div className="t-field">
            <label htmlFor={id("sql-setup")}>Setup script</label>
            <textarea
              id={id("sql-setup")}
              className="textarea mono"
              rows={8}
              spellCheck={false}
              value={q.sqlSetup}
              onChange={(ev) => onChange({ sqlSetup: ev.target.value })}
              aria-invalid={!!e.sqlSetup}
              aria-describedby={id("sql-setup-hint")}
            />
            <span className="hint" id={id("sql-setup-hint")}>
              Runs on an empty in-memory SQLite database before every test: create tables and insert rows here. Students see this
              script.
            </span>
            <span className={`hint${q.sqlSetup.length > MAX_SQL_SETUP_CHARS ? " field-error" : ""}`} aria-live="polite">
              {q.sqlSetup.length.toLocaleString("en-US")} / {MAX_SQL_SETUP_CHARS.toLocaleString("en-US")}
            </span>
            {e.sqlSetup && <span className="field-error">{e.sqlSetup}</span>}
          </div>
          <div className="t-field">
            <label htmlFor={id("sql-compare")}>Compare rows</label>
            <select
              id={id("sql-compare")}
              className="t-select"
              value={q.sqlCompare}
              onChange={(ev) => onChange({ sqlCompare: ev.target.value as SqlCompare })}
            >
              <option value="unordered">Unordered (default)</option>
              <option value="ordered">Ordered</option>
            </select>
          </div>
        </div>
      )}

      <div className="t-field">
        <span className="t-label" id={id("starter-label")}>
          Starter code (optional)
        </span>
        <div className="t-code-editor">
          <CodeEditor
            key={lang}
            initialValue={q.starter}
            language={lang}
            onChange={(starter) => onChange({ starter })}
            label="Starter code"
          />
        </div>
        <span className="hint">Students start from this code. Leave it empty for a blank editor.</span>
      </div>

      <fieldset className="t-code-tests">
        <legend className="t-label">Test cases — input and the exact output expected</legend>
        <span className="hint">
          Visible tests are shown to students as examples and they can run them. Hidden tests are only used when you grade.
          Trailing spaces and empty lines at the end of the output are ignored.
        </span>
        {q.tests.length === 0 && <span className="hint">No tests yet. Without tests you grade by reading the code.</span>}
        {q.tests.map((t, i) => (
          <div className={`t-code-test${t.visible ? "" : " hidden"}`} key={t.key} data-testid="code-test-row">
            <div className="t-code-test-head">
              <strong>Test {i + 1}</strong>
              <div className="t-seg" role="group" aria-label={`Test ${i + 1} is shown to students`}>
                <button type="button" aria-pressed={t.visible} onClick={() => setTest(t.key, { visible: true })}>
                  Visible
                </button>
                <button type="button" aria-pressed={!t.visible} onClick={() => setTest(t.key, { visible: false })}>
                  Hidden
                </button>
              </div>
              <label className="t-code-pts">
                Points
                <input
                  className="input mono"
                  inputMode="numeric"
                  value={t.points}
                  placeholder="0"
                  aria-label={`Test ${i + 1} points`}
                  onChange={(ev) => setTest(t.key, { points: ev.target.value })}
                />
              </label>
              <button
                type="button"
                className="t-icon-btn"
                aria-label={`Remove test ${i + 1}`}
                onClick={() => onChange({ tests: q.tests.filter((x) => x.key !== t.key) })}
              >
                <Icon name="close" size={16} />
              </button>
            </div>
            <div className={isSql ? undefined : "t-grid-2"}>
              {!isSql && (
                <div className="t-field">
                  <label htmlFor={id(`test-${i}-in`)}>Input (stdin)</label>
                  <textarea
                    id={id(`test-${i}-in`)}
                    className="textarea mono"
                    rows={3}
                    spellCheck={false}
                    value={t.input}
                    onChange={(ev) => setTest(t.key, { input: ev.target.value })}
                  />
                </div>
              )}
              <div className="t-field">
                <label htmlFor={id(`test-${i}-out`)}>
                  {isSql
                    ? "Expected result (first line: column names, then one row per line, values separated by a TAB, NULL for null)"
                    : "Expected output"}
                </label>
                <textarea
                  id={id(`test-${i}-out`)}
                  className="textarea mono"
                  rows={3}
                  spellCheck={false}
                  value={t.expected}
                  onChange={(ev) => setTest(t.key, { expected: ev.target.value })}
                />
              </div>
            </div>
          </div>
        ))}
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12, justifyContent: "space-between" }}>
          <span style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            <button type="button" className="t-link-btn" onClick={() => onChange({ tests: [...q.tests, newTestRow(true)] })} disabled={q.tests.length >= 50}>
              + Visible test
            </button>
            <button type="button" className="t-link-btn" onClick={() => onChange({ tests: [...q.tests, newTestRow(false)] })} disabled={q.tests.length >= 50}>
              + Hidden test
            </button>
          </span>
          {q.tests.length > 0 && (
            <span className={`t-rubric-total ${sum === 0 || sum === target ? "ok" : "bad"}`} aria-live="polite">
              {visible} visible · {q.tests.length - visible} hidden
              {sum > 0 ? ` · test points ${sum} / ${target}` : " · points shared by tests passed"}
            </span>
          )}
        </div>
        {e.tests && <span className="field-error">{e.tests}</span>}
      </fieldset>
    </div>
  );
}
