"""FairPlay's own say over the websites an EXTERNAL source opens inside the lock
(security review 2026-10-24).

A FairPlay teacher picks an exam's allowed websites in FairPlay; for an exam that comes from
another system (DKU OJ) that system's settings used to decide alone, so a compromised or
careless source could open chatgpt.com or web.whatsapp.com during an exam. Every site an
external source asks for now passes this policy first:

- a built-in deny-list (AI chat and assistants, messaging, file sharing and notes). A deny
  entry covers the domain and all its subdomains; an allowed entry is refused when it is a
  denied host, or when it is a wildcard ("*.google.com") that reaches one (gemini.google.com);
- optionally FAIRPLAY_EXTERNAL_SITE_ALLOWLIST (comma-separated site names, the same form as
  the teacher's list: "docs.python.org,*.cppreference.com"): when set, ONLY sites it covers
  may be used.

- 2026-10-24: FAIRPLAY_SITE_POLICY_EXCEPTIONS (comma-separated exact host names; unset =
  DEFAULT_EXCEPTIONS, "duke.app.box.com,duke.box.com,duke.account.box.com"; set it empty for none): hosts the
  deny-list does not refuse. Duke Box is the university's own file storage, which professors
  and students use every day, so a DKU OJ professor may allow it; Box in general (app.box.com,
  other universities' and companies' Box, Dropbox, Google Drive, OneDrive ...) stays refused.
  An exception is exact (no subdomains, no wildcard: "*.box.com" is still refused) and lifts
  only the deny-list: FAIRPLAY_EXTERNAL_SITE_ALLOWLIST, when set, still has to cover it.
  The trade-off: Box opens the student's OWN Box (every file they keep there, files shared
  with them, comments) during the exam -- the professor's choice, like open book. The sites
  Box needs to work (sign-in, scripts, previews) are added by app.site_companions, not here.

Dropped sites are kept with their reason (`websites_blocked: [{"site", "reason"}]`) so the
source's own page can show the professor what FairPlay removed. FairPlay-native exams are
not touched: their teacher chose the sites in FairPlay itself.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("proctora.integrations")

ENV_ALLOWLIST = "FAIRPLAY_EXTERNAL_SITE_ALLOWLIST"
ENV_EXCEPTIONS = "FAIRPLAY_SITE_POLICY_EXCEPTIONS"
DEFAULT_EXCEPTIONS = "duke.app.box.com,duke.box.com,duke.account.box.com"
# 2026-11: duke.account.box.com is in the open-book default list (app.integrations.dkuoj.open_book_defaults), so the
# unset default lets the deny-list pass it (it is never a page of its own, only the Box sign-in chain). An
# administrator who sets the variable (also empty) decides alone.
WILDCARD = "*."

AI = "AI assistant"
MESSAGING = "messaging"
SHARING = "file sharing / notes"
SEARCH = "web search / translation"
CODE = "online code editor / paste site"
NOT_ALLOWED = "not on this FairPlay server's list of sites for external exams"

_AI_SUFFIX = (
    "openai.com",
    "chatgpt.com",
    "oaiusercontent.com",
    "claude.ai",
    "anthropic.com",
    "gemini.google.com",
    "bard.google.com",
    "aistudio.google.com",
    "notebooklm.google.com",
    "copilot.microsoft.com",
    "copilot.cloud.microsoft",
    "bing.com",
    "perplexity.ai",
    "poe.com",
    "deepseek.com",
    "kimi.moonshot.cn",
    "kimi.com",
    "moonshot.cn",
    "doubao.com",
    "tongyi.aliyun.com",
    "qianwen.aliyun.com",
    "yiyan.baidu.com",
    "chatglm.cn",
    "yuanbao.tencent.com",
    "character.ai",
    "you.com",
    "phind.com",
    "mistral.ai",
    "groq.com",
    "x.ai",
    "grok.com",
    "meta.ai",
    "pi.ai",
    "cohere.com",
    "huggingface.co",
    "hf.co",
    "githubcopilot.com",
    "copilot.github.com",
    "blackbox.ai",
    "chatbot.theb.ai",
    "quillbot.com",
    "qwen.ai",
    "tongyi.com",
    "z.ai",
    "metaso.cn",
    "kimi.ai",
    "manus.im",
    "felo.ai",
    "chegg.com",
    "wolframalpha.com",
    "grammarly.com",
)
_SEARCH_SUFFIX = (
    "deepl.com",
    "duckduckgo.com",
    "kagi.com",
    "msn.com",
)
_CODE_SUFFIX = (
    "github.dev",
    "vscode.dev",
    "replit.com",
    "codepen.io",
    "jsfiddle.net",
    "ideone.com",
    "onlinegdb.com",
    "godbolt.org",
    "rentry.co",
    "dpaste.org",
)
_MESSAGING_SUFFIX = (
    "whatsapp.com",
    "whatsapp.net",
    "telegram.org",
    "telegram.me",
    "t.me",
    "wechat.com",
    "weixin.qq.com",
    "wx.qq.com",
    "qq.com",
    "messenger.com",
    "facebook.com",
    "instagram.com",
    "discord.com",
    "discord.gg",
    "discordapp.com",
    "slack.com",
    "teams.microsoft.com",
    "teams.live.com",
    "signal.org",
    "line.me",
    "skype.com",
    "mail.google.com",
    "outlook.live.com",
    "outlook.office.com",
    "chat.google.com",
    "x.com",
    "twitter.com",
    "outlook.office365.com",
    "teams.cloud.microsoft",
    "m365.cloud.microsoft",
    "zoom.us",
    "reddit.com",
)
_SHARING_SUFFIX = (
    "drive.google.com",
    "docs.google.com",
    "sheets.google.com",
    "slides.google.com",
    "keep.google.com",
    "dropbox.com",
    "onedrive.live.com",
    "1drv.ms",
    "sharepoint.com",
    "box.com",
    "notion.so",
    "notion.site",
    "evernote.com",
    "pastebin.com",
    "paste.ee",
    "hastebin.com",
    "gist.github.com",
    "gist.githubusercontent.com",
    "raw.githubusercontent.com",
    "wetransfer.com",
    "mega.nz",
    "pan.baidu.com",
    "weiyun.com",
    "kdocs.cn",
    "docs.qq.com",
    "shimo.im",
    "yuque.com",
    "feishu.cn",
    "larksuite.com",
    "codeshare.io",
    "file.io",
    "gofile.io",
    "catbox.moe",
    "notion.com",
    "icloud.com",
    "proton.me",
    "alipan.com",
    "quark.cn",
)

DENY_SUFFIX: dict[str, str] = {}
for _reason, _items in ((AI, _AI_SUFFIX), (SEARCH, _SEARCH_SUFFIX), (CODE, _CODE_SUFFIX),
                        (MESSAGING, _MESSAGING_SUFFIX), (SHARING, _SHARING_SUFFIX)):
    DENY_SUFFIX.update({d: _reason for d in _items})

_EXACT = (
    "github.com",
    "www.github.com",
    "www.google.com",
    "google.com",
    "translate.google.com",
    "meet.google.com",
    "search.brave.com",
)
DENY_EXACT: dict[str, str] = {d: (AI + " (GitHub Copilot chat)" if "github" in d else SEARCH) for d in _EXACT}


def _host(entry: str) -> tuple[str, bool]:
    e = entry.strip().lower().rstrip(".")
    if e.startswith(WILDCARD):
        return e[len(WILDCARD):], True
    return e, False


def _under(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def exceptions() -> set[str]:
    """FAIRPLAY_SITE_POLICY_EXCEPTIONS as exact host names (unset: DEFAULT_EXCEPTIONS).
    Wildcards and anything that is not a plain host name are ignored."""
    raw = os.environ.get(ENV_EXCEPTIONS)
    if raw is None:
        raw = DEFAULT_EXCEPTIONS
    out = set()
    for x in raw.split(","):
        h = x.strip().lower().rstrip(".")
        if h and "*" not in h and "/" not in h and ":" not in h and "." in h:
            out.add(h)
    return out


def denied(entry: str) -> str | None:
    """The reason a site entry is refused for an external exam, or None."""
    host, wild = _host(entry)
    if not host:
        return None
    if not wild and host in exceptions():
        return None
    for d, why in DENY_EXACT.items():
        if host == d or (wild and _under(d, host)):
            return why
    for d, why in DENY_SUFFIX.items():
        if _under(host, d) or (wild and _under(d, host)):
            return why
    return None


def allowlist() -> list[str] | None:
    """FAIRPLAY_EXTERNAL_SITE_ALLOWLIST as site entries, or None when unset."""
    raw = os.environ.get(ENV_ALLOWLIST, "")
    items = [x.strip().lower().rstrip(".") for x in raw.split(",") if x.strip()]
    return items or None


def _allowed_by(entry: str, allow: list[str]) -> bool:
    host, wild = _host(entry)
    for a in allow:
        ah, awild = _host(a)
        if awild:
            if _under(host, ah):              # "*.x" covers x, its subdomains and "*.sub.x"
                return True
        elif not wild and host == ah:         # a plain entry covers only itself
            return True
    return False


def filter_sites(sites: list[str]) -> tuple[list[str], list[dict]]:
    """(the sites kept, [{"site", "reason"}] for the ones dropped), order kept."""
    allow = allowlist()
    kept, blocked = [], []
    for s in sites:
        why = denied(s)
        if why is None and allow is not None and not _allowed_by(s, allow):
            why = NOT_ALLOWED
        if why is None:
            kept.append(s)
        else:
            blocked.append({"site": s, "reason": why})
    return kept, blocked
