// Phase 4: code editor (CodeMirror 6, bundled; nothing loads from a CDN). Line numbers,
// syntax colours, bracket matching, auto-indent, Tab inserts spaces. No autocomplete, no
// hints. Remount (key) to load different code; `onChange` gets every edit.
import { useEffect, useRef } from "react";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { cpp } from "@codemirror/lang-cpp";
import { java } from "@codemirror/lang-java";
import { javascript } from "@codemirror/lang-javascript";
import { python } from "@codemirror/lang-python";
import {
  HighlightStyle,
  bracketMatching,
  indentOnInput,
  indentUnit,
  syntaxHighlighting,
} from "@codemirror/language";
import { EditorState, type Extension } from "@codemirror/state";
import {
  EditorView,
  drawSelection,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightSpecialChars,
  keymap,
  lineNumbers,
} from "@codemirror/view";
import { tags as t } from "@lezer/highlight";
import type { CodeLanguage } from "../../api/types";
import { MAX_CODE_CHARS, indentFor } from "../../lib/code/languages";
import "../../styles/code.css";

function languageExtension(lang: CodeLanguage): Extension {
  switch (lang) {
    case "python":
      return python();
    case "javascript":
      return javascript();
    case "java":
      return java();
    case "c":
    case "cpp":
      return cpp();
  }
}

// Our colours: logo blue for keywords, quiet greys for comments.
const fairplayHighlight = HighlightStyle.define([
  { tag: [t.keyword, t.controlKeyword, t.moduleKeyword, t.operatorKeyword, t.definitionKeyword], color: "#2f4bd8", fontWeight: "600" },
  { tag: [t.string, t.special(t.string), t.character], color: "#16794c" },
  { tag: [t.number, t.bool, t.null, t.atom], color: "#b4570b" },
  { tag: [t.comment, t.lineComment, t.blockComment], color: "#6b7686", fontStyle: "italic" },
  { tag: [t.function(t.variableName), t.function(t.propertyName)], color: "#7a3fb8" },
  { tag: [t.typeName, t.className, t.namespace], color: "#0e7490" },
  { tag: [t.definition(t.variableName)], color: "#1b2533" },
  { tag: [t.meta, t.processingInstruction], color: "#8f4508" },
  { tag: t.invalid, color: "#b42318" },
]);

const theme = EditorView.theme({
  "&": { height: "100%", fontSize: "calc(14px * var(--stu-text-scale, 1))", backgroundColor: "#fff" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": {
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace",
    lineHeight: "1.6",
  },
  ".cm-content": { padding: "10px 0", caretColor: "#2f4bd8" },
  ".cm-gutters": { backgroundColor: "#f8f9fb", color: "#8a94a3", borderRight: "1px solid #edf0f3" },
  ".cm-activeLine": { backgroundColor: "#f6f8fe" },
  ".cm-activeLineGutter": { backgroundColor: "#eef1fd", color: "#2f4bd8" },
  "&.cm-focused .cm-matchingBracket": { backgroundColor: "#dfe5fb", outline: "1px solid #bac5f5" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": { backgroundColor: "#d6ddf8 !important" },
  ".cm-cursor": { borderLeftColor: "#2f4bd8", borderLeftWidth: "2px" },
});

interface Props {
  initialValue: string;
  language: CodeLanguage;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  /** accessible name of the editing area */
  label: string;
  /** id of an element that describes the editor (e.g. the Tab key hint) */
  describedBy?: string;
  className?: string;
  /** element id, set on the editor's content element (so a <label htmlFor> works) */
  id?: string;
}

export function CodeEditor({ initialValue, language, onChange, readOnly = false, label, describedBy, className, id }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const changeRef = useRef(onChange);
  changeRef.current = onChange;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const attrs: Record<string, string> = {
      "aria-label": label,
      spellcheck: "false",
      autocorrect: "off",
      autocapitalize: "off",
      autocomplete: "off",
      "data-gramm": "false",
    };
    if (describedBy) attrs["aria-describedby"] = describedBy;
    if (id) attrs.id = id;
    const view = new EditorView({
      parent: host,
      state: EditorState.create({
        doc: initialValue,
        extensions: [
          lineNumbers(),
          highlightActiveLineGutter(),
          highlightSpecialChars(),
          history(),
          drawSelection(),
          indentOnInput(),
          bracketMatching(),
          highlightActiveLine(),
          syntaxHighlighting(fairplayHighlight),
          indentUnit.of(indentFor(language)),
          EditorState.tabSize.of(indentFor(language).length),
          keymap.of([indentWithTab, ...defaultKeymap, ...historyKeymap]),
          languageExtension(language),
          theme,
          EditorView.lineWrapping,
          EditorView.contentAttributes.of(attrs),
          EditorState.readOnly.of(readOnly),
          EditorView.editable.of(!readOnly),
          // never more than the server accepts
          EditorState.changeFilter.of((tr) => tr.newDoc.length <= MAX_CODE_CHARS || tr.newDoc.length <= tr.startState.doc.length),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) changeRef.current?.(u.state.doc.toString());
          }),
        ],
      }),
    });
    return () => view.destroy();
    // the editor is created once per mount; remount (key) to change code or language
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <div ref={hostRef} className={`fp-code-editor${readOnly ? " readonly" : ""}${className ? ` ${className}` : ""}`} />;
}
