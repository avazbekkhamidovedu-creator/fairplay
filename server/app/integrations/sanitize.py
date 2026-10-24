"""Text from an external source, cleaned on arrival (security review 2026-10-24).

Statements, options, instructions and feedback arrive from DKU OJ as Markdown or HTML and
used to be stored as sent (the web cleans them again with DOMPurify, and keeps doing so).
They are now cleaned on the server with a strict allow-list before they are stored, so
nothing else that reads them (the apps, an export, a future page) can be handed markup
that runs:

- HTML: only a small set of formatting tags and attributes survive; script / style /
  iframe / object / svg ... go with their content; any other tag goes, its text stays;
- links: http, https, mailto, fp-asset: and in-page (#...) only (no javascript:, data: ...);
- images: only `fp-asset:` (the exam's own files) and `data:image/png|jpeg|gif|webp`.
  Any other image (an http(s) URL would load from outside during the exam, and can track
  who opens it) becomes the text "[external image removed]";
- Markdown: the same rules for the raw HTML inside it and for its links and images; code
  (fenced blocks and `inline code`) is left exactly as sent.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser

EXTERNAL_IMAGE = "[external image removed]"

ALLOWED_TAGS = {
    "p", "br", "b", "strong", "i", "em", "u", "s", "del", "ins", "sub", "sup", "small", "mark",
    "code", "pre", "kbd", "samp", "var", "blockquote", "q", "ul", "ol", "li", "dl", "dt", "dd",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "caption", "colgroup", "col", "span", "div", "a", "img", "abbr", "cite", "figure", "figcaption",
    "details", "summary",
}
VOID_TAGS = {"br", "hr", "img", "col"}
# dropped together with everything inside them
DROP_CONTENT = {"script", "style", "iframe", "frame", "frameset", "object", "embed", "applet",
                "noscript", "noembed", "template", "svg", "math", "textarea", "select", "xmp",
                "plaintext", "title", "head", "base", "link", "meta", "form", "audio", "video"}
ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan", "align"}, "th": {"colspan", "rowspan", "align", "scope"},
    "col": {"span"}, "colgroup": {"span"}, "ol": {"start", "type"}, "abbr": {"title"},
    "code": {"class"}, "pre": {"class"}, "span": {"class"}, "div": {"class"},
}
CLASS_RE = re.compile(r"^[A-Za-z0-9_ -]{0,200}$")
NUM_RE = re.compile(r"^[0-9]{1,4}%?$")
DATA_IMAGE_RE = re.compile(r"^data:image/(png|jpeg|jpg|gif|webp);base64,[A-Za-z0-9+/=\s]*$", re.I)
_CTRL_RE = re.compile(r"[\x00-\x20\x7f]+")


def _scheme(url: str) -> str:
    """The lowercase scheme of a URL as a browser reads it (controls and spaces ignored),
    "" for a relative one."""
    u = _CTRL_RE.sub("", html.unescape(url or ""))
    m = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*):", u)
    return m.group(1).lower() if m else ""


def safe_link(url: str) -> bool:
    u = (url or "").strip()
    s = _scheme(u)
    if s in ("http", "https", "mailto", "fp-asset"):
        return True
    return s == "" and not u.startswith("//") and not u.startswith("\\")


def safe_image(url: str) -> bool:
    u = _CTRL_RE.sub("", html.unescape((url or "").strip()))
    if _scheme(u) == "fp-asset":
        return True
    return bool(DATA_IMAGE_RE.match(u))


def _attr_ok(tag: str, name: str, value: str) -> bool:
    if name not in ALLOWED_ATTRS.get(tag, ()):
        return False
    if name == "href":
        return safe_link(value)
    if name == "src":
        return safe_image(value)
    if name == "class":
        return bool(CLASS_RE.match(value))
    if name in ("width", "height", "colspan", "rowspan", "span", "start"):
        return bool(NUM_RE.match(value))
    return True


def _render_tag(tag: str, attrs: list[tuple[str, str | None]], close: bool = False) -> str:
    if close:
        return f"</{tag}>"
    kept = [(k, v or "") for k, v in attrs if _attr_ok(tag, k, v or "")]
    out = "".join(f' {k}="{html.escape(v, quote=True)}"' for k, v in kept)
    return f"<{tag}{out}>"


class _Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self.skip: list[str] = []          # open DROP_CONTENT tags

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self.skip:
            if tag in DROP_CONTENT and tag not in VOID_TAGS:
                self.skip.append(tag)
            return
        if tag in DROP_CONTENT:
            if tag not in ("base", "link", "meta"):
                self.skip.append(tag)
            return
        if tag == "img" and not safe_image(dict(attrs).get("src") or ""):
            self.out.append(html.escape(EXTERNAL_IMAGE))
            return
        if tag in ALLOWED_TAGS:
            self.out.append(_render_tag(tag, attrs))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        tag = tag.lower()
        if self.skip and self.skip[-1] == tag:
            self.skip.pop()

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.skip:
            if tag == self.skip[-1]:
                self.skip.pop()
            return
        if tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))

    def handle_entityref(self, name):
        if not self.skip:
            self.out.append(f"&{name};")

    def handle_charref(self, name):
        if not self.skip:
            self.out.append(f"&#{name};")

    # comments, <!doctype>, <?pi?> and CDATA are dropped
    def handle_comment(self, data):
        pass

    def handle_decl(self, decl):
        pass

    def handle_pi(self, data):
        pass

    def unknown_decl(self, data):
        pass


def clean_html(text: str) -> str:
    """HTML with only the allowed tags, attributes, links and images."""
    if not text or "<" not in text and "&" not in text:
        return text or ""
    p = _Cleaner()
    p.feed(text)
    p.close()
    rest = p.rawdata if hasattr(p, "rawdata") else ""
    out = "".join(p.out)
    if rest and not p.skip:                  # an unfinished "<..." at the end: text
        out += html.escape(rest, quote=False)
    return out


# ---------------------------------------------------------------- Markdown
_CODE_RE = re.compile(r"(^|\n)([ \t]{0,3})(`{3,}|~{3,})[^\n]*\n.*?(\n[ \t]{0,3}\3[`~]*[ \t]*(?=\n|$)|$)"
                      r"|(`+)(?!`).+?(?<!`)\5(?!`)", re.S)
_TAG_RE = re.compile(r"<!--.*?(?:-->|$)|<(/?)([A-Za-z][A-Za-z0-9-]*)((?:\s+[^<>]*?)?)\s*(/?)>", re.S)
_ATTR_RE = re.compile(r"""([A-Za-z_:][-A-Za-z0-9_:.]*)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s"'=<>`]+))?""")
_MD_IMAGE_RE = re.compile(r"!\[((?:[^\]\\]|\\.)*)\]\(\s*(<[^>\n]*>|(?:[^()\s]|\([^()\s]*\))*)(\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)")
_MD_LINK_RE = re.compile(r"(?<!!)\[((?:[^\]\\]|\\.)*)\]\(\s*(<[^>\n]*>|(?:[^()\s]|\([^()\s]*\))*)(\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)")
_MD_REFDEF_RE = re.compile(r"^([ \t]{0,3}\[((?:[^\]\\]|\\.)+)\]:[ \t]*)(<[^>\n]*>|\S+)(.*)$", re.M)
_MD_REFIMG_RE = re.compile(r"!\[((?:[^\]\\]|\\.)*)\]\[((?:[^\]\\]|\\.)*)\]|!\[((?:[^\]\\]|\\.)+)\](?![\[(])")
_MD_AUTOLINK_RE = re.compile(r"<([A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*)>")


def _md_tag(m: re.Match, state: dict) -> str:
    """One raw HTML tag inside Markdown text."""
    whole = m.group(0)
    if whole.startswith("<!--"):
        return ""
    close, tag, attrs, _self = m.group(1), m.group(2).lower(), m.group(3) or "", m.group(4)
    if state["skip"]:
        if close and tag == state["skip"][-1]:
            state["skip"].pop()
        elif not close and tag in DROP_CONTENT:
            state["skip"].append(tag)
        return ""
    if tag in DROP_CONTENT:
        if not close and not _self and tag not in ("base", "link", "meta"):
            state["skip"].append(tag)
        return ""
    if tag not in ALLOWED_TAGS:
        return ""
    if close:
        return "" if tag in VOID_TAGS else f"</{tag}>"
    pairs = []
    for am in _ATTR_RE.finditer(attrs):
        v = am.group(2) or ""
        if v[:1] in ("'", '"'):
            v = v[1:-1]
        pairs.append((am.group(1).lower(), html.unescape(v)))
    if tag == "img" and not safe_image(dict(pairs).get("src") or ""):
        return EXTERNAL_IMAGE
    return _render_tag(tag, pairs)


def _md_text(text: str, state: dict, refs: dict[str, str]) -> str:
    """A stretch of Markdown outside code."""
    def image(m):
        url = m.group(2).strip("<>")
        return m.group(0) if safe_image(url) else EXTERNAL_IMAGE

    def link(m):
        url = m.group(2).strip("<>")
        return m.group(0) if safe_link(url) else m.group(1)

    def refimg(m):
        ref = (m.group(2) or m.group(1) or m.group(3) or "").strip().lower()
        url = refs.get(ref)
        if url is None:
            return m.group(0)               # not an image reference after all
        return m.group(0) if safe_image(url) else EXTERNAL_IMAGE

    def autolink(m):
        return m.group(0) if safe_link(m.group(1)) else html.escape(m.group(1), quote=False)

    text = _MD_IMAGE_RE.sub(image, text)
    text = _MD_REFIMG_RE.sub(refimg, text)
    text = _MD_LINK_RE.sub(link, text)
    text = _MD_AUTOLINK_RE.sub(autolink, text)

    out, pos = [], 0
    for m in _TAG_RE.finditer(text):
        if not state["skip"]:
            out.append(text[pos:m.start()])
        out.append(_md_tag(m, state))
        pos = m.end()
    if not state["skip"]:
        out.append(text[pos:])
    return "".join(out)


def clean_markdown(text: str) -> str:
    """Markdown whose raw HTML, links and images follow the same rules as clean_html;
    fenced code blocks and inline code untouched."""
    if not text:
        return ""
    if not any(c in text for c in "<[!"):
        return text
    refs: dict[str, str] = {}

    def refdef(m):
        url = m.group(3).strip("<>")
        refs[m.group(2).strip().lower()] = url
        return m.group(0) if safe_link(url) else m.group(1) + "#" + m.group(4)
    state: dict = {"skip": []}
    out, pos = [], 0
    for m in _CODE_RE.finditer(text):
        seg = text[pos:m.start()]
        seg = _MD_REFDEF_RE.sub(refdef, seg)
        out.append(_md_text(seg, state, refs))
        if not state["skip"]:
            out.append(m.group(0))
        pos = m.end()
    seg = _MD_REFDEF_RE.sub(refdef, text[pos:])
    out.append(_md_text(seg, state, refs))
    return "".join(out)


def clean(text: str, fmt: str = "markdown") -> str:
    return clean_html(text) if fmt == "html" else clean_markdown(text)
