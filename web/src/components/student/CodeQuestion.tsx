// Phase 4: a coding question in the exam. Left: the problem (prompt, pictures, sample
// tests). Right: the editor on top and the test panel below (Sample tests / Custom input).
// The language is fixed by the teacher. Hidden tests are never here (the server strips
// them); nothing on this screen shows a score.
import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import type { AnswerValue, CodeLanguage, CodeLanguageOption, StudentQuestion } from "../../api/types";
import { CodeEditor } from "../code/LazyCodeEditor";
import { LANGUAGE_LABEL, DEFAULT_TIME_LIMIT_MS, canRunInBrowser, isCodeLanguage, resolveLanguage } from "../../lib/code/languages";
import { codeDrafts, currentLanguage, isCodeAnswer, saveCodeDraft, sourceFor } from "../../lib/answerTypes";
import { fmtSeconds, type RunResult } from "../../lib/code/runner";
import { getRunner } from "../../lib/code/runners";
import { STATUS_LABEL, runTests, type TestOutcome } from "../../lib/code/runTests";

interface Props {
  question: StudentQuestion;
  value: AnswerValue | undefined;
  onChange: (v: AnswerValue | undefined) => void;
  /** pictures + the prompt heading (the page owns its focus handling) */
  prompt: ReactNode;
  /** 2026-10-23: the attempt, so remembered results never show in another attempt */
  attemptId?: string;
}

type Tab = "samples" | "custom";

interface Memory {
  tab: Tab;
  samples: TestOutcome[] | null;
  customInput: string;
  custom: RunResult | null;
  panel: number;
}

// Results stay while the student moves between questions (this page only, never saved).
// 2026-10-23: kept per attempt + question, and forgotten when the attempt finishes.
const memory = new Map<string, Memory>();
const memoryKey = (attemptId: string, questionId: string) => `${attemptId}\u0000${questionId}`;

/** Forget the run results of one attempt (the exam is over). */
export function forgetCodeResults(attemptId: string): void {
  const prefix = `${attemptId}\u0000`;
  for (const k of [...memory.keys()]) if (k.startsWith(prefix)) memory.delete(k);
}

/** For tests: how many questions have remembered results. */
export function rememberedCodeResults(): number {
  return memory.size;
}
const DEFAULT_PANEL = 260;

export function codeOf(question: StudentQuestion, value: AnswerValue | undefined): string {
  if (isCodeAnswer(value)) return value.source;
  return typeof value === "string" ? value : question.starter_code ?? "";
}

/** The message for a language FairPlay cannot run in the exam. */
export const RUNS_AFTER_HAND_IN = "This language runs after you hand in.";

/** Editor colours for a language without a runner (C-like when unknown). */
export function editorLanguageFor(l: CodeLanguageOption): CodeLanguage {
  if (l.runner && isCodeLanguage(l.runner)) return l.runner;
  const k = l.key.toUpperCase();
  if (k.startsWith("PY")) return "python";
  if (k === "JS" || k.startsWith("NODE") || k.startsWith("V8")) return "javascript";
  if (k.startsWith("JAVA")) return "java";
  if (/^C\d*$/.test(k)) return "c";
  return "cpp";
}

export function CodeQuestion({ question, value, onChange, prompt, attemptId = "" }: Props) {
  // A question may allow several languages (the student picks one; the code
  // of the others is kept on this computer).
  const langOptions = question.languages ?? [];
  const multi = langOptions.length > 0;
  const [langKey, setLangKey] = useState(() => currentLanguage(langOptions, value)?.key ?? "");
  const langOption = multi ? (langOptions.find((l) => l.key === langKey) ?? langOptions[0]!) : null;
  const runLang: CodeLanguage | null = langOption
    ? langOption.runner && isCodeLanguage(langOption.runner)
      ? langOption.runner
      : null
    : resolveLanguage(question.language);
  const lang: CodeLanguage = langOption ? editorLanguageFor(langOption) : (runLang ?? "python");
  const label = langOption ? langOption.name : LANGUAGE_LABEL[lang];
  const samples = question.tests ?? [];
  const writeOnly = !!question.write_only;
  const runnable = !writeOnly && runLang !== null && canRunInBrowser(runLang);
  const limit = question.time_limit_ms ?? DEFAULT_TIME_LIMIT_MS;
  const key = memoryKey(attemptId, question.id);
  const mem = memory.get(key);

  const codeRef = useRef(
    langOption
      ? sourceFor(question, langOption.key, value, codeDrafts(attemptId, question.id), langOptions[0]?.key)
      : codeOf(question, value),
  );
  const [tab, setTab] = useState<Tab>(mem?.tab ?? (samples.length ? "samples" : "custom"));
  const [results, setResults] = useState<TestOutcome[] | null>(mem?.samples ?? null);
  const [customInput, setCustomInput] = useState(mem?.customInput ?? "");
  const [custom, setCustom] = useState<RunResult | null>(mem?.custom ?? null);
  const [panel, setPanel] = useState(mem?.panel ?? DEFAULT_PANEL);
  const [busy, setBusy] = useState<null | "loading" | "running">(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    memory.set(key, { tab, samples: results, customInput, custom, panel });
  }, [key, tab, results, customInput, custom, panel]);

  const onCode = useCallback(
    (code: string) => {
      codeRef.current = code;
      if (langOption) {
        saveCodeDraft(attemptId, question.id, langOption.key, code);
        onChange(code.trim() === "" ? undefined : { language: langOption.key, source: code });
      } else onChange(code.trim() === "" ? undefined : code);
    },
    [onChange, langOption, attemptId, question.id],
  );

  const switchLanguage = (key: string) => {
    if (!langOption || key === langOption.key) return;
    saveCodeDraft(attemptId, question.id, langOption.key, codeRef.current);
    const src = sourceFor(question, key, undefined, codeDrafts(attemptId, question.id));
    codeRef.current = src;
    setLangKey(key);
    setResults(null);
    setCustom(null);
    setProblem(null);
    onChange(src.trim() === "" ? undefined : { language: key, source: src });
  };

  async function runner() {
    if (!runLang) return null;
    const r = getRunner(runLang);
    if (!r) return null;
    const x = await r;
    setBusy("loading");
    try {
      await x.prepare();
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
      return null;
    }
    return x;
  }

  async function runSamples() {
    if (busy || samples.length === 0) return;
    setTab("samples");
    setProblem(null);
    const x = await runner();
    if (!x) {
      setBusy(null);
      return;
    }
    setBusy("running");
    setResults([]);
    try {
      const out = await runTests(x, codeRef.current, samples, limit, (done) => setResults(done));
      setResults(out);
    } finally {
      setBusy(null);
    }
  }

  async function runCustom() {
    if (busy) return;
    setTab("custom");
    setProblem(null);
    const x = await runner();
    if (!x) {
      setBusy(null);
      return;
    }
    setBusy("running");
    setCustom(null);
    try {
      setCustom(await x.run(codeRef.current, customInput, limit));
    } finally {
      setBusy(null);
    }
  }

  // ----- resizable test panel (drag the bar, or arrow keys on it) -----
  const rightRef = useRef<HTMLDivElement>(null);
  const clampPanel = (h: number) => {
    const total = rightRef.current?.clientHeight ?? 700;
    return Math.round(Math.min(Math.max(h, 120), Math.max(160, total - 160)));
  };
  const onDragStart = (e: ReactPointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const startY = e.clientY;
    const start = panel;
    const move = (ev: PointerEvent) => setPanel(clampPanel(start + (startY - ev.clientY)));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const onSepKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key === "ArrowUp") setPanel((p) => clampPanel(p + 32));
    else if (e.key === "ArrowDown") setPanel((p) => clampPanel(p - 32));
    else return;
    e.preventDefault();
  };

  const passed = results?.filter((r) => r.status === "pass").length ?? 0;
  const status = busy === "loading" ? (runLang === "python" ? "Loading Python (first run only)…" : "Starting…") : busy === "running" ? "Running…" : null;

  return (
    <div className="stu-cq" data-testid="code-question">
      <section className="stu-cq-left" aria-label="Problem">
        {prompt}
        {samples.length > 0 && (
          <div className="stu-cq-examples">
            {samples.map((t, i) => (
              <div key={i} className="stu-cq-example">
                <h2>Example {i + 1}</h2>
                <div className="stu-cq-io">
                  <div>
                    <span>Input</span>
                    <pre>{t.input || " "}</pre>
                  </div>
                  <div>
                    <span>Output</span>
                    <pre>{t.expected || " "}</pre>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="stu-cq-right" aria-label="Your code" ref={rightRef}>
        <div className="stu-cq-bar">
          {langOptions.length > 1 ? (
            <label className="stu-cq-langpick">
              Language
              <select
                data-testid="code-language-picker"
                value={langOption?.key}
                disabled={!!busy}
                onChange={(e) => switchLanguage(e.target.value)}
              >
                {langOptions.map((l) => (
                  <option key={l.key} value={l.key}>
                    {l.name}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <span className="stu-cq-lang" data-testid="code-language">
              {label}
            </span>
          )}
          <span className="stu-cq-meta">Time limit {fmtSeconds(limit)}</span>
          <span className="stu-spacer" />
          <span className="stu-cq-meta" id={`code-keys-${question.id}`}>
            Tab indents · Esc then Tab leaves the editor
          </span>
        </div>
        <div className="stu-cq-editor">
          <CodeEditor
            key={langOption?.key ?? lang}
            initialValue={codeRef.current}
            language={lang}
            onChange={onCode}
            label={`Your ${label} code`}
            describedBy={`code-keys-${question.id}`}
          />
        </div>

        {!runnable ? (
          <div className="stu-cq-note" role="note" data-testid="code-no-run">
            {writeOnly
              ? "Write your answer. It is checked after the exam."
              : langOption
                ? RUNS_AFTER_HAND_IN
                : `Run isn't available for ${label} in this exam. Your code is checked after the exam.`}
          </div>
        ) : (
          <>
            <div
              className="stu-cq-sep"
              role="separator"
              aria-orientation="horizontal"
              aria-label="Resize the test panel"
              aria-valuenow={panel}
              aria-valuemin={120}
              tabIndex={0}
              onPointerDown={onDragStart}
              onKeyDown={onSepKey}
            />
            <div className="stu-cq-panel" style={{ height: panel }}>
              <div className="stu-cq-tabs">
                <div role="tablist" aria-label="Tests">
                  <button
                    type="button"
                    role="tab"
                    id={`tab-samples-${question.id}`}
                    aria-selected={tab === "samples"}
                    aria-controls={`panel-samples-${question.id}`}
                    onClick={() => setTab("samples")}
                  >
                    Sample tests
                  </button>
                  <button
                    type="button"
                    role="tab"
                    id={`tab-custom-${question.id}`}
                    aria-selected={tab === "custom"}
                    aria-controls={`panel-custom-${question.id}`}
                    onClick={() => setTab("custom")}
                  >
                    Custom input
                  </button>
                </div>
                <span className="stu-spacer" />
                <span className="stu-cq-status" role="status">
                  {status}
                </span>
                <button
                  type="button"
                  className="stu-btn primary stu-cq-run"
                  disabled={!!busy || (tab === "samples" && samples.length === 0)}
                  onClick={() => void (tab === "samples" ? runSamples() : runCustom())}
                >
                  <PlayIcon />
                  {tab === "samples" ? "Run samples" : "Run"}
                </button>
              </div>
              {problem && (
                <p className="stu-cq-problem" role="alert">
                  {problem}
                </p>
              )}
              {tab === "samples" ? (
                <div role="tabpanel" id={`panel-samples-${question.id}`} aria-labelledby={`tab-samples-${question.id}`} className="stu-cq-out">
                  {samples.length === 0 ? (
                    <p className="stu-muted">This question has no sample tests. Use Custom input to try your code.</p>
                  ) : results === null ? (
                    <p className="stu-muted">Press Run samples to check your code against the examples.</p>
                  ) : (
                    <>
                      {results.length === samples.length && (
                        <p className={`stu-cq-summary${passed === samples.length ? " ok" : ""}`} data-testid="samples-summary">
                          {passed} of {samples.length} sample {samples.length === 1 ? "test" : "tests"} passed
                        </p>
                      )}
                      <ol className="stu-cq-results">
                        {results.map((r) => (
                          <SampleResult key={r.index} r={r} />
                        ))}
                      </ol>
                    </>
                  )}
                </div>
              ) : (
                <div role="tabpanel" id={`panel-custom-${question.id}`} aria-labelledby={`tab-custom-${question.id}`} className="stu-cq-out custom">
                  <label htmlFor={`custom-in-${question.id}`}>Input</label>
                  <textarea
                    id={`custom-in-${question.id}`}
                    className="stu-cq-stdin"
                    value={customInput}
                    spellCheck={false}
                    autoCorrect="off"
                    autoCapitalize="off"
                    autoComplete="off"
                    maxLength={20000}
                    placeholder="Type the input for this run"
                    onChange={(e) => setCustomInput(e.target.value)}
                  />
                  {custom && <RunOutput r={custom} />}
                </div>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  );
}

function SampleResult({ r }: { r: TestOutcome }) {
  const ok = r.status === "pass";
  return (
    <li className={`stu-cq-result ${ok ? "pass" : "fail"}`}>
      <details open={!ok}>
        <summary>
          <span className="name">Sample {r.index + 1}</span>
          <span className={`stu-cq-pill ${r.status}`}>{STATUS_LABEL[r.status]}</span>
          {r.status !== "unavailable" && r.status !== "timeout" && <span className="time">{r.timeMs} ms</span>}
        </summary>
        <div className="stu-cq-io three">
          <div>
            <span>Input</span>
            <pre>{r.input || " "}</pre>
          </div>
          <div>
            <span>Expected</span>
            <pre>{r.expected || " "}</pre>
          </div>
          <div>
            <span>Your output</span>
            <pre>{r.stdout || " "}</pre>
          </div>
        </div>
        {r.stderr && <pre className="stu-cq-stderr">{r.stderr}</pre>}
        {r.truncated && <p className="stu-muted stu-small">The output was very long and was cut.</p>}
      </details>
    </li>
  );
}

function RunOutput({ r }: { r: RunResult }) {
  return (
    <div className="stu-cq-run-out" data-testid="custom-output">
      <span className="lbl">
        Output
        {r.status === "ok" && <span className="time"> · {r.timeMs} ms</span>}
        {r.status === "timeout" && <span className="stu-cq-pill timeout">Time limit exceeded</span>}
        {r.status === "error" && <span className="stu-cq-pill error">Error</span>}
      </span>
      <pre>{r.stdout || " "}</pre>
      {r.stderr && <pre className="stu-cq-stderr">{r.stderr}</pre>}
      {r.truncated && <p className="stu-muted stu-small">The output was very long and was cut.</p>}
    </div>
  );
}

function PlayIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M4 2.5v11l9-5.5z" fill="currentColor" />
    </svg>
  );
}
