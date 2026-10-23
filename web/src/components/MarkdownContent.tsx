// A question text (or option, or feedback) written in markdown or HTML, rendered
// safely (lib/markdown.ts). Pictures come from the attempt / teacher asset URL or the
// offline package. During an exam, links only open the exam's allowed websites (in the
// Websites panel); every other link is shown as plain text.
import {
  forwardRef,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import type { PromptFormat } from "../api/types";
import { type AssetContext, resolveAsset } from "../lib/questionAssets";
import { renderPrompt } from "../lib/markdown";
import { hostAllowed } from "../lib/websites";
import "katex/dist/katex.min.css";
import "../styles/questions.css";

/** Links during an exam: only `allowed` hosts work, and they open through onOpen. */
export interface ExamLinks {
  allowed: string[];
  onOpen?: (url: string) => void;
}

export const BLOCKED_LINK_TITLE = "Links to other websites are blocked during the exam";

interface Props {
  format: PromptFormat | undefined | null;
  text: string;
  /** one line (an option): no paragraphs, rendered in a span */
  inline?: boolean;
  assets?: AssetContext | null;
  /** set on the student exam screen; teacher views leave it out (links open a new tab) */
  links?: ExamLinks | null;
  /** fill in the blank: an input for each "___" */
  renderBlank?: (index: number) => ReactNode;
  /** how many blank markers the text has (fill in the blank) */
  onBlankCount?: (n: number) => void;
  id?: string;
  className?: string;
  tabIndex?: number;
  "data-testid"?: string;
}

export const PICTURE_NEEDS_CONNECTION = "Picture needs the connection";

/** In place of a picture that cannot load (offline, or the exam is over). */
function missingPicture(img: HTMLImageElement): HTMLElement {
  const span = document.createElement("span");
  span.className = "fp-md-img-missing";
  span.setAttribute("role", "img");
  const alt = img.getAttribute("alt") ?? "";
  span.setAttribute("aria-label", alt ? `${alt} (${PICTURE_NEEDS_CONNECTION.toLowerCase()})` : PICTURE_NEEDS_CONNECTION);
  span.setAttribute("data-fp-asset", img.getAttribute("data-fp-asset") ?? "");
  span.textContent = alt ? `${alt}: ${PICTURE_NEEDS_CONNECTION.toLowerCase()}` : PICTURE_NEEDS_CONNECTION;
  return span;
}

function hostOf(href: string): string | null {
  try {
    const u = new URL(href);
    return u.protocol === "https:" || u.protocol === "http:" ? u.host : null;
  } catch {
    return null;
  }
}

export const MarkdownContent = forwardRef<HTMLElement, Props>(function MarkdownContent(
  { format, text, inline = false, assets, links, renderBlank, onBlankCount, id, className, tabIndex, "data-testid": testId },
  ref,
) {
  const blanks = !!renderBlank;
  const rendered = useMemo(() => renderPrompt(format, text, { inline, blanks }), [format, text, inline, blanks]);
  const hostRef = useRef<HTMLElement | null>(null);
  const [blankNodes, setBlankNodes] = useState<HTMLElement[]>([]);
  const linksRef = useRef(links);
  linksRef.current = links;
  const blankCountRef = useRef(onBlankCount);
  blankCountRef.current = onBlankCount;
  const allowedKey = links ? links.allowed.join(" ") : null;

  useLayoutEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    // blanks: inputs go into the placeholders through portals
    const nodes = blanks ? [...el.querySelectorAll<HTMLElement>("[data-fp-blank]")] : [];
    setBlankNodes(nodes);
    blankCountRef.current?.(nodes.length);
    // links
    if (allowedKey !== null) {
      const allowed = allowedKey ? allowedKey.split(" ") : [];
      el.querySelectorAll("a").forEach((a) => {
        const href = a.getAttribute("href") ?? "";
        const host = href ? hostOf(href) : null;
        if (host && hostAllowed(host, allowed)) {
          a.setAttribute("data-fp-site", "1");
          return;
        }
        a.removeAttribute("href");
        a.removeAttribute("target");
        a.classList.add("fp-md-link-off");
        a.setAttribute("title", BLOCKED_LINK_TITLE);
      });
    }
    // pictures
    let alive = true;
    el.querySelectorAll<HTMLImageElement>("img[data-fp-asset]").forEach((img) => {
      const name = img.getAttribute("data-fp-asset") ?? "";
      if (!assets || !name) {
        img.replaceWith(missingPicture(img));
        return;
      }
      resolveAsset(assets, name).then(
        (url) => {
          if (alive) img.setAttribute("src", url);
        },
        () => {
          if (!alive) return;
          img.replaceWith(missingPicture(img));
        },
      );
    });
    return () => {
      alive = false;
    };
    // assets: by scope (a new object each render is the same source)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rendered.html, blanks, allowedKey, assets?.scope, assets?.inline]);

  const onClick = (e: ReactMouseEvent<HTMLElement>) => {
    const l = linksRef.current;
    if (!l) return; // teacher views: links open in a new tab
    const a = (e.target as Element | null)?.closest?.("a");
    if (!a) return;
    e.preventDefault();
    const href = a.getAttribute("href");
    if (href && a.getAttribute("data-fp-site")) l.onOpen?.(href);
  };

  const setRef = (node: HTMLElement | null) => {
    hostRef.current = node;
    if (typeof ref === "function") ref(node);
    else if (ref) ref.current = node;
  };
  const cls = `fp-md${inline ? " inline" : ""}${format === "html" ? " html" : ""}${className ? ` ${className}` : ""}`;
  const Tag = inline ? "span" : "div";
  return (
    <>
      <Tag
        ref={setRef}
        id={id}
        className={cls}
        tabIndex={tabIndex}
        data-testid={testId}
        onClick={onClick}
        dangerouslySetInnerHTML={{ __html: rendered.html }}
      />
      {renderBlank && blankNodes.map((n, i) => createPortal(renderBlank(i), n, `blank-${i}`))}
    </>
  );
});
