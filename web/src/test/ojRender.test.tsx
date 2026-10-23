// DKU OJ bridge: rendering of DKU OJ text (markdown + KaTeX, code, tables, DKU OJ HTML),
// sanitizing, fp-asset pictures (asset URL and offline package copies) and links.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OjContent, BLOCKED_LINK_TITLE } from "../components/OjContent";
import { _resetAssetsForTests, attemptAssetContext, forgetAssets, resolveAsset, type AssetContext } from "../lib/ojAssets";
import { assetNames, plainSummary, renderOjHtml, renderOjMarkdown, renderPrompt, sanitizeHtml } from "../lib/ojMarkdown";

const html = (src: string) => renderOjMarkdown(src).html;
const dom = (s: string) => {
  const d = document.createElement("div");
  d.innerHTML = s;
  return d;
};

describe("DKU OJ markdown", () => {
  it("renders paragraphs, emphasis, lists and tables", () => {
    const d = dom(html("Hello **world**\n\n- a\n- b\n\n| n | out |\n|---|-----|\n| 1 | 2 |\n"));
    expect(d.querySelector("strong")?.textContent).toBe("world");
    expect(d.querySelectorAll("li")).toHaveLength(2);
    expect(d.querySelector("table th")?.textContent).toBe("n");
    expect(d.querySelector("table td")?.textContent).toBe("1");
  });

  it("renders ~…~ as inline math and $$…$$ as display math with KaTeX", () => {
    const d = dom(html("Compute ~x^2 + 1~ for every x.\n\n$$\n\\sum_{i=1}^n i\n$$\n"));
    const inline = d.querySelector("p .katex");
    expect(inline).not.toBeNull();
    expect(inline?.closest(".katex-display")).toBeNull();
    expect(d.querySelector(".katex-display")).not.toBeNull();
    // MathML copy carries the TeX source
    expect(d.querySelector("annotation")?.textContent).toBe("x^2 + 1");
    expect(d.textContent).not.toContain("~x^2");
  });

  it("renders $$…$$ inside a sentence and on one line", () => {
    expect(dom(html("Area $$\\pi r^2$$ here")).querySelector(".katex-display")).not.toBeNull();
    expect(dom(html("$$a+b$$")).querySelector(".katex-display")).not.toBeNull();
  });

  it("keeps ~ in code and escaped \\~ as text, and ~~ is not strikethrough", () => {
    const d = dom(html("Use `a ~ b` and \\~5 minutes."));
    expect(d.querySelector("code")?.textContent).toBe("a ~ b");
    expect(d.querySelector(".katex")).toBeNull();
    expect(d.textContent).toContain("~5 minutes");
    expect(dom(html("~~x~~")).querySelector("s, del")).toBeNull();
  });

  it("shows broken TeX as an error, not a crash", () => {
    const d = dom(html("Bad ~\\frac{1}{~ math"));
    expect(d.textContent).toContain("Bad");
  });

  it("highlights fenced code by language", () => {
    const d = dom(html("```python\ndef f(x):\n    return x  # done\n```\n\n```cpp\nint main() { return 0; }\n```\n"));
    const blocks = d.querySelectorAll("pre.fp-md-code code");
    expect(blocks).toHaveLength(2);
    expect(blocks[0]!.className).toContain("language-python");
    expect(blocks[0]!.querySelector(".hljs-keyword")?.textContent).toBe("def");
    expect(blocks[0]!.querySelector(".hljs-comment")).not.toBeNull();
    expect(blocks[1]!.className).toContain("language-cpp");
    // unknown language: escaped plain text
    const u = dom(html("```brainfuck\n<b>x</b>\n```"));
    expect(u.querySelector("pre code b")).toBeNull();
    expect(u.querySelector("pre code")?.textContent).toBe("<b>x</b>\n");
  });

  it("turns ___ into blanks only when asked, not inside words or for __bold__", () => {
    const r = renderOjMarkdown("A ___ maps keys; lookup is O(___). snake_case_name and __bold__", { blanks: true });
    expect(r.blankCount).toBe(2);
    const d = dom(r.html);
    expect(d.querySelectorAll("[data-fp-blank]")).toHaveLength(2);
    expect(d.textContent).toContain("snake_case_name");
    expect(d.querySelector("strong")?.textContent).toBe("bold");
    expect(renderOjMarkdown("A ___ here").blankCount).toBe(0);
  });

  it("finds fp-asset names and makes plain summaries", () => {
    expect(assetNames("![c](fp-asset:cell.png) and ![d](fp-asset:my%20pic.jpg)")).toEqual(["cell.png", "my pic.jpg"]);
    expect(plainSummary("oj_md", "Find **x** where ~x^2 = 4~")).toMatch(/^Find x where/);
    expect(plainSummary("plain", "Same text")).toBe("Same text");
    expect(renderPrompt("plain", "<b>x</b>").html).toBe("&lt;b&gt;x&lt;/b&gt;");
  });
});

/** on* handlers, style, and javascript:/data:text URLs in any attribute. */
function badAttributes(root: Element): string[] {
  const out: string[] = [];
  root.querySelectorAll("*").forEach((el) => {
    for (const at of [...el.attributes]) {
      if (/^on/i.test(at.name) || at.name === "style" || /^\s*(javascript|vbscript|data:text)/i.test(at.value))
        out.push(`${el.tagName}.${at.name}`);
    }
  });
  return out;
}

describe("sanitizing", () => {
  const attacks = [
    "<script>window.__pwned = 1</script>",
    '<img src="x" onerror="window.__pwned = 1">',
    '<iframe src="https://evil.example"></iframe>',
    '<a href="javascript:window.__pwned=1">click</a>',
    '<div style="position:fixed;inset:0">cover</div>',
    '<svg><script>window.__pwned=1</script></svg>',
    '<math><mi xlink:href="javascript:alert(1)">x</mi></math>',
    '<form action="https://evil.example"><input name="p"></form>',
    '<object data="x"></object><embed src="x">',
    '<p onclick="window.__pwned=1">p</p>',
    '<a href="data:text/html,<script>1</script>">d</a>',
    "[md link](javascript:alert(1))",
    "![img](javascript:alert(1))",
  ];
  it.each(attacks)("removes the dangerous part of %s", (src) => {
    for (const out of [renderOjMarkdown(src).html, renderOjHtml(src).html]) {
      const d = dom(out);
      expect(d.querySelector("script, iframe, svg > script, form, input, object, embed")).toBeNull();
      expect(badAttributes(d)).toEqual([]);
    }
  });

  it("keeps harmless HTML DKU OJ uses (sup, br, tables, https links)", () => {
    const d = dom(sanitizeHtml('x<sup>2</sup><br><a href="https://docs.python.org/3/">docs</a>'));
    expect(d.querySelector("sup")).not.toBeNull();
    const a = d.querySelector("a")!;
    expect(a.getAttribute("href")).toBe("https://docs.python.org/3/");
    expect(a.getAttribute("rel")).toBe("noopener noreferrer");
    expect(a.getAttribute("target")).toBe("_blank");
  });

  it("KaTeX runs untrusted: \\href and \\htmlClass make no link or class", () => {
    const d = dom(html("~\\href{javascript:alert(1)}{x}~ and ~\\htmlStyle{color:red}{y}~"));
    expect(d.querySelector("a")).toBeNull();
    // the TeX source is shown as text; nothing links anywhere
    d.querySelectorAll("[href]").forEach((el) => expect(el.getAttribute("href")).not.toMatch(/javascript/i));
  });

  it("math placeholders written by hand never pick up other content", () => {
    const d = dom(html('<span data-fp-math="m1-0"></span> and ~a~'));
    expect(d.querySelectorAll(".katex")).toHaveLength(1);
  });

  it("renders DKU OJ HTML with its marked math", () => {
    const d = dom(renderOjHtml('<p>Let <span class="inline-math">~n \\le 10~</span>.</p><div class="display-math">$$x$$</div>').html);
    expect(d.querySelector("p .katex")).not.toBeNull();
    expect(d.querySelector(".katex-display")).not.toBeNull();
  });

  it("moves fp-asset image sources to data-fp-asset and drops other schemes", () => {
    const d = dom(html("![cell](fp-asset:cell.png) ![remote](http://insecure.example/a.png) ![ok](https://cdn.example/a.png)"));
    const imgs = d.querySelectorAll("img");
    expect(imgs[0]!.getAttribute("data-fp-asset")).toBe("cell.png");
    expect(imgs[0]!.hasAttribute("src")).toBe(false);
    expect(imgs[1]!.hasAttribute("src")).toBe(false);
    expect(imgs[2]!.getAttribute("src")).toBe("https://cdn.example/a.png");
    expect(imgs[2]!.getAttribute("referrerpolicy")).toBe("no-referrer");
    const h = dom(renderOjHtml('<img src="fp-asset:graph.svg" alt="g">').html);
    expect(h.querySelector("img")?.getAttribute("data-fp-asset")).toBe("graph.svg");
  });
});

describe("pictures (fp-asset)", () => {
  const createObjectURL = vi.fn((b: Blob) => `blob:${(b as Blob & { tag?: string }).tag ?? "x"}`);
  beforeEach(() => {
    _resetAssetsForTests();
    Object.defineProperty(URL, "createObjectURL", { value: createObjectURL, configurable: true, writable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true, writable: true });
  });
  afterEach(() => createObjectURL.mockClear());

  const blob = (tag: string) => Object.assign(new Blob(["png"]), { tag });

  it("loads each picture once through the API and shows it", async () => {
    const fetch = vi.fn(async (name: string) => blob(name));
    const ctx: AssetContext = { scope: "a:a_1", fetch };
    render(<OjContent format="oj_md" text={"![cell](fp-asset:cell.png)\n\n![cell again](fp-asset:cell.png)"} assets={ctx} />);
    await waitFor(() => expect(screen.getByAltText("cell")).toHaveAttribute("src", "blob:cell.png"));
    expect(screen.getByAltText("cell again")).toHaveAttribute("src", "blob:cell.png");
    expect(fetch).toHaveBeenCalledTimes(1);
    forgetAssets("a:a_1");
    await resolveAsset(ctx, "cell.png");
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("uses the offline package's copy (data: URL) without the network", async () => {
    const ctx = attemptAssetContext(null, { "cell.png": "data:image/png;base64,AAAA" });
    render(<OjContent format="oj_md" text="![cell](fp-asset:cell.png)" assets={ctx} />);
    await waitFor(() => expect(screen.getByAltText("cell")).toHaveAttribute("src", "data:image/png;base64,AAAA"));
  });

  it("marks a picture that cannot load, and tries again later", async () => {
    const fetch = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValue(blob("ok"));
    const ctx: AssetContext = { scope: "a:a_2", fetch };
    render(<OjContent format="oj_md" text="![cell](fp-asset:cell.png)" assets={ctx} />);
    await waitFor(() => expect(screen.getByAltText("cell")).toHaveClass("fp-md-img-missing"));
    await expect(resolveAsset(ctx, "cell.png")).resolves.toBe("blob:ok");
    // an offline attempt without a copy in the package
    render(<OjContent format="oj_md" text="![other](fp-asset:other.png)" assets={attemptAssetContext(null)} />);
    await waitFor(() => expect(screen.getByAltText("other")).toHaveClass("fp-md-img-missing"));
  });
});

describe("links during the exam", () => {
  const text = "See [the docs](https://docs.python.org/3/library/) or [a forum](https://stackoverflow.com/q/1).";

  it("opens allowed sites through the Websites panel and blocks the rest", () => {
    const onOpen = vi.fn();
    render(<OjContent format="oj_md" text={text} links={{ allowed: ["docs.python.org"], onOpen }} />);
    const docs = screen.getByText("the docs");
    fireEvent.click(docs);
    expect(onOpen).toHaveBeenCalledWith("https://docs.python.org/3/library/");
    const forum = screen.getByText("a forum");
    expect(forum).not.toHaveAttribute("href");
    expect(forum).toHaveAttribute("title", BLOCKED_LINK_TITLE);
    fireEvent.click(forum);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("blocks every link when the exam allows no websites", () => {
    render(<OjContent format="oj_md" text={text} links={{ allowed: [] }} />);
    expect(screen.getByText("the docs")).not.toHaveAttribute("href");
  });

  it("teacher views keep links (new tab, no referrer)", () => {
    render(<OjContent format="oj_md" text={text} />);
    const a = screen.getByText("a forum");
    expect(a).toHaveAttribute("href", "https://stackoverflow.com/q/1");
    expect(a).toHaveAttribute("target", "_blank");
  });
});
