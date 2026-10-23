// DKU OJ bridge (docs/dkuoj-bridge.md §6): answer controls of DKU OJ questions.
// - mcq with multiple answers: checkboxes (answer = sorted option indexes)
// - true / false: two big options (answer = option index)
// - fill in the blank: inputs inside the statement at "___", or numbered inputs under it
//   when the markers cannot be placed (answer = one string per blank)
// - file upload: picker with extension / size checks (answer = upload ids)
// Every answer goes through the exam page's answer queue, like the other types.
import { useRef, useState, type ReactNode, type Ref } from "react";
import { NetworkError } from "../../api/client";
import type { AnswerUpload, AnswerValue, StudentQuestion } from "../../api/types";
import type { AssetContext } from "../../lib/ojAssets";
import {
  blankAnswer,
  blankValues,
  extensionList,
  fileProblem,
  formatSize,
  multiAnswer,
  normalizeExtensions,
  selectedIndexes,
  uploadIds,
  uploadedFile,
} from "../../lib/ojAnswers";
import { isOjFormat } from "../../lib/ojMarkdown";
import { OjContent, type ExamLinks } from "../OjContent";
import { LETTERS } from "./format";

interface BaseProps {
  question: StudentQuestion;
  value: AnswerValue | undefined;
  onChange: (v: AnswerValue | undefined) => void;
  labelledBy: string;
  assets?: AssetContext | null;
  links?: ExamLinks | null;
}

/** An option's text: DKU OJ markdown (one line) or plain text. */
export function OptionText({
  question,
  text,
  assets,
  links,
}: {
  question: StudentQuestion;
  text: string;
  assets?: AssetContext | null;
  links?: ExamLinks | null;
}) {
  if (!isOjFormat(question.prompt_format)) return <>{text}</>;
  return <OjContent format="oj_md" text={text} inline assets={assets} links={links} />;
}

export function McqMultiInput({ question, value, onChange, labelledBy, assets, links }: BaseProps) {
  const chosen = new Set(selectedIndexes(value, question.options.length));
  const toggle = (i: number) => {
    const next = new Set(chosen);
    if (next.has(i)) next.delete(i);
    else next.add(i);
    onChange(multiAnswer(next));
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="stu-options" role="group" aria-labelledby={labelledBy} data-testid="mcq-multi">
        {question.options.map((opt, i) => (
          <div key={i} className="stu-option-row">
            <button
              type="button"
              role="checkbox"
              aria-checked={chosen.has(i)}
              className="stu-option"
              onClick={() => toggle(i)}
            >
              <span className="stu-option-letter" aria-hidden="true">
                {chosen.has(i) ? "✓" : LETTERS[i]}
              </span>
              <span className="stu-option-text">
                <span className="sr-only">{LETTERS[i]}. </span>
                <OptionText question={question} text={opt} assets={assets} links={links} />
              </span>
            </button>
          </div>
        ))}
      </div>
      <p className="stu-hint">Choose every answer that is right. Click an option again to clear it.</p>
    </div>
  );
}

export function TrueFalseInput({ question, value, onChange, labelledBy, assets, links }: BaseProps) {
  const options = question.options.length === 2 ? question.options : ["True", "False"];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="stu-tf" role="radiogroup" aria-labelledby={labelledBy} data-testid="true-false">
        {options.map((opt, i) => (
          <button
            key={i}
            type="button"
            role="radio"
            aria-checked={value === i}
            tabIndex={value === i || (typeof value !== "number" && i === 0) ? 0 : -1}
            className="stu-option"
            onClick={() => onChange(value === i ? undefined : i)}
            onKeyDown={(e) => {
              if (["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"].includes(e.key)) {
                e.preventDefault();
                const next = i === 0 ? 1 : 0;
                onChange(next);
                (e.currentTarget.parentElement?.querySelectorAll<HTMLElement>("[role=radio]")[next])?.focus();
              }
            }}
          >
            <OptionText question={question} text={opt} assets={assets} links={links} />
          </button>
        ))}
      </div>
      <p className="stu-hint">Click an answer to choose it. Click it again to clear.</p>
    </div>
  );
}

interface FillProps extends BaseProps {
  /** the prompt element's id and ref (the page moves focus there) */
  promptRef?: Ref<HTMLElement>;
}

/** Statement with an input at each blank. Falls back to numbered inputs under it. */
export function FillBlankQuestion({ question, value, onChange, labelledBy, assets, links, promptRef }: FillProps) {
  const [markers, setMarkers] = useState<number | null>(null);
  const declared = question.blanks && question.blanks > 0 ? question.blanks : null;
  const [values, setValues] = useState<string[]>(() =>
    blankValues(value, Math.max(declared ?? 0, Array.isArray(value) ? value.length : 0)),
  );
  const inline = markers !== null && markers > 0 && (declared === null || markers === declared);
  const n = declared ?? markers ?? 0;
  const vals = values.length >= n ? values : [...values, ...Array.from({ length: n - values.length }, () => "")];

  const setOne = (i: number, s: string) => {
    const next = [...vals];
    next[i] = s;
    setValues(next);
    onChange(blankAnswer(next.slice(0, n)));
  };
  const input = (i: number, withLabel: boolean): ReactNode => (
    <input
      key={i}
      type="text"
      className={withLabel ? "stu-answer-input" : "stu-blank"}
      aria-label={`Blank ${i + 1}`}
      data-testid={`blank-${i}`}
      autoComplete="off"
      autoCorrect="off"
      autoCapitalize="off"
      spellCheck={false}
      maxLength={2000}
      value={vals[i] ?? ""}
      onChange={(e) => setOne(i, e.target.value)}
    />
  );

  return (
    <>
      <OjContent
        ref={promptRef}
        id={labelledBy}
        tabIndex={-1}
        className="stu-prompt"
        format={question.prompt_format ?? "oj_md"}
        text={question.prompt}
        assets={assets}
        links={links}
        renderBlank={(i) => (inline && i < n ? input(i, false) : <span className="stu-blank-mark">___</span>)}
        onBlankCount={setMarkers}
      />
      {markers !== null && !inline && n > 0 && (
        <ol className="stu-blank-list" aria-label="Blanks" data-testid="blank-list">
          {Array.from({ length: n }, (_, i) => (
            <li key={i}>
              <span className="stu-option-letter" aria-hidden="true">
                {i + 1}
              </span>
              {input(i, true)}
            </li>
          ))}
        </ol>
      )}
      <p className="stu-hint">
        {n === 1 ? "Type your answer in the blank." : `Type your answers in the ${n} blanks.`}
        {question.blank_points && question.blank_points.length === n
          ? ` Each blank: ${question.blank_points.join(" / ")} points.`
          : ""}
      </p>
    </>
  );
}

interface UploadProps {
  question: StudentQuestion;
  value: AnswerValue | undefined;
  onChange: (v: AnswerValue | undefined) => void;
  /** the attempt (for the file names kept on this computer) */
  attemptId: string;
  /** uploads one file; null = cannot upload here */
  upload: ((file: File) => Promise<AnswerUpload>) | null;
  /** remembers the uploaded file's name */
  onUploaded?: (f: AnswerUpload) => void;
}

export function FileUploadInput({ question, value, onChange, attemptId, upload, onUploaded }: UploadProps) {
  const ids = uploadIds(value);
  const max = Math.max(1, question.max_files ?? 1);
  const exts = normalizeExtensions(question.allowed_extensions);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [known, setKnown] = useState<Record<string, AnswerUpload>>({});
  const replaceRef = useRef<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = `file-${question.id}`;

  const info = (id: string): AnswerUpload => known[id] ?? uploadedFile(attemptId, id) ?? { id, name: "Uploaded file", size: 0 };

  async function pick(file: File | undefined) {
    if (!file) return;
    setProblem(null);
    const bad = fileProblem(question, file);
    if (bad) {
      setProblem(bad);
      return;
    }
    if (!upload) {
      setProblem("Files can be uploaded once this computer is connected to the exam.");
      return;
    }
    setBusy(true);
    try {
      const up = await upload(file);
      setKnown((k) => ({ ...k, [up.id]: up }));
      onUploaded?.(up);
      const replacing = replaceRef.current;
      const next = replacing ? ids.map((x) => (x === replacing ? up.id : x)) : max === 1 ? [up.id] : [...ids, up.id];
      onChange(next.length ? next : undefined);
    } catch (e) {
      setProblem(
        e instanceof NetworkError
          ? "Uploading needs the connection. Try again when you are back online; your other answers are saved."
          : e instanceof Error && e.message
            ? e.message
            : "The upload failed. Try again.",
      );
    } finally {
      replaceRef.current = null;
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  const choose = (replacing: string | null) => {
    replaceRef.current = replacing;
    inputRef.current?.click();
  };

  const rules = [
    exts.length ? `${extensionList(exts)} files` : "Any file type",
    question.max_file_size ? `up to ${formatSize(question.max_file_size)}` : null,
    max > 1 ? `at most ${max} files` : null,
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <div className="stu-upload" data-testid="file-upload">
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        className="sr-only"
        accept={exts.length ? exts.map((e) => `.${e}`).join(",") : undefined}
        tabIndex={-1}
        aria-hidden="true"
        data-testid="file-input"
        onChange={(e) => void pick(e.target.files?.[0])}
      />
      {ids.length > 0 && (
        <ul className="stu-upload-files" aria-label="Your files">
          {ids.map((id) => {
            const f = info(id);
            return (
              <li key={id} className="stu-upload-file">
                <span className="name">{f.name}</span>
                {f.size > 0 && <span className="size">{formatSize(f.size)}</span>}
                <button type="button" className="stu-btn sm" disabled={busy} onClick={() => choose(id)}>
                  Replace
                </button>
                <button
                  type="button"
                  className="stu-btn sm quiet"
                  disabled={busy}
                  aria-label={`Remove ${f.name}`}
                  onClick={() => {
                    const next = ids.filter((x) => x !== id);
                    onChange(next.length ? next : undefined);
                  }}
                >
                  Remove
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {ids.length < max && (
        <div className="stu-upload-drop">
          <button type="button" className="stu-btn primary" disabled={busy} onClick={() => choose(null)}>
            {busy ? "Uploading…" : ids.length ? "Add another file" : "Choose a file"}
          </button>
          <span className="stu-upload-rules">{rules}</span>
        </div>
      )}
      {busy && ids.length >= max && (
        <p className="stu-hint" role="status">
          Uploading…
        </p>
      )}
      {problem && (
        <p className="stu-field-error" role="alert">
          {problem}
        </p>
      )}
    </div>
  );
}
