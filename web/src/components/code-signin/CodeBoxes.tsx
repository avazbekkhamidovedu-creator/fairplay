import { useRef, type ClipboardEvent, type KeyboardEvent } from "react";

export const CODE_LENGTH = 6;

const clean = (raw: string) => raw.toUpperCase().replace(/[^A-Z0-9]/g, "");

export function CodeBoxes({
  chars,
  onChange,
  invalid,
  shakeKey,
  disabled,
  describedBy,
}: {
  chars: string[];
  onChange: (next: string[]) => void;
  invalid: boolean;
  shakeKey: number;
  disabled: boolean;
  describedBy?: string;
}) {
  const refs = useRef<Array<HTMLInputElement | null>>([]);
  const focus = (i: number) => {
    const el = refs.current[Math.max(0, Math.min(CODE_LENGTH - 1, i))];
    el?.focus();
    el?.select();
  };

  function fill(from: number, text: string) {
    const next = [...chars];
    let i = from;
    for (const c of text) {
      if (i >= CODE_LENGTH) break;
      next[i++] = c;
    }
    onChange(next);
    focus(i);
  }

  function typed(i: number, raw: string) {
    let v = clean(raw);
    const old = chars[i] ?? "";
    if (v.length > 1 && old) v = v.replace(old, "");
    if (!v) {
      if (!raw) {
        const next = [...chars];
        next[i] = "";
        onChange(next);
      }
      return;
    }
    fill(i, v);
  }

  function keyDown(i: number, e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Backspace") {
      e.preventDefault();
      const next = [...chars];
      if (next[i]) {
        next[i] = "";
        onChange(next);
      } else if (i > 0) {
        next[i - 1] = "";
        onChange(next);
        focus(i - 1);
      }
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      focus(i - 1);
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      focus(i + 1);
    }
  }

  function paste(e: ClipboardEvent<HTMLInputElement>) {
    e.preventDefault();
    const text = clean(e.clipboardData.getData("text"));
    if (text) fill(0, text.slice(0, CODE_LENGTH));
  }

  return (
    <div key={shakeKey} className={`cs-boxes${invalid ? " cs-invalid" : ""}`} role="group" aria-label="Exam code">
      {Array.from({ length: CODE_LENGTH }, (_, i) => (
        <input
          key={i}
          ref={(el) => {
            refs.current[i] = el;
          }}
          className="cs-box"
          aria-label={`Character ${i + 1} of ${CODE_LENGTH}`}
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy}
          value={chars[i] ?? ""}
          onChange={(e) => typed(i, e.target.value)}
          onKeyDown={(e) => keyDown(i, e)}
          onPaste={paste}
          onFocus={(e) => e.target.select()}
          inputMode="text"
          autoCapitalize="characters"
          autoComplete="off"
          autoCorrect="off"
          spellCheck={false}
          autoFocus={i === 0}
          disabled={disabled}
        />
      ))}
    </div>
  );
}
