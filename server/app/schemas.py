from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

QID_RE = re.compile(r"^q_[A-Za-z0-9_-]{1,40}$")
# slice 3, tightened by the 2026-10-12 audit (#10): an allowed-website entry is an EXACT
# host ("canvas.dku.edu"), "*.host" for the host and all its subdomains, optionally
# followed by a path prefix ("docs.google.com/forms/d/e/1FAIpQL/").
DOMAIN_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$")
MAX_ALLOWED_DOMAINS = 30
MAX_EXTERNAL_URL = 2000
MAX_FINISH_CONTAINS = 200
MAX_FINISH_REGEX = 300


def normalize_domain(v: str) -> str:
    """Normalize/validate one allowed-website entry ([*.]host[/path]); raises ValueError.
    The host is lowercased, the path keeps its case (form ids are case-sensitive)."""
    raw = v.strip()
    s = raw
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", s):
        s = s.split("://", 1)[1]
    s = s.split("?", 1)[0].split("#", 1)[0]
    host, slash, path = s.partition("/")
    wildcard = host.startswith("*.")
    if wildcard:
        host = host[2:]
    host = host.lower().rstrip(".")
    if ":" in host:
        host = host.split(":", 1)[0]
    if not host:
        raise ValueError("allowed website must not be empty")
    if len(host) > 253 or not DOMAIN_RE.match(host) or ("." not in host and host != "localhost"):
        raise ValueError(f"'{raw}' is not a website name like canvas.dku.edu")
    path = ("/" + path) if slash else ""
    if path == "/":
        path = ""
    if path and (len(path) > 500 or not PATH_RE.match(path)):
        raise ValueError(f"'{raw}' has an unusable path")
    return ("*." if wildcard else "") + host + path


# 2026-10-23 (exam tools): allowed websites of a "questions" exam. Not the external-exam
# allowed_domains (no paths here).
# 2026-10-23 (review): the apps match an entry exactly: "docs.python.org" is that host (and
# the same host with "www." added or removed); "*.python.org" is python.org and all its
# subdomains. A wildcard on a public suffix or shared hosting, where anyone can get a
# subdomain ("*.com", "*.github.io", "*.co.uk"), is refused. The same list as the apps'
# AllowedSites (Mac / iPad) and web/src/lib/websites.ts; 2026-10-23 (review 2): the server
# also checks the full Public Suffix List (is_shared_suffix), so it refuses more than they do.
MAX_WEBSITES = 20
CALCULATOR_MODES = ("off", "scientific", "graphing")
WILDCARD_PREFIX = "*."
SHARED_SUFFIXES = frozenset({
    "com", "net", "org", "edu", "gov", "mil", "int", "info", "biz", "io", "app", "dev", "page",
    "site", "online", "xyz", "me", "co", "ai", "cn", "uk", "us", "eu", "de", "ru", "uz", "jp",
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.cn", "net.cn", "org.cn", "edu.cn", "gov.cn", "ac.cn",
    "github.io", "githubusercontent.com", "github.com", "gitlab.io", "gitlab.com", "bitbucket.io",
    "vercel.app", "now.sh", "netlify.app", "netlify.com", "pages.dev", "workers.dev",
    "web.app", "firebaseapp.com", "herokuapp.com", "blogspot.com", "wordpress.com",
    "glitch.me", "replit.app", "replit.dev", "repl.co", "onrender.com", "fly.dev",
    "azurewebsites.net", "azurestaticapps.net", "cloudfront.net", "amazonaws.com",
    "appspot.com", "wixsite.com", "weebly.com", "notion.site", "carrd.co", "surge.sh",
    "neocities.org", "codepen.io", "jsfiddle.net", "stackblitz.io", "codesandbox.io",
    "ngrok.io", "ngrok-free.app", "trycloudflare.com", "google.com", "googleusercontent.com",
    "sites.google.com", "docs.google.com", "translate.goog", "s3.amazonaws.com",
})
# second-level labels under a two-letter country code that are public suffixes too (com.au)
COUNTRY_SECOND_LEVELS = frozenset({
    "com", "net", "org", "edu", "gov", "mil", "ac", "co", "go", "or", "ne", "gob", "nic", "sch", "gv",
})
# 2026-10-23 (review 2): the Public Suffix List, ICANN and private sections (shared hosting:
# *.pythonanywhere.com, *.r2.dev, *.hf.space, *.web.core.windows.net ...). The package
# bundles a snapshot: nothing is fetched at run time. SHARED_SUFFIXES above stays as an extra
# list for hosts the PSL leaves out (google.com: sites / docs / translate pages of anyone).
# "blogspot" names Blogger's country domains (blogspot.co.uk ...), no longer all on the PSL.
SHARED_FIRST_LABELS = frozenset({"blogspot"})
_PSL = None


def _psl():
    global _PSL
    if _PSL is None:
        from publicsuffixlist import PublicSuffixList
        _PSL = PublicSuffixList()     # only_icann=False: the private section counts too
    return _PSL


def is_public_suffix(host: str) -> bool:
    """True when the PSL lists `host` itself as a public suffix (com, co.uk, github.io)."""
    psl = _psl()
    return psl.publicsuffix(host) == host or psl.privatesuffix(host) is None


def is_shared_suffix(host: str) -> bool:
    """A wildcard base nobody may use: a public suffix (PSL, private section included), on
    SHARED_SUFFIXES, a single label, a country-code second level ("com.au", "edu.uz"), or a
    Blogger domain (blogspot.co.uk)."""
    if host in SHARED_SUFFIXES:
        return True
    labels = host.split(".")
    if len(labels) < 2:
        return True
    if len(labels) == 2 and len(labels[1]) == 2 and labels[0] in COUNTRY_SECOND_LEVELS:
        return True
    if is_public_suffix(host):
        return True
    private = _psl().privatesuffix(host) or host
    return private.split(".", 1)[0] in SHARED_FIRST_LABELS


def to_ascii_host(host: str, raw: str) -> str:
    """2026-10-23 (review 2): an international name is stored as punycode ("пример.рф" ->
    "xn--e1afmkfd.xn--p1ai"), the form the apps and browsers compare."""
    if host.isascii():
        return host
    import idna
    try:
        return idna.encode(host, uts46=True, transitional=False).decode("ascii")
    except idna.IDNAError:
        raise ValueError(f"'{raw}' is not a website name like docs.python.org") from None


def _is_ip(host: str) -> bool:
    import ipaddress
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        pass
    # 2026-10-23 (review 2): no top-level domain is a number, so "10.1", "0x7f.1" and
    # "127.0.0.1" are all addresses, not names
    return bool(re.fullmatch(r"[0-9]+", host.rsplit(".", 1)[-1]))


def normalize_website(v: str) -> str:
    """One allowed website ("https://docs.python.org/3/" is refused, "HTTPS://Docs.Python.org/"
    -> "docs.python.org", "*.Python.org" -> "*.python.org"). A scheme, a trailing slash,
    "www." stays as typed; a real path, a port, a "*" anywhere but one leading "*.", a
    wildcard on a shared suffix, a host that is itself a public suffix ("github.io"), an IP,
    a single label or odd characters is refused (ValueError). International names are
    stored as punycode."""
    raw = v.strip()
    s = raw
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", s):
        if not s.lower().startswith(("http://", "https://")):
            raise ValueError(f"'{raw}' is not a website")
        s = s.split("://", 1)[1]
    host, _, rest = s.partition("/")
    if rest.strip("/") or "?" in host or "#" in host:
        raise ValueError(f"'{raw}': use only the site name, like docs.python.org")
    host = host.lower().rstrip(".")
    wildcard = host.startswith(WILDCARD_PREFIX)
    if wildcard:
        host = host[len(WILDCARD_PREFIX):]
    if ":" in host or "@" in host or "*" in host or "[" in host:
        raise ValueError(f"'{raw}': use only the site name, like docs.python.org or *.python.org")
    host = to_ascii_host(host, raw)
    if not host or len(host) > 253 or not DOMAIN_RE.match(host) or "." not in host or _is_ip(host):
        raise ValueError(f"'{raw}' is not a website name like docs.python.org")
    if wildcard and is_shared_suffix(host):
        raise ValueError(f"'{raw}': anyone can make a site under {host}; name the site itself")
    if not wildcard and is_public_suffix(host):
        raise ValueError(f"'{raw}': {host} is shared by many sites; name the site itself")
    return (WILDCARD_PREFIX if wildcard else "") + host


def normalize_websites(v: list[str] | None) -> list[str]:
    out: list[str] = []
    for d in v or []:
        if not isinstance(d, str) or not d.strip():
            continue
        h = normalize_website(d)
        if h not in out:
            out.append(h)
    if len(out) > MAX_WEBSITES:
        raise ValueError(f"at most {MAX_WEBSITES} websites")
    return out


# 2026-10-24 (website links): an allowed website may be given as a full link
# ("https://duke.app.box.com/folder/0"), optionally after a title ("Course folder
# https://duke.app.box.com/folder/0"). The HOST goes on the allow-list as a plain entry
# (every page of that host, any path: a Box folder link names a folder id, not a place in a
# tree), and the link is kept as the page the exam's websites tab opens first.
MAX_WEBSITE_LINKS = 20
MAX_LINK_CHARS = 2000
MAX_LINK_TITLE = 80
LINK_PATH_RE = re.compile(r"^[A-Za-z0-9._~!$&'()*+,;=:@%/?#\[\]-]*$")


def _link_title(raw: str) -> str | None:
    t = "".join(c for c in raw if c.isprintable()).strip()
    t = re.sub(r"\s+", " ", t)[:MAX_LINK_TITLE].strip()
    return t or None


def parse_website_entry(v: str) -> tuple[str, dict | None]:
    """One allowed-website line -> (allow-list entry, link or None). A plain site name keeps
    the rules of normalize_website. A full http(s) link with a path, query or a title gives
    the link {"host", "url", "title"}: url = "https://" + host + the rest as typed (http is
    upgraded: the exam pane only loads https); the host is the plain entry. A link on a
    wildcard, with a port, a user name, spaces or odd characters is refused (ValueError)."""
    raw = (v or "").strip()
    title = None
    s = raw
    parts = raw.rsplit(None, 1)
    if len(parts) == 2 and re.match(r"^https?://", parts[1], re.I):
        title, s = _link_title(parts[0]), parts[1]
    if not re.match(r"^https?://", s, re.I):
        if title is not None or re.search(r"\s", s):
            raise ValueError(f"'{raw}' is not a website name or a link like https://docs.python.org/3/")
        return normalize_website(s), None
    if len(s) > MAX_LINK_CHARS:
        raise ValueError(f"'{raw[:80]}...' is too long (at most {MAX_LINK_CHARS} characters)")
    rest = s.split("://", 1)[1]
    cut = min([i for i in (rest.find("/"), rest.find("?"), rest.find("#")) if i >= 0], default=len(rest))
    authority, tail = rest[:cut], rest[cut:]
    if authority.startswith(WILDCARD_PREFIX):
        raise ValueError(f"'{raw}': a link can't have a *. wildcard; use the site name *.example.com instead")
    if "@" in authority or ":" in authority:
        raise ValueError(f"'{raw}': use a link without a port or a user name")
    host = normalize_website(authority)
    if tail in ("", "/") and title is None:
        return host, None                      # just the site: no start page to keep
    if tail and not tail.startswith("/"):
        tail = "/" + tail
    if not LINK_PATH_RE.match(tail or ""):
        raise ValueError(f"'{raw}' has characters a link can't have (spaces or quotes)")
    return host, {"host": host, "url": f"https://{host}{tail or '/'}", "title": title}


def split_websites(v: list[str] | None) -> tuple[list[str], list[dict]]:
    """The allowed-website lines -> (hosts for the allow-list, links). Several links may share
    a host (two Box folders); the host is listed once."""
    hosts: list[str] = []
    links: list[dict] = []
    for d in v or []:
        if not isinstance(d, str) or not d.strip():
            continue
        h, link = parse_website_entry(d)
        if h not in hosts:
            hosts.append(h)
        if link is not None and all(x["url"] != link["url"] for x in links):
            links.append(link)
    if len(hosts) > MAX_WEBSITES:
        raise ValueError(f"at most {MAX_WEBSITES} websites")
    if len(links) > MAX_WEBSITE_LINKS:
        raise ValueError(f"at most {MAX_WEBSITE_LINKS} website links")
    return hosts, links


def clean_website_links(v, hosts: list[str]) -> list[dict]:
    """Stored / received links, kept only when well formed and their host is on `hosts`."""
    out: list[dict] = []
    for x in v or []:
        if not isinstance(x, dict) or not isinstance(x.get("url"), str):
            continue
        try:
            h, link = parse_website_entry(x["url"])
        except ValueError:
            continue
        if h not in hosts:
            continue
        if link is None:
            link = {"host": h, "url": f"https://{h}/", "title": None}
        t = x.get("title")
        link["title"] = _link_title(t) if isinstance(t, str) else link["title"]
        if all(y["url"] != link["url"] for y in out):
            out.append(link)
        if len(out) >= MAX_WEBSITE_LINKS:
            break
    return out


def external_host(url: str | None) -> str | None:
    """Lowercase hostname of an http(s) URL, or None."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    return host.lower()


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


MAX_ANSWER_CHARS = 50_000


def _whole(x: float) -> int | float:
    return int(x) if float(x).is_integer() else x


class RubricItemIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    label: str = Field(min_length=1, max_length=500)
    points: float = Field(ge=0, le=1000)

    @field_validator("label")
    @classmethod
    def _label(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("rubric label must not be empty")
        return v

    @field_validator("points")
    @classmethod
    def _points(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("rubric points must be finite")
        return v


# ---------------------------------------------------------------- phase 4: coding questions
# A "code" question: the student writes a program that reads stdin and prints to stdout.
# Tests are {input, expected, visible, points}. Hidden tests never reach students (see
# services.student_question). The server never runs code: runs happen in the browser
# (students: visible tests; teachers: all tests on the grading page).
CODE_LANGUAGES = ("python", "javascript", "c", "cpp", "java")
CodeLanguage = Literal["python", "javascript", "c", "cpp", "java"]
MAX_CODE_TESTS = 50
MAX_TEST_TEXT = 20_000
MAX_STARTER_CODE = 20_000
DEFAULT_TIME_LIMIT_MS = 2000
CODE_FIELDS = ("language", "starter_code", "tests", "write_only", "time_limit_ms")


class CodeTestIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    input: str = Field(default="", max_length=MAX_TEST_TEXT)
    expected: str = Field(default="", max_length=MAX_TEST_TEXT)
    visible: bool = False
    points: int = Field(default=0, ge=0, le=1000)


# ---------------------------------------------------------------- slice 6: images + topics
MAX_QUESTION_IMAGES = 6
MAX_TOPICS = 5
MAX_TOPIC_CHARS = 60
UPLOAD_URL_RE = re.compile(r"^/api/uploads/[A-Za-z0-9_-]{16,64}$")
TOPIC_REQUIRED_DETAIL = "Each question needs at least one topic"


class ImageIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    url: str = Field(min_length=1, max_length=2000)
    alt: str = Field(default="", max_length=300)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip()
        if UPLOAD_URL_RE.match(v):
            return v
        parts = urlsplit(v) if v.lower().startswith("https://") else None
        if parts is None or not parts.hostname or any(c.isspace() for c in v):
            raise ValueError("image url must be an uploaded image (/api/uploads/...) or an https:// address")
        return v

    @field_validator("alt")
    @classmethod
    def _alt(cls, v: str) -> str:
        return v.strip()


def normalize_topics(v: list | None) -> list[str]:
    """Trim, lower-case, drop blanks, case-insensitive dedupe; 1-60 chars each, max 5."""
    out: list[str] = []
    for t in v or []:
        if not isinstance(t, str):
            raise ValueError("topics must be text")
        t = " ".join(t.split()).lower()
        if not t:
            continue
        if len(t) > MAX_TOPIC_CHARS:
            raise ValueError(f"a topic can have at most {MAX_TOPIC_CHARS} characters")
        if t not in out:
            out.append(t)
    if len(out) > MAX_TOPICS:
        raise ValueError(f"a question can have at most {MAX_TOPICS} topics")
    return out


class QuestionIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    # slice 6: pictures shown above the prompt, and topic tags (required on POST/PUT exam,
    # checked in ExamIn so stored exams from before slice 6 stay valid)
    images: list[ImageIn] = Field(default_factory=list, max_length=MAX_QUESTION_IMAGES)
    topics: list[str] = Field(default_factory=list, max_length=50)
    type: Literal["mcq", "numeric", "short", "essay", "code"]
    prompt: str = Field(min_length=1, max_length=10_000)
    points: int = Field(default=1, ge=1, le=1000)
    options: list[str] | None = None
    correct_index: int | None = None
    answer_number: float | None = None
    tolerance: float | None = None
    accepted_answers: list[str] | None = None
    rubric: list[RubricItemIn] | None = Field(default=None, max_length=50)
    min_words: int | None = Field(default=None, ge=1, le=100_000)
    max_words: int | None = Field(default=None, ge=1, le=100_000)
    # slice 6 (Scan & Grade): lines of answer space on the printed paper (null = default)
    answer_lines: int | None = Field(default=None, ge=1, le=40)
    # phase 4 (type "code"): language None = the exam's default_language
    language: CodeLanguage | None = None
    starter_code: str = Field(default="", max_length=MAX_STARTER_CODE)
    tests: list[CodeTestIn] = Field(default_factory=list, max_length=MAX_CODE_TESTS)
    write_only: bool = False
    time_limit_ms: int = Field(default=DEFAULT_TIME_LIMIT_MS, ge=100, le=10_000)

    @field_validator("prompt")
    @classmethod
    def _prompt(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("prompt must not be empty")
        return v

    @field_validator("topics")
    @classmethod
    def _topics(cls, v: list[str]) -> list[str]:
        return normalize_topics(v)

    @model_validator(mode="after")
    def _by_type(self) -> "QuestionIn":
        if self.type != "code":
            self.language, self.starter_code, self.tests = None, "", []
            self.write_only, self.time_limit_ms = False, DEFAULT_TIME_LIMIT_MS
        else:
            # phase 4: graded by the teacher (tests run in the teacher's browser). Test points,
            # when given, must add up to the question's points.
            test_sum = sum(t.points for t in self.tests)
            if test_sum and test_sum != self.points:
                raise ValueError("test points must add up to the question points")
            self.options, self.correct_index, self.accepted_answers = [], None, []
            self.answer_number = self.tolerance = None
            self.rubric, self.min_words, self.max_words = [], None, None
            return self
        if self.type == "essay":
            rubric = self.rubric or []
            if rubric and abs(sum(r.points for r in rubric) - self.points) > 1e-9:
                raise ValueError("rubric points must sum to the question points")
            if (self.min_words is not None and self.max_words is not None
                    and self.min_words > self.max_words):
                raise ValueError("min_words must be <= max_words")
            self.rubric = rubric
            self.options, self.correct_index, self.accepted_answers = [], None, []
            self.answer_number = self.tolerance = None
            return self
        # rubric / word limits only apply to essays
        self.rubric, self.min_words, self.max_words = [], None, None
        if self.type == "mcq":
            opts = self.options or []
            if not 2 <= len(opts) <= 8:
                raise ValueError("mcq needs 2-8 options")
            if any(not isinstance(o, str) or not o.strip() for o in opts):
                raise ValueError("mcq options must be non-empty strings")
            if self.correct_index is None or not 0 <= self.correct_index < len(opts):
                raise ValueError("mcq correct_index must point at an option")
            self.answer_number = self.tolerance = None
            self.accepted_answers = []
        elif self.type == "numeric":
            if self.answer_number is None or not math.isfinite(self.answer_number):
                raise ValueError("numeric question needs answer_number")
            tol = 0.0 if self.tolerance is None else self.tolerance
            if not math.isfinite(tol) or tol < 0:
                raise ValueError("tolerance must be >= 0")
            self.tolerance = tol
            self.options, self.correct_index, self.accepted_answers = [], None, []
        else:  # short
            acc = [a for a in (self.accepted_answers or []) if isinstance(a, str) and a.strip()]
            if not acc:
                raise ValueError("short question needs at least one accepted answer")
            self.accepted_answers = acc
            self.options, self.correct_index = [], None
            self.answer_number = self.tolerance = None
        return self

    def to_dict(self) -> dict:
        exclude = {"id", "rubric"} | (set() if self.type == "code" else set(CODE_FIELDS))
        d = self.model_dump(exclude=exclude)
        d["rubric"] = [{"label": r.label, "points": _whole(r.points)} for r in self.rubric or []]
        return d


class CanvasIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    quiz_type: Literal["classic", "new", "assignment"] | None = None
    access_code_source: Literal["managed", "manual"] = "managed"
    access_code: str | None = Field(default=None, max_length=64)
    removed_hosts: list[str] = Field(default_factory=list, max_length=MAX_ALLOWED_DOMAINS)
    open_book: bool = False

    @field_validator("removed_hosts")
    @classmethod
    def _removed(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(x.strip().lower() for x in v if x and x.strip()))

    @field_validator("access_code")
    @classmethod
    def _code(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if len(v) < 4 or not v.isprintable():
            raise ValueError("access_code must be 4 to 64 printable characters")
        return v


class ExamIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=200)
    duration_min: int = Field(ge=1, le=1440)
    # slice 3: may be [] for external exams; "questions" mode still needs >= 1 (checked below)
    questions: list[QuestionIn] = Field(default_factory=list, max_length=500)
    # slice 2, all optional (on PUT an omitted field keeps the stored value)
    course: str = Field(default="", max_length=64)
    room: str = Field(default="", max_length=64)
    scheduled_at: datetime | None = None
    # 2026-10-23: optional automatic close (FairPlay exams; external ones take it from
    # their source). None = closes only when the teacher closes it.
    closes_at: datetime | None = None
    shuffle_questions: bool = False
    # slice 3: external-website exams
    mode: Literal["questions", "external"] = "questions"
    external_url: str | None = Field(default=None, max_length=MAX_EXTERNAL_URL)
    allowed_domains: list[str] = Field(default_factory=list, max_length=MAX_ALLOWED_DOMAINS)
    finish_url_contains: str | None = Field(default=None, max_length=MAX_FINISH_CONTAINS)
    finish_url_regex: str | None = Field(default=None, max_length=MAX_FINISH_REGEX)
    external_kind: Literal["canvas"] | None = None
    canvas: CanvasIn | None = None
    # slice 4: points for an external exam (the teacher enters each student's score)
    external_points: float = Field(default=100, gt=0, le=10000)
    # slice 6: how the exam is delivered. Omitted -> derived from mode. "website" <=> mode
    # "external"; sending only delivery sets the mode, sending both must agree (422).
    delivery: Literal["computer", "paper", "website"] | None = None
    # Audit S-03: the class that takes this exam (one of the teacher's classes). Required
    # before the exam can be opened, unless it is linked to an LMS course.
    class_id: int | None = Field(default=None, ge=1)
    # 2026-10-14: apps allowed during the exam (allowed_apps.CATALOG keys); [] = none.
    allowed_apps: list[str] = Field(default_factory=list, max_length=20)
    # S3 (2026-10-20): coming back after FairPlay closed needs the teacher's OK
    resume_needs_approval: bool = True
    # S6: require the strongest lock (beats must say sep=1, else weak_lock is flagged)
    require_strong_lock: bool = False
    # 2026-10-23 (exam tools): calculator, open book (student uploads), allowed websites.
    # allowed_apps above is still accepted from older editors but no longer stored.
    calculator: Literal["off", "scientific", "graphing"] = "off"
    open_book: bool = False
    # 2026-10-24: each line a site name or a full link (parse_website_entry): `websites`
    # keeps the hosts (the allow-list, as before), `website_links` the links (start pages).
    # website_links is always derived from websites (anything sent in it is ignored).
    websites: list[str] = Field(default_factory=list, max_length=100)
    website_links: list[dict] = Field(default_factory=list)
    # 2026-10-24: open book: students may upload their files this many days before the exam's
    # date (0 = only on "Before you start" once it is open). Needs scheduled_at to mean anything.
    upload_opens_days: int = Field(default=0, ge=0, le=7)
    # phase 4: language of coding questions that do not pick their own
    default_language: CodeLanguage = "python"

    @field_validator("websites")
    @classmethod
    def _websites(cls, v: list[str]) -> list[str]:
        # hosts and links are split in _links below; here only the shape is checked
        split_websites(v)
        return [x for x in v if isinstance(x, str) and x.strip()]

    @model_validator(mode="after")
    def _links(self) -> "ExamIn":
        # only when sent: setting a field marks it as sent (a PUT without websites keeps them)
        if "websites" in self.model_fields_set:
            self.websites, self.website_links = split_websites(self.websites)
        return self

    @field_validator("allowed_apps")
    @classmethod
    def _apps(cls, v: list[str]) -> list[str]:
        from .allowed_apps import normalize
        return normalize(v)

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title must not be empty")
        return v

    @field_validator("course", "room")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("scheduled_at", "closes_at")
    @classmethod
    def _naive_utc(cls, v: datetime | None, info) -> datetime | None:
        # Audit S-17: a time without a zone ("2026-10-12T09:00") is ambiguous -- a teacher in
        # China meant 09:00 there, the server would store 09:00 UTC (8 hours off). Refuse it.
        if v is not None and v.tzinfo is None:
            raise ValueError(f"{info.field_name} needs a time zone, e.g. 2026-10-12T09:00:00+08:00 or ...Z")
        if v is not None:
            v = v.astimezone(timezone.utc).replace(tzinfo=None)
        return v

    @model_validator(mode="after")
    def _close_after_start(self) -> "ExamIn":
        # 2026-10-23: an automatic close before the planned start would close it at once
        if self.closes_at is not None and self.scheduled_at is not None and self.closes_at <= self.scheduled_at:
            raise ValueError("closes_at must be after scheduled_at (the automatic close comes after the start)")
        return self

    @field_validator("allowed_domains")
    @classmethod
    def _domains(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for d in v:
            if not isinstance(d, str) or not d.strip():
                continue  # blank lines from a textarea are ignored
            d = normalize_domain(d)
            if d not in out:
                out.append(d)
        return out

    @model_validator(mode="after")
    def _by_mode(self) -> "ExamIn":
        # slice 6: delivery and mode must agree
        if self.delivery is None:
            self.delivery = "website" if self.mode == "external" else "computer"
        elif "mode" not in self.model_fields_set:
            self.mode = "external" if self.delivery == "website" else "questions"
        elif (self.delivery == "website") != (self.mode == "external"):
            raise ValueError("delivery \"website\" goes with mode \"external\" (and only with it)")
        # 2026-10-13: topics are optional (a question without one gets the course, or
        # "general", when saved -- see routes_teacher._apply).
        if self.mode == "external":
            url = (self.external_url or "").strip()
            if not url:
                raise ValueError("external_url is required for an exam on another website")
            if external_host(url) is None:
                raise ValueError("external_url must be a full http:// or https:// address")
            if self.questions:
                raise ValueError("an exam on another website cannot have questions")
            self.external_url = url
            fin = (self.finish_url_contains or "").strip()
            self.finish_url_contains = fin or None
            self._canvas_checks(url)
            # 2026-10-23: the exam tools live in FairPlay's own exam page, not on the website
            self.calculator, self.open_book, self.websites = "off", False, []
            self.website_links, self.upload_opens_days = [], 0
        else:
            if not self.questions:
                raise ValueError("add at least one question")
            if self.delivery == "paper" and any(q.type == "code" for q in self.questions):
                raise ValueError("coding questions need an exam on computers, not on paper")
            if self.external_kind is not None or self.canvas is not None:
                raise ValueError("external_kind and canvas are only for an exam on another website")
            self.external_url = None
            self.allowed_domains = []
            self.finish_url_contains = None
            self.finish_url_regex = None
        return self

    def _canvas_checks(self, url: str) -> None:
        from . import canvas_mode as cm
        rx = (self.finish_url_regex or "").strip()
        self.finish_url_regex = cm.validate_regex(rx) if rx else None
        if self.external_kind != cm.KIND:
            if self.canvas is not None:
                raise ValueError("canvas settings need external_kind \"canvas\"")
            return
        if not cm.is_https(url):
            raise ValueError("a Canvas exam link must start with https://")
        if not cm.clean_url(url):
            raise ValueError(cm.UNCLEAN_URL_MESSAGE)
        if cm.scope_of(url) is None:
            raise ValueError(cm.NO_SCOPE_MESSAGE)
        if self.canvas is None:
            return
        c = self.canvas
        c.quiz_type = c.quiz_type or cm.infer_quiz_type(url)
        if c.quiz_type is None:
            raise ValueError("canvas.quiz_type is needed: the link is not a Canvas quiz or assignment link")


AnswerMap = dict[str, Any]


class ClientProofIn(BaseModel):
    """Audit #1: proof that the FairPlay app (not a browser) starts the attempt.
    signature = hex HMAC-SHA256(app key, "start|<nonce>|<device_id>"); the nonce comes
    from GET /api/student/exams/{id}/challenge and is single-use."""
    model_config = ConfigDict(extra="ignore")
    device_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    nonce: str = Field(min_length=8, max_length=128)
    signature: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    version: str = Field(default="", max_length=32)
    build: str = Field(default="", max_length=16)


class StartIn(BaseModel):
    start_code: str = Field(default="", max_length=16)
    client: ClientProofIn | None = None
    # slice 6: the student began from the offline package before the server knew; the
    # server accepts up to 30 minutes in the past (older is clamped) and sets the deadline
    # from it. Ignored when resuming an existing attempt.
    offline_started_at: datetime | None = None

    @field_validator("offline_started_at")
    @classmethod
    def _offline_utc(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is not None:
            v = v.astimezone(timezone.utc).replace(tzinfo=None)
        return v


MAX_ANSWER_INT = 10 ** 300
SCALAR_ONLY_DETAIL = "answer values must be numbers, strings or null"
MAX_LIST_ANSWER = 100


def check_structured_answer(val) -> None:
    """Extended answer shapes (app.question_types): a flat list of numbers / strings / null (options, blanks, file
    ids) or {"language": str, "source": str} (code). Raises ValueError otherwise."""
    if isinstance(val, list):
        if len(val) > MAX_LIST_ANSWER:
            raise ValueError("too many items in one answer")
        total = 0
        for x in val:
            if x is None or isinstance(x, bool):
                continue
            if isinstance(x, str):
                total += len(x)
            elif isinstance(x, float):
                if not math.isfinite(x):
                    raise ValueError("answer numbers must be finite")
            elif isinstance(x, int):
                if abs(x) > MAX_ANSWER_INT:
                    raise ValueError("answer number too large")
            else:
                raise ValueError(SCALAR_ONLY_DETAIL)
        if total > MAX_ANSWER_CHARS:
            raise ValueError("answer too long")
        return
    if set(val) - {"language", "source"} or not isinstance(val.get("source", ""), str) \
            or not isinstance(val.get("language", ""), str):
        raise ValueError(SCALAR_ONLY_DETAIL)
    if len(val.get("source") or "") > MAX_ANSWER_CHARS or len(val.get("language") or "") > 32:
        raise ValueError("answer too long")


class AnswersIn(BaseModel):
    answers: AnswerMap = Field(default_factory=dict)
    client_seq: int = Field(default=0, ge=0, le=2 ** 62)   # audit S-14: fits BIGINT

    @field_validator("answers")
    @classmethod
    def _limit(cls, v: AnswerMap) -> AnswerMap:
        if len(v) > 1000:
            raise ValueError("too many answers")
        for k, val in v.items():
            if len(k) > 64:
                raise ValueError("bad question id")
            if isinstance(val, str) and len(val) > MAX_ANSWER_CHARS:
                raise ValueError("answer too long")
            if isinstance(val, (dict, list)):
                # extended question types: structured answers (options, blanks, files, code
                # with its language). Only their shape is checked here; whether the
                # question takes one is checked against the exam (routes_student).
                check_structured_answer(val)
                continue
            # Audit S-04: no inf/NaN, no integers a float can't hold.
            if isinstance(val, float) and not math.isfinite(val):
                raise ValueError("answer numbers must be finite")
            if isinstance(val, int) and not isinstance(val, bool) and abs(val) > MAX_ANSWER_INT:
                raise ValueError("answer number too large")
        return v


class HeartbeatIn(BaseModel):
    current_question: int = Field(default=0, ge=0, le=10_000)
    # Ignored since the audit (#2): lock state only counts from signed app beats.
    locked: bool = False
    # 2026-10-23: the laptop's battery (0..1) and charger, so the invigilator sees a seat
    # about to run flat. Optional: older pages and desktops send nothing.
    battery: float | None = Field(default=None, ge=0, le=1)
    charging: bool | None = None


class AppBeatIn(BaseModel):
    """Sent by the Mac app itself every 5 s (audit #2, #6). signature = hex
    HMAC-SHA256(app key, "beat|<attempt>|<session>|<device_id>|<ts>|<locked 0/1>|<checks>")."""
    model_config = ConfigDict(extra="ignore")
    session: str = Field(min_length=8, max_length=64)
    device_id: str = Field(min_length=8, max_length=64)
    ts: int = Field(ge=0)                       # client ms, strictly increasing per attempt
    locked: bool = False
    checks: str = Field(default="", max_length=200)   # e.g. "vm=0,displays=1,tap=1,input=1"
    signature: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")


class CanvasCodeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    proof: str = Field(default="", pattern=r"^[0-9a-f]{64}$")
    ts: int = Field(default=0, ge=0, le=2 ** 62)


class ExitCodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    # Audit 2026-10-13: the app's nonce; the reply then carries proofs over
    # "exit|<attempt>|<session>|ok|<nonce>" (app_client.exit_message).
    nonce: str | None = Field(default=None, min_length=8, max_length=128,
                              pattern=r"^[A-Za-z0-9_-]+$")


class OfflineKeyIn(BaseModel):
    """The app asks for its attempt's offline exit key. signature = hex
    HMAC-SHA256(app key, "offkey|<attempt>|<session>|<device_id>|<ts>")."""
    model_config = ConfigDict(extra="ignore")
    session: str = Field(min_length=8, max_length=64)
    device_id: str = Field(min_length=8, max_length=64)
    ts: int = Field(ge=0, le=2 ** 62)
    signature: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")


class OfflineExitIn(BaseModel):
    mac_code: str = Field(min_length=1, max_length=20)


class EventIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str = Field(min_length=1, max_length=64)
    detail: str = ""
    client_time: str | None = None


class EventsIn(BaseModel):
    events: list[EventIn] = Field(default_factory=list, max_length=500)


# ---------------------------------------------------------------- slice 2 teacher bodies
MAX_OPEN_TO = 5000


class OpenIn(BaseModel):
    """2026-10-23: POST /api/exams/{id}/open. "all" (default) or the student ids that may
    start; everyone else sees the exam as not open for them yet."""
    model_config = ConfigDict(extra="ignore")
    open_to: Literal["all"] | list[int] = Field(default="all")
    canvas_access_code: str | None = Field(default=None, max_length=64)

    @field_validator("canvas_access_code")
    @classmethod
    def _canvas_code(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if len(v) < 4 or not v.isprintable():
            raise ValueError("canvas_access_code must be 4 to 64 printable characters")
        return v

    @field_validator("open_to")
    @classmethod
    def _limit(cls, v):
        if isinstance(v, list) and len(v) > MAX_OPEN_TO:
            raise ValueError(f"at most {MAX_OPEN_TO} students")
        return v


class OpenToIn(BaseModel):
    """2026-10-23: PUT /api/exams/{id}/open-to. Either the whole selection (open_to) or
    students to add / remove (safe when two people edit at once)."""
    model_config = ConfigDict(extra="ignore")
    open_to: Literal["all"] | list[int] | None = None
    add: list[int] = Field(default_factory=list, max_length=MAX_OPEN_TO)
    remove: list[int] = Field(default_factory=list, max_length=MAX_OPEN_TO)

    @field_validator("open_to")
    @classmethod
    def _limit(cls, v):
        if isinstance(v, list) and len(v) > MAX_OPEN_TO:
            raise ValueError(f"at most {MAX_OPEN_TO} students")
        return v


class ExtendIn(BaseModel):
    minutes: int = Field(ge=1, le=120)


class ReviewIn(BaseModel):
    status: Literal["dismissed", "noted"]
    note: str | None = Field(default=None, max_length=500)


class GradeIn(BaseModel):
    points: float
    comment: str = Field(default="", max_length=5000)
    rubric_scores: list[float] | None = Field(default=None, max_length=50)

    @field_validator("points")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("points must be a finite number")
        return v

    @field_validator("rubric_scores")
    @classmethod
    def _finite_list(cls, v: list[float] | None) -> list[float] | None:
        if v is not None and not all(math.isfinite(x) for x in v):
            raise ValueError("rubric scores must be finite numbers")
        return v


class CommentIn(BaseModel):
    comment: str = Field(default="", max_length=2000)


class ReleaseIn(BaseModel):
    force: bool = False


# ---------------------------------------------------------------- S1: device check
MAX_DEVICE_CHECKS = 40
DEVICE_TEXT = 300


class DeviceCheckItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(min_length=1, max_length=DEVICE_TEXT)
    ok: bool
    message: str = Field(default="", max_length=DEVICE_TEXT)
    # informational rows (VPN, battery) never block
    info: bool = False


class DeviceCheckIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    platform: str = Field(min_length=1, max_length=DEVICE_TEXT)
    app_version: str = Field(default="", max_length=DEVICE_TEXT)
    os_version: str = Field(default="", max_length=DEVICE_TEXT)
    checks: list[DeviceCheckItem] = Field(default_factory=list, max_length=MAX_DEVICE_CHECKS)
    exam_id: str | None = Field(default=None, max_length=DEVICE_TEXT)

    @field_validator("platform")
    @classmethod
    def _platform(cls, v: str) -> str:
        v = v.strip().lower()
        if not v:
            raise ValueError("platform must not be empty")
        return v


# ---------------------------------------------------------------- S4: device swap
class SwapRequestIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    device_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9-]+$")


# ---------------------------------------------------------------- S5: sealed offline submit
class SealedSubmitIn(BaseModel):
    """The app's sealed file, as written at time-up (routes_student.sealed_submit):
    payload = "attempt_id|session|device_id|client_seq|sealed_at|answers_sha256",
    proof = hex HMAC-SHA256(offline key of attempt + session, payload)."""
    model_config = ConfigDict(extra="ignore")
    payload: str = Field(min_length=1, max_length=1000)
    answers_json: str = Field(max_length=7_000_000)
    proof: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
