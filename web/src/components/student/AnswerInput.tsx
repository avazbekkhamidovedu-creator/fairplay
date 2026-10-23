import { useState } from "react";
import type { AnswerValue, StudentQuestion } from "../../api/types";
import { LETTERS, fmtNum, wordCount } from "./format";
import { ChevronRightIcon } from "./icons";
import type { AssetContext } from "../../lib/questionAssets";
import { isRichFormat } from "../../lib/markdown";
import type { ExamLinks } from "../MarkdownContent";
import { McqMultiInput, OptionText, TrueFalseInput } from "./QuestionInputs";
import { optionOrder } from "../../lib/answerTypes";

interface Props {
  question: StudentQuestion;
  value: AnswerValue | undefined;
  onChange: (v: AnswerValue | undefined) => void;
  /** id of the element that labels the question (the prompt heading). */
  labelledBy: string;
  /** 2026-10-23: cross-out for multiple choice (local only, never graded). */
  cross?: CrossOut;
  /** Rich questions: pictures and links inside options */
  assets?: AssetContext | null;
  links?: ExamLinks | null;
  /** "shuffle": options shown in an order fixed by this seed (answers keep original indexes) */
  shuffleSeed?: string;
  /** display order of the options (original indexes); set by AnswerInput */
  order?: number[];
}

export interface CrossOut {
  /** The cross-out tool is switched on: show a cross-out button beside each option. */
  on: boolean;
  /** Crossed-out option indexes. */
  crossed: number[];
  onToggle: (index: number) => void;
}

/** The answer control for one question. Remount (key) per question. */
export function AnswerInput({ question, value, onChange, labelledBy, cross, assets, links, shuffleSeed }: Props) {
  const order =
    question.type === "mcq" && question.shuffle && !question.true_false && shuffleSeed !== undefined
      ? optionOrder(question.options.length, `${shuffleSeed}:${question.id}`)
      : undefined;
  // several answers (checkboxes) / true or false (two big options)
  if (question.type === "mcq" && question.multiple)
    return (
      <McqMultiInput question={question} value={value} onChange={onChange} labelledBy={labelledBy} assets={assets} links={links} order={order} />
    );
  if (question.type === "mcq" && question.true_false)
    return <TrueFalseInput question={question} value={value} onChange={onChange} labelledBy={labelledBy} assets={assets} links={links} />;
  if (question.type === "mcq")
    return (
      <McqInput
        question={question}
        value={value}
        onChange={onChange}
        labelledBy={labelledBy}
        cross={cross}
        assets={assets}
        links={links}
        order={order}
      />
    );
  // rich short answers are read by a person: room for a few sentences
  if (question.type === "short" && isRichFormat(question.prompt_format))
    return <EssayInput question={question} value={value} onChange={onChange} labelledBy={labelledBy} />;
  if (question.type === "numeric") return <NumericInput question={question} value={value} onChange={onChange} />;
  if (question.type === "essay") return <EssayInput question={question} value={value} onChange={onChange} labelledBy={labelledBy} />;
  return <ShortInput question={question} value={value} onChange={onChange} />;
}

function McqInput({ question, value, onChange, labelledBy, cross, assets, links, order: shown }: Props) {
  const count = question.options.length;
  // display position -> original option index (identity unless shuffled)
  const order = shown && shown.length === count ? shown : Array.from({ length: count }, (_, i) => i);
  const crossedOrig = new Set(cross?.crossed ?? []);
  // positions below are display positions; value, crossed and onToggle use original indexes
  const crossed = new Set(order.map((o, p) => (crossedOrig.has(o) ? p : -1)).filter((p) => p >= 0));
  const crossOn = !!cross?.on;
  const valuePos = typeof value === "number" ? order.indexOf(value) : -1;
  // Roving tabindex: the selected option (or the first) is the tab stop; arrows move + select.
  const focusIndex = valuePos >= 0 ? valuePos : 0;
  const choose = (p: number) => {
    // Choosing a crossed-out option brings it back.
    if (crossed.has(p)) cross?.onToggle(order[p]!);
    onChange(order[p]!);
  };
  // 2026-10-23 review: arrows never bring a crossed-out option back (only a click on it
  // does); they skip crossed-out options, unless every other option is crossed out.
  const move = (from: number, delta: number, el: HTMLElement) => {
    let next = (from + delta + count) % count;
    for (let k = 0; k < count - 1 && crossed.has(next); k++) next = (next + delta + count) % count;
    if (crossed.has(next)) next = (from + delta + count) % count;
    onChange(order[next]!);
    const group = el.closest("[role=radiogroup]");
    (group?.querySelectorAll<HTMLElement>("[role=radio]")[next])?.focus();
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div className={`stu-options${crossOn ? " cross-on" : ""}`} role="radiogroup" aria-labelledby={labelledBy}>
        {order.map((orig, i) => {
          const opt = question.options[orig] ?? "";
          const selected = value === orig;
          const isCrossed = crossed.has(i) && !selected;
          return (
            <div key={i} className={`stu-option-row${isCrossed ? " crossed" : ""}`}>
              <button
                type="button"
                role="radio"
                aria-checked={selected}
                aria-description={isCrossed ? "crossed out" : undefined}
                tabIndex={i === focusIndex ? 0 : -1}
                className="stu-option"
                onClick={() => (selected ? onChange(undefined) : choose(i))}
                onKeyDown={(e) => {
                  if (e.key === "ArrowDown" || e.key === "ArrowRight") {
                    e.preventDefault();
                    move(i, 1, e.currentTarget);
                  } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
                    e.preventDefault();
                    move(i, -1, e.currentTarget);
                  }
                }}
              >
                <span className="stu-option-letter" aria-hidden="true">
                  {LETTERS[i]}
                </span>
                <span className="stu-option-text">
                  <OptionText question={question} text={opt} assets={assets} links={links} />
                </span>
              </button>
              {crossOn && cross && (
                <button
                  type="button"
                  className="stu-cross-btn"
                  aria-pressed={isCrossed}
                  aria-label={isCrossed ? `Undo cross out ${LETTERS[i]}` : `Cross out ${LETTERS[i]}`}
                  onClick={() => {
                    if (selected && !isCrossed) onChange(undefined);
                    cross.onToggle(orig);
                  }}
                >
                  {isCrossed ? "Undo" : <span className="stu-cross-letter">{LETTERS[i]}</span>}
                </button>
              )}
            </div>
          );
        })}
      </div>
      <p className="stu-hint">
        {crossOn
          ? "Cross out answers you think are wrong. Click Undo to bring one back."
          : "Click an option to choose it. Click it again to clear."}
      </p>
    </div>
  );
}

function NumericInput({ question, value, onChange }: Omit<Props, "labelledBy">) {
  const [text, setText] = useState(value === undefined ? "" : String(value));
  const norm = text.trim().replace(",", ".");
  const bad = norm !== "" && !Number.isFinite(Number(norm));
  const id = `ans-${question.id}`;
  return (
    <div className="stu-field">
      <label htmlFor={id}>Your answer (a number)</label>
      <input
        id={id}
        className="stu-answer-input num"
        type="text"
        inputMode="decimal"
        autoComplete="off"
        spellCheck={false}
        value={text}
        aria-invalid={bad || undefined}
        aria-describedby={bad ? `${id}-err` : undefined}
        onChange={(e) => {
          const t = e.target.value;
          setText(t);
          const n = t.trim().replace(",", ".");
          if (n === "") onChange(undefined);
          else if (Number.isFinite(Number(n))) onChange(Number(n));
        }}
      />
      {bad && (
        <span id={`${id}-err`} className="stu-field-error">
          Please type a number, for example 3.14
        </span>
      )}
    </div>
  );
}

function ShortInput({ question, value, onChange }: Omit<Props, "labelledBy">) {
  const [text, setText] = useState(value === undefined ? "" : String(value));
  const id = `ans-${question.id}`;
  return (
    <div className="stu-field">
      <label htmlFor={id}>Your answer</label>
      <input
        id={id}
        className="stu-answer-input"
        type="text"
        autoComplete="off"
        autoCorrect="off"
        autoCapitalize="off"
        spellCheck={false}
        maxLength={10000}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          onChange(e.target.value.trim() === "" ? undefined : e.target.value);
        }}
      />
    </div>
  );
}

/** "aim for 600–900 words" / "at least 600 words" / "at most 900 words" / "" */
export function wordAim(min: number | null | undefined, max: number | null | undefined): string {
  if (min && max) return `aim for ${min}–${max} words`;
  if (min) return `aim for at least ${min} words`;
  if (max) return `at most ${max} words`;
  return "";
}

/** The essay rubric, shown as a quiet collapsible under the prompt. */
export function RubricPanel({ question }: { question: StudentQuestion }) {
  const rubric = question.rubric ?? [];
  if (rubric.length === 0) return null;
  const total = rubric.reduce((s, r) => s + (Number(r.points) || 0), 0);
  return (
    <details className="stu-rubric-details">
      <summary>
        <ChevronRightIcon />
        How this is graded
        <span className="pts">
          {rubric.length === 1 ? "1 criterion" : `${rubric.length} criteria`} · {fmtNum(total)} {total === 1 ? "point" : "points"}
        </span>
      </summary>
      <ul className="stu-rubric-list">
        {rubric.map((r, i) => (
          <li key={i}>
            <span>{r.label}</span>
            <b>
              {fmtNum(r.points)} <span className="sr-only">points</span>
            </b>
          </li>
        ))}
      </ul>
    </details>
  );
}

export function EssayInput({ question, value, onChange, labelledBy }: Props) {
  // labelledBy (the prompt) describes the textarea; its label is the visible "Your answer".
  const [text, setText] = useState(typeof value === "string" ? value : "");
  const words = wordCount(text);
  const min = question.min_words ?? null;
  const max = question.max_words ?? null;
  const aim = wordAim(min, max);
  const target = max ?? min;
  const over = max !== null && words > max;
  const pct = target ? Math.min(100, Math.round((words / target) * 100)) : 0;
  const id = `ans-${question.id}`;
  return (
    <div className="stu-editor">
      <div className="stu-editor-bar">
        <label htmlFor={id}>Your answer</label>
        <span className="stu-spacer" />
        <span>Spell check off</span>
      </div>
      <textarea
        id={id}
        aria-describedby={`${labelledBy} ${id}-count`}
        value={text}
        spellCheck={false}
        autoCorrect="off"
        autoCapitalize="off"
        autoComplete="off"
        maxLength={50000}
        rows={14}
        onChange={(e) => {
          setText(e.target.value);
          onChange(e.target.value.trim() === "" ? undefined : e.target.value);
        }}
      />
      <div className="stu-editor-foot">
        <div className="stu-wordcount" id={`${id}-count`}>
          <span>
            <span data-testid="word-count">{words === 1 ? "1 word" : `${words} words`}</span>
            {aim && <span className="aim"> · {aim}</span>}
            {over && <span className="over"> · over the limit</span>}
          </span>
          {target ? (
            <div className={`stu-progress${over ? " over" : ""}`} aria-hidden="true">
              <span style={{ width: `${pct}%` }} />
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
