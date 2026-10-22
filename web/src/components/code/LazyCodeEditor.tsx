// Phase 4: the code editor (CodeMirror and its language modes, ~600 kB) is its own chunk,
// so pages without coding questions stay small. The exam page preloads it as soon as an
// exam with coding questions opens; if it still cannot load (Wi-Fi dropped before it
// arrived), a plain text box takes its place so the student can keep writing.
import { useEffect, useState, type ComponentProps, type ComponentType } from "react";
import type { CodeEditor as CodeEditorType } from "./CodeEditor";
import "../../styles/code.css";

type Props = ComponentProps<typeof CodeEditorType>;

let loaded: ComponentType<Props> | null = null;
let loading: Promise<ComponentType<Props>> | null = null;

export function preloadCodeEditor(): Promise<ComponentType<Props>> {
  if (loaded) return Promise.resolve(loaded);
  loading ??= import("./CodeEditor").then(
    (m) => (loaded = m.CodeEditor),
    (e) => {
      loading = null;
      throw e;
    },
  );
  return loading;
}

export function CodeEditor(props: Props) {
  const [Editor, setEditor] = useState<ComponentType<Props> | null>(() => loaded);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (Editor) return;
    let alive = true;
    preloadCodeEditor().then(
      (C) => alive && setEditor(() => C),
      () => alive && setFailed(true),
    );
    return () => {
      alive = false;
    };
  }, [Editor]);
  if (Editor) return <Editor {...props} />;
  if (failed) return <PlainCodeBox {...props} />;
  return (
    <div className="fp-code-loading" role="status">
      Loading the editor…
    </div>
  );
}

/** Fallback editor: a monospace text box (Tab inserts spaces). */
function PlainCodeBox({ initialValue, onChange, readOnly, label, describedBy, id }: Props) {
  const [text, setText] = useState(initialValue);
  return (
    <textarea
      id={id}
      className="fp-code-plain"
      aria-label={label}
      aria-describedby={describedBy}
      value={text}
      readOnly={readOnly}
      spellCheck={false}
      autoCorrect="off"
      autoCapitalize="off"
      autoComplete="off"
      maxLength={50000}
      onChange={(e) => {
        setText(e.target.value);
        onChange?.(e.target.value);
      }}
      onKeyDown={(e) => {
        if (e.key !== "Tab" || e.shiftKey || readOnly) return;
        e.preventDefault();
        const el = e.currentTarget;
        const { selectionStart: a, selectionEnd: b } = el;
        const next = text.slice(0, a) + "    " + text.slice(b);
        setText(next);
        onChange?.(next);
        requestAnimationFrame(() => el.setSelectionRange(a + 4, a + 4));
      }}
    />
  );
}
