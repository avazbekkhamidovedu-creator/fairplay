// Rich question text: FairPlay markdown and sanitized HTML.
//
// - "markdown": markdown with math. Math with KaTeX (bundled, fonts included: nothing from a
//   CDN), "~…~" inline and "$$…$$" display; fenced code highlighted; tables; raw HTML is
//   allowed, so the result is sanitized here (no script, iframe, style, event
//   handlers, javascript: links).
// - "html": HTML made elsewhere (a full-markup problem), sanitized the same way.
// - Pictures are "fp-asset:<name>": rendered as <img data-fp-asset="name"> without a
//   src; the page fills the src (attempt / teacher asset URL, or the offline package).
// - Fill in the blank: "___" (three or more underscores, not inside a word) becomes
//   <span data-fp-blank="i">, where the page puts an input.
//
// Math is rendered after sanitizing: the sanitized markup only carries placeholders with
// a per-render nonce, which are then replaced by KaTeX's own output (KaTeX escapes its
// input and has trust off, so \href / \url / \htmlClass do nothing).
import DOMPurify from "dompurify";
import katex from "katex";
import MarkdownIt from "markdown-it";
import type StateBlock from "markdown-it/lib/rules_block/state_block.mjs";
import type StateInline from "markdown-it/lib/rules_inline/state_inline.mjs";
import hljs from "highlight.js/lib/core";
import c from "highlight.js/lib/languages/c";
import cpp from "highlight.js/lib/languages/cpp";
import java from "highlight.js/lib/languages/java";
import javascript from "highlight.js/lib/languages/javascript";
import python from "highlight.js/lib/languages/python";
import type { PromptFormat } from "../api/types";

hljs.registerLanguage("python", python);
hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("c", c);
hljs.registerLanguage("cpp", cpp);
hljs.registerLanguage("java", java);
const HL_ALIASES: Record<string, string> = {
  py: "python", py3: "python", python3: "python", pypy: "python", pypy3: "python",
  js: "javascript", node: "javascript", c11: "c", "c++": "cpp", cc: "cpp", cxx: "cpp",
  cpp11: "cpp", cpp14: "cpp", cpp17: "cpp", cpp20: "cpp", java8: "java", java11: "java",
};

export const ASSET_SCHEME = "fp-asset:";

export interface RenderOptions {
  /** turn "___" into blank placeholders (fill in the blank) */
  blanks?: boolean;
  /** one line of text (an option): no paragraphs */
  inline?: boolean;
}

export interface Rendered {
  html: string;
  /** number of blank placeholders in html */
  blankCount: number;
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]!);
}

// ---------------------------------------------------------------- markdown-it rules

interface Env {
  math: { tex: string; display: boolean }[];
  blanks: number;
  nonce: string;
  blanksOn: boolean;
}

function mathInline(state: StateInline, silent: boolean): boolean {
  const src = state.src;
  const start = state.pos;
  const ch = src.charCodeAt(start);
  let open: string;
  if (ch === 0x7e /* ~ */) open = "~";
  else if (ch === 0x24 /* $ */ && src.charCodeAt(start + 1) === 0x24) open = "$$";
  else return false;
  let end = start + open.length;
  for (;;) {
    end = src.indexOf(open, end);
    if (end < 0) return false;
    if (src.charCodeAt(end - 1) === 0x5c /* \ */) {
      end += 1;
      continue;
    }
    break;
  }
  const tex = src.slice(start + open.length, end);
  if (!tex.trim()) return false;
  if (!silent) {
    const t = state.push("fp_math", "", 0);
    t.content = tex;
    t.meta = { display: open === "$$" };
  }
  state.pos = end + open.length;
  return true;
}

function mathBlock(state: StateBlock, startLine: number, endLine: number, silent: boolean): boolean {
  let pos = state.bMarks[startLine]! + state.tShift[startLine]!;
  let max = state.eMarks[startLine]!;
  if (state.sCount[startLine]! - state.blkIndent >= 4) return false;
  if (pos + 2 > max || state.src.slice(pos, pos + 2) !== "$$") return false;
  pos += 2;
  let first = state.src.slice(pos, max);
  let last = "";
  let line = startLine;
  let found = false;
  if (first.trim().endsWith("$$")) {
    first = first.trim().slice(0, -2);
    found = true;
  }
  if (silent) return true;
  while (!found) {
    line += 1;
    if (line >= endLine) break;
    pos = state.bMarks[line]! + state.tShift[line]!;
    max = state.eMarks[line]!;
    const text = state.src.slice(pos, max);
    if (text.trim().endsWith("$$")) {
      last = text.trim().slice(0, -2);
      found = true;
    }
  }
  if (!found) return false; // no closing $$: ordinary text
  state.line = line + 1;
  const t = state.push("fp_math_block", "", 0);
  t.block = true;
  t.content = (first.trim() ? first + "\n" : "") + state.getLines(startLine + 1, line, state.tShift[startLine]!, true) + last;
  t.map = [startLine, state.line];
  return true;
}

function blankInline(state: StateInline, silent: boolean): boolean {
  // Always recognised (2026-10-24): otherwise "___" became empty emphasis and the blank
  // vanished from the text wherever there is no input (teacher views, review, results).
  const src = state.src;
  const start = state.pos;
  if (src.charCodeAt(start) !== 0x5f /* _ */) return false;
  let end = start;
  while (src.charCodeAt(end) === 0x5f) end += 1;
  if (end - start < 3) return false;
  const before = start > 0 ? src[start - 1]! : " ";
  const after = end < src.length ? src[end]! : " ";
  if (/\w/.test(before) || /\w/.test(after)) return false;
  if (!silent) {
    const t = state.push("fp_blank", "", 0);
    t.meta = { index: (state.env as Env).blanks++ };
  }
  state.pos = end;
  return true;
}

function makeMd(): MarkdownIt {
  const md = new MarkdownIt({
    html: true,
    linkify: false,
    typographer: false,
    breaks: false,
    highlight(code, lang) {
      const name = HL_ALIASES[lang.toLowerCase()] ?? lang.toLowerCase();
      const known = name && hljs.getLanguage(name);
      const body = known ? hljs.highlight(code, { language: name, ignoreIllegals: true }).value : escapeHtml(code);
      return `<pre class="fp-md-code"><code class="hljs${known ? ` language-${name}` : ""}">${body}</code></pre>`;
    },
  });
  // "~" is math in this flavour, so "~~strike~~" is not markdown here.
  md.disable("strikethrough");
  md.inline.ruler.before("escape", "fp_math", mathInline);
  md.inline.ruler.before("emphasis", "fp_blank", blankInline);
  md.block.ruler.before("fence", "fp_math_block", mathBlock, { alt: ["paragraph", "reference", "blockquote", "list"] });
  const placeholder = (env: Env, tex: string, display: boolean) => {
    env.math.push({ tex, display });
    return `<span data-fp-math="${env.nonce}-${env.math.length - 1}"></span>`;
  };
  md.renderer.rules.fp_math = (tokens, idx, _o, env: Env) =>
    placeholder(env, tokens[idx]!.content, !!(tokens[idx]!.meta as { display: boolean }).display);
  md.renderer.rules.fp_math_block = (tokens, idx, _o, env: Env) =>
    `<div class="fp-md-display">${placeholder(env, tokens[idx]!.content, true)}</div>\n`;
  md.renderer.rules.fp_blank = (tokens, idx, _o, env: Env) =>
    env.blanksOn
      ? `<span data-fp-blank="${(tokens[idx]!.meta as { index: number }).index}"></span>`
      : `<span class="fp-blank-view" aria-label="blank ${(tokens[idx]!.meta as { index: number }).index + 1}">______</span>`;
  // markdown-it refuses unknown link schemes only for javascript:/vbscript:/file:/data:
  // (except images); fp-asset: passes and is handled by the sanitizer hook below.
  return md;
}

let mdInstance: MarkdownIt | null = null;
const md = () => (mdInstance ??= makeMd());

// ---------------------------------------------------------------- sanitizer

let purifier: ReturnType<typeof DOMPurify> | null = null;

const FORBID_TAGS = [
  "script", "style", "iframe", "frame", "frameset", "object", "embed", "applet", "form", "input",
  "button", "textarea", "select", "option", "link", "meta", "base", "svg", "math", "template",
  "audio", "video", "source", "track", "noscript", "dialog", "portal",
];

function getPurifier(): ReturnType<typeof DOMPurify> {
  if (purifier) return purifier;
  const p = DOMPurify(window);
  p.addHook("uponSanitizeElement", (node, data) => {
    if (data.tagName !== "img") return;
    const el = node as Element;
    const src = el.getAttribute("src") ?? "";
    if (src.toLowerCase().startsWith(ASSET_SCHEME)) {
      let name = src.slice(ASSET_SCHEME.length);
      try {
        name = decodeURIComponent(name);
      } catch {
        /* keep as written */
      }
      el.setAttribute("data-fp-asset", name);
      el.removeAttribute("src");
    } else if (!/^(https:|data:image\/(png|jpe?g|gif|webp);)/i.test(src)) {
      el.removeAttribute("src");
    }
  });
  p.addHook("afterSanitizeAttributes", (node) => {
    const el = node as Element;
    if (el.tagName === "A") {
      const href = el.getAttribute("href") ?? "";
      if (/^https?:\/\//i.test(href)) {
        el.setAttribute("target", "_blank");
        el.setAttribute("rel", "noopener noreferrer");
      } else if (!href.startsWith("#")) {
        el.removeAttribute("href");
      }
    }
    if (el.tagName === "IMG") {
      el.setAttribute("referrerpolicy", "no-referrer");
      el.setAttribute("loading", "lazy");
    }
  });
  purifier = p;
  return p;
}

/** Sanitize HTML (from another system, or from markdown with raw HTML). */
export function sanitizeHtml(html: string): string {
  return getPurifier().sanitize(html, {
    FORBID_TAGS,
    FORBID_ATTR: ["style", "srcset", "formaction", "action", "background", "ping", "xlink:href"],
    ALLOW_DATA_ATTR: false,
    ADD_ATTR: ["data-fp-math", "data-fp-blank", "data-fp-asset", "target", "referrerpolicy", "loading"],
    SANITIZE_NAMED_PROPS: true,
    ALLOW_UNKNOWN_PROTOCOLS: false,
  }) as unknown as string;
}

// ---------------------------------------------------------------- math

export function renderTex(tex: string, display: boolean): string {
  try {
    return katex.renderToString(tex, {
      displayMode: display,
      throwOnError: false,
      trust: false,
      strict: "ignore",
      maxExpand: 1000,
      maxSize: 50,
      output: "htmlAndMathml",
    });
  } catch {
    return `<code class="fp-md-tex-error">${escapeHtml(tex)}</code>`;
  }
}

function fillMath(html: string, env: Env): string {
  if (env.math.length === 0) return html;
  const re = new RegExp(`<span data-fp-math="${env.nonce}-(\\d+)"></span>`, "g");
  return html.replace(re, (_m, i: string) => {
    const m = env.math[Number(i)];
    return m ? renderTex(m.tex, m.display) : "";
  });
}

/** HTML from other systems marks math as <span class="inline-math">~x~</span> / class="display-math". */
function htmlMath(html: string, env: Env): string {
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
  const nodes = doc.body.querySelectorAll(".inline-math, .display-math, .tex-math");
  if (nodes.length === 0) return html;
  nodes.forEach((n) => {
    const display = n.classList.contains("display-math");
    let tex = (n.textContent ?? "").trim();
    if (tex.startsWith("$$") && tex.endsWith("$$") && tex.length >= 4) tex = tex.slice(2, -2);
    else if (tex.startsWith("~") && tex.endsWith("~") && tex.length >= 2) tex = tex.slice(1, -1);
    else if (tex.startsWith("\\(") && tex.endsWith("\\)")) tex = tex.slice(2, -2);
    else if (tex.startsWith("\\[") && tex.endsWith("\\]")) tex = tex.slice(2, -2);
    env.math.push({ tex, display });
    const ph = doc.createElement("span");
    ph.setAttribute("data-fp-math", `${env.nonce}-${env.math.length - 1}`);
    n.replaceWith(ph);
  });
  return doc.body.innerHTML;
}

function nonce(): string {
  const a = new Uint32Array(2);
  try {
    crypto.getRandomValues(a);
  } catch {
    a[0] = Math.floor(Math.random() * 2 ** 32);
    a[1] = Date.now() >>> 0;
  }
  return `m${a[0]!.toString(36)}${a[1]!.toString(36)}`;
}

function countBlanks(html: string): number {
  return (html.match(/data-fp-blank="/g) ?? []).length;
}

// ---------------------------------------------------------------- public

/** Markdown -> safe HTML. */
export function renderMarkdown(src: string, opts: RenderOptions = {}): Rendered {
  const env: Env = { math: [], blanks: 0, nonce: nonce(), blanksOn: !!opts.blanks };
  const raw = opts.inline ? md().renderInline(src ?? "", env) : md().render(src ?? "", env);
  const clean = sanitizeHtml(raw);
  return { html: fillMath(clean, env), blankCount: opts.blanks ? countBlanks(clean) : 0 };
}

/** HTML -> safe HTML (marked math is rendered with KaTeX). */
export function renderHtml(src: string): Rendered {
  const env: Env = { math: [], blanks: 0, nonce: nonce(), blanksOn: false };
  const clean = sanitizeHtml(htmlMath(sanitizeHtml(src ?? ""), env));
  return { html: fillMath(clean, env), blankCount: 0 };
}

export function isRichFormat(f: PromptFormat | undefined | null): f is "markdown" | "html" {
  return f === "markdown" || f === "html";
}

/** Render a prompt in its format. "plain" is escaped text (line breaks kept by CSS). */
export function renderPrompt(format: PromptFormat | undefined | null, text: string, opts: RenderOptions = {}): Rendered {
  if (format === "html") return renderHtml(text);
  if (format === "markdown") return renderMarkdown(text, opts);
  return { html: escapeHtml(text ?? ""), blankCount: 0 };
}

/** fp-asset names a text refers to (for prefetching while online). */
export function assetNames(text: string | null | undefined): string[] {
  if (!text) return [];
  const out = new Set<string>();
  const re = /fp-asset:([^\s)"'<>]+)/gi;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    let n = m[1]!;
    try {
      n = decodeURIComponent(n);
    } catch {
      /* keep */
    }
    out.add(n);
  }
  return [...out];
}

/** Rich text as plain text for one-line summaries (lists, review rows), cut at `max`. */
export function plainSummary(format: PromptFormat | undefined | null, text: string, max = 160): string {
  if (!isRichFormat(format)) return text; // FairPlay text: unchanged
  const doc = new DOMParser().parseFromString(renderPrompt(format, text).html, "text/html");
  doc.querySelectorAll(".katex-mathml").forEach((n) => n.remove());
  const t = (doc.body.textContent ?? "").replace(/\s+/g, " ").trim();
  return t.length > max ? `${t.slice(0, max - 1)}…` : t;
}
