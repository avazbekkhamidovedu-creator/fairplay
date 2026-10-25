"""Table tests of the restricted-browser policy.

1. The expectation tables of mac/Tests/ProctoraTests/CanvasModeTests.swift are read out of that file
   at run time and replayed against the Python port (canvas_nav.py, standin.py).
2. A large look-alike URL list is judged by the port and by an independent oracle written from the
   prose of the contract (regular expressions, no shared code).
"""
from __future__ import annotations

import itertools
import os
import re
from urllib.parse import quote

import canvas_nav as N
import config as C
import standin as S
from lib import check, note, section

SWIFT_TESTS = os.path.join(C.REPO, "mac", "Tests", "ProctoraTests", "CanvasModeTests.swift")
STR = r'"((?:[^"\\]|\\.)*)"'


def swift_unescape(s: str) -> str:
    s = re.sub(r"\\u\{([0-9A-Fa-f]+)\}", lambda m: chr(int(m[1], 16)), s)
    return s.replace('\\"', '"').replace("\\\\", "\\")


def function_bodies(src: str) -> dict[str, str]:
    out = {}
    for m in re.finditer(r"@Test func (\w+)\(\)", src):
        i = src.index("{", m.end())
        depth, j, n = 0, i, len(src)
        while j < n:
            c = src[j]
            if c == '"':
                if src.startswith('"""', j):
                    j = src.index('"""', j + 3) + 3
                    continue
                j += 1
                while src[j] != '"':
                    j += 2 if src[j] == "\\" else 1
            elif c == "/" and src.startswith("//", j):
                j = src.index("\n", j)
                continue
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out[m[1]] = src[i + 1:j]
    return out


def array_after(body: str, header: str) -> list[list[str]]:
    """Every string-array literal that follows `header` (e.g. 'for path in')."""
    res = []
    for m in re.finditer(re.escape(header) + r"\s*\[", body):
        k, depth = m.end(), 1
        start = k
        while depth:
            c = body[k]
            if c == '"':
                k += 1
                while body[k] != '"':
                    k += 2 if body[k] == "\\" else 1
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
            k += 1
        res.append([swift_unescape(x) for x in re.findall(STR, body[start:k - 1])])
    return res


HOST = "canvas.dku.edu"


def sc(kind="quizzes", open_book=False) -> N.Scope:
    return N.Scope("123", kind, "456", open_book)


def d(path, phase, kind="quizzes", open_book=False, host=None):
    return N.decide("https://" + (host or HOST) + path, HOST, sc(kind, open_book), phase)


class Tally:
    def __init__(self):
        self.n = 0
        self.bad: list[str] = []

    def eq(self, got, want, label):
        self.n += 1
        if got != want:
            self.bad.append(f"{label}: got {got!r} want {want!r}")


def swift_tables() -> None:
    section("7a. Swift CanvasModeTests tables replayed against the Python port")
    src = open(SWIFT_TESTS, encoding="utf-8").read()
    bodies = function_bodies(src)
    A, B = N.ALLOW, N.BLOCK
    t = Tally()

    def arrays(name, header="for path in"):
        arr = array_after(bodies[name], header)
        assert arr, name
        return arr

    # lobbyAllowedPages
    arr = arrays("lobbyAllowedPages")[0]
    for p in arr:
        t.eq(d(p, N.LOBBY), A, f"lobbyAllowed {p}")
    t.eq(d("/courses/123/assignments/456", N.LOBBY, "assignments"), A, "assignments lobby")
    t.eq(d("/courses/123/assignments/456/submissions/1", N.LOBBY, "assignments"), A, "assignments submissions")
    n_lobby_allowed = len(arr)
    # lobbyBlockedPages
    arr = arrays("lobbyBlockedPages")[0]
    for p in arr:
        t.eq(d(p, N.LOBBY), B, f"lobbyBlocked {p}")
    t.eq(d("/courses/123/quizzes/456", N.LOBBY, "assignments"), B, "quiz path under assignments scope")
    t.eq(d("/courses/123/assignments/4567", N.LOBBY, "assignments"), B, "assignment 4567")
    # lookAlikePathsAreRefused
    arr = arrays("lookAlikePathsAreRefused")[0]
    for p, ph in itertools.product(arr, (N.LOBBY, N.QUIZ)):
        t.eq(d(p, ph), B, f"lookAlike {ph} {p}")
    # caseIsExact
    a0, a1 = arrays("caseIsExact")[:2]
    for p in a0:
        t.eq(d(p, N.LOBBY), B, f"case {p}")
    t.eq(d("/courses/123/quizzes/456/Take", N.LOBBY), A, "case Take")
    for p in a1:
        t.eq(d(p, N.LOBBY), B, f"case2 lobby {p}")
        t.eq(d(p, N.QUIZ, open_book=True), B, f"case2 quiz openbook {p}")
    # quizPhaseKeepsOnlyTheItem
    qa = arrays("quizPhaseKeepsOnlyTheItem")
    for p in qa[0]:
        t.eq(d(p, N.QUIZ), A, f"quiz allow {p}")
    for p in qa[1]:
        t.eq(d(p, N.QUIZ), A, f"quiz login {p}")
        t.eq(d(p, N.QUIZ, "assignments"), A, f"quiz login (assignments) {p}")
    for p in qa[2] + qa[3]:
        t.eq(d(p, N.QUIZ), B, f"quiz block {p}")
    t.eq(d("/courses/123/assignments/456/anything", N.QUIZ, "assignments"), A, "assignments anything")
    t.eq(d("/courses/123/quizzes/456", N.QUIZ, "assignments"), B, "quiz under assignments scope")
    # openBookAddsCourseMaterialOnly
    oa = arrays("openBookAddsCourseMaterialOnly")
    for p in oa[0]:
        t.eq(d(p, N.QUIZ, open_book=True), A, f"openbook allow {p}")
        t.eq(d(p, N.QUIZ, open_book=False), A if "quizzes" in p else B, f"openbook off {p}")
    for p in oa[1]:
        t.eq(d(p, N.QUIZ, open_book=True), B, f"openbook block {p}")
    t.eq(d("/courses/123/pages/x", N.LOBBY, open_book=True), B, "openbook lobby pages")
    t.eq(d("/courses/123/modules", N.LOBBY, open_book=True), B, "openbook lobby modules")
    # alwaysBlockedInEveryPhase
    paths = array_after(bodies["alwaysBlockedInEveryPhase"], "let paths =")[0]
    for p, ph, ob, kind in itertools.product(paths, (N.LOBBY, N.QUIZ), (False, True), ("quizzes", "assignments")):
        t.eq(d(p, ph, kind, ob), B, f"always {ph} {ob} {kind} {p}")

    # single-line decide(...) == .x expectations in every test
    pat = re.compile(r'decide\(' + STR + r', \.(lobby|quiz)((?:, (?:kind|openBook|host): [^,)]+)*)\) == \.(allow|block)')
    single = 0
    for name, body in bodies.items():
        if name in ("lobbyAllowedPages", "lobbyBlockedPages"):
            pass
        for m in pat.finditer(body):
            path, ph, opts, want = swift_unescape(m[1]), m[2], m[3], m[4]
            kind, ob, host = "quizzes", False, None
            for o in re.finditer(r", (kind|openBook|host): ([^,)]+)", opts):
                k, v = o[1], o[2].strip()
                if k == "kind":
                    kind = v.strip('"')
                elif k == "openBook":
                    ob = v == "true"
                else:
                    host = v.strip('"')
            t.eq(d(path, ph, kind, ob, host), A if want == "allow" else B, f"{name}: {path} {ph} {opts}")
            single += 1

    # segments(encodedPath:)
    seg_n = 0
    for m in re.finditer(r'segments\(encodedPath: ' + STR + r'\) == (nil|\[[^\]]*\])', bodies["pathSegmentParsing"]):
        want = None if m[2] == "nil" else [swift_unescape(x) for x in re.findall(STR, m[2])]
        t.eq(N.segments_of_path(swift_unescape(m[1])), want, f"segments {m[1]}")
        seg_n += 1
    for m in re.finditer(r'segments\(URL\(string: ' + STR + r'\)!\) == (\[[^\]]*\])', bodies["pathSegmentParsing"]):
        want = [swift_unescape(x) for x in re.findall(STR, m[2])]
        t.eq(N.segments(swift_unescape(m[1])), want, f"segments url {m[1]}")
        seg_n += 1

    # phaseEntry
    ent = 0
    for m in re.finditer(r'#expect\((!?)enters\(' + STR + r'(?:, kind: "(\w+)")?\)\)', bodies["phaseEntry"]):
        got = N.enters_quiz("https://" + HOST + swift_unescape(m[2]), HOST, sc(m[3] or "quizzes"))
        t.eq(got, m[1] == "", f"enters {m[2]} {m[3]}")
        ent += 1
    t.eq(N.enters_quiz("https://evil.example.com/courses/123/quizzes/456/take", HOST, sc()), False, "enters other host")
    # landing / signIn
    land = 0
    for m in re.finditer(r'#expect\((!?)landing\(' + STR + r'\)\)', bodies["landingPages"]):
        t.eq(N.is_landing(m[2], HOST, sc()), m[1] == "", f"landing {m[2]}")
        land += 1
    for m in re.finditer(r'#expect\((!?)signIn\(' + STR + r'\)\)', bodies["signInPages"]):
        t.eq(N.is_sign_in(m[2], HOST), m[1] == "", f"signIn {m[2]}")
        land += 1

    # phaseMovesOnlyForward
    phase, seen = N.LOBBY, []
    for u in ["https://canvas.dku.edu/", "https://canvas.dku.edu/courses/123/quizzes/456",
              "https://canvas.dku.edu/courses/123/quizzes/456/take", "https://canvas.dku.edu/courses/123/quizzes/456"]:
        if phase == N.LOBBY and N.enters_quiz(u, HOST, sc()):
            phase = N.QUIZ
        seen.append(phase)
    t.eq(seen, [N.LOBBY, N.LOBBY, N.QUIZ, N.QUIZ], "phase moves only forward")

    # scopeParsing (transcribed: the Swift literals mix Int and String values)
    good = {"course_id": "123", "item_kind": "quizzes", "item_id": "456", "open_book": True}
    t.eq(N.scope_from_json(good), N.Scope("123", "quizzes", "456", True), "scope good")
    t.eq(N.scope_from_json({"course_id": "1", "item_kind": "assignments", "item_id": "2"}).open_book, False, "scope no open_book")
    t.eq(N.scope_from_json({"course_id": 7, "item_kind": "quizzes", "item_id": 8, "open_book": False}).course_id, "7", "scope numeric")
    for bad, label in [(None, "nil"), ("x", "string"),
                       ({"course_id": "12a", "item_kind": "quizzes", "item_id": "2"}, "12a"),
                       ({"course_id": "", "item_kind": "quizzes", "item_id": "2"}, "empty"),
                       ({"course_id": "1", "item_kind": "pages", "item_id": "2"}, "pages kind"),
                       ({"course_id": "1", "item_kind": "quizzes"}, "no item"),
                       ({"course_id": "1/2", "item_kind": "quizzes", "item_id": "2"}, "slash"),
                       ({"course_id": "9" * 21, "item_kind": "quizzes", "item_id": "2"}, "21 digits"),
                       ({"course_id": "\u0661", "item_kind": "quizzes", "item_id": "2"}, "arabic digit")]:
        t.eq(N.scope_from_json(bad), None, f"scope bad {label}")
    t.eq(N.scope_from_json({"course_id": "9" * 20, "item_kind": "quizzes", "item_id": "2"}) is not None, True, "scope 20 digits")

    # recovery gate (transcribed)
    g = N.RecoveryGate()
    t.eq([g.should_recover(1000), g.should_recover(1000.5), g.should_recover(1000.99), g.should_recover(1001.0),
          g.should_recover(1002.5)], [True, False, False, True, True], "recovery gate interval")
    g = N.RecoveryGate()
    t.eq([g.first_time("a"), g.first_time("a"), g.first_time("b")], [True, False, True], "recovery gate once")
    for i in range(N.RecoveryGate.MAX_REMEMBERED + 5):
        g.first_time(f"p{i}")
    t.eq(g.first_time("a"), True, "recovery gate forgets after 200")

    # fillGateWithScope
    start = "https://canvas.dku.edu/courses/123/quizzes/456"
    body = bodies["fillGateWithScope"]
    fn = 0
    for m in re.finditer(r'origin\(' + STR + r', (sc|nil)\) (== "https://canvas.dku.edu"|== nil|!= nil)', body):
        scope = None if m[2] == "nil" else sc()
        got = N.fill_origin(swift_unescape(m[1]), start, scope)
        want = "https://canvas.dku.edu" if "https" in m[3] else (None if m[3] == "== nil" else got)
        if m[3] == "!= nil":
            t.eq(got is not None, True, f"fill {m[1]}")
        else:
            t.eq(got, want, f"fill {m[1]}")
        fn += 1
    # legacy fillOrigin tables (no scope)
    leg = 0
    for fname, st in (("fillHostIsTheStartHostOnly", "https://Canvas.DKU.edu/courses/1/quizzes/2"),
                      ("fillOnlyOnQuizPages", "https://canvas.dku.edu/courses/1/quizzes/2")):
        for m in re.finditer(r'#expect\(origin\(' + STR + r'\) (== "https://canvas.dku.edu"|== nil|!= nil)\)', bodies[fname]):
            got = N.fill_origin(swift_unescape(m[1]), st)
            if m[2] == "!= nil":
                t.eq(got is not None, True, f"{fname} {m[1]}")
            else:
                t.eq(got, "https://canvas.dku.edu" if "https" in m[2] else None, f"{fname} {m[1]}")
            leg += 1
    t.eq(fn >= 12, True, "fillGateWithScope rows parsed")
    t.eq(N.fill_origin("https://canvas.dku.edu/courses/1/quizzes/2", "http://canvas.dku.edu/courses/1/quizzes/2"), None,
         "fill: http start host")

    # hostPolicyIsHttpsAndExact
    rules = [S.SiteRule(x) for x in ("canvas.dku.edu", "*.instructure.com", "login.microsoftonline.com")]
    pol = 0
    for m in re.finditer(r'#expect\((!?)allowed\(' + STR + r'\)\)', bodies["hostPolicyIsHttpsAndExact"]):
        t.eq(S.policy_allows(swift_unescape(m[2]), rules), m[1] == "", f"allowed {m[2]}")
        pol += 1
    # popupPlans
    for m in re.finditer(r'#expect\(plan\((nil|' + STR + r')(, survives: true)?\) == \.(child|sameView|block)\)',
                         bodies["popupPlans"]):
        url = None if m[1] == "nil" else swift_unescape(m[2])
        want = {"child": "child", "sameView": "sameView", "block": "block"}[m[4]]
        t.eq(S.popup_plan(url, HOST, rules, bool(m[3])), want, f"popup {m[1]}")
        pol += 1

    n_total = t.n
    check(f"Swift tables: {n_total} expectations parsed from CanvasModeTests.swift all agree with the port "
          f"({n_lobby_allowed} lobby-allowed paths, {single} single-line decide() cases, {seg_n} segment cases, "
          f"{ent} phase-entry, {land} landing/sign-in, {fn + leg} fill-gate, {pol} host/popup)",
          not t.bad and n_total > 400, "; ".join(t.bad[:6]) + f" ({len(t.bad)} bad, {n_total} total)")


# ------------------------------------------------------------------------------------------ oracle
def oracle(path: str, phase: str, kind: str, open_book: bool, C_="123", I="456") -> str:
    """The contract's prose as regular expressions. Not derived from canvas_nav."""
    if path == "":
        path = "/"
    if not path.startswith("/"):
        return "block"
    if re.search(r"(?i)%2f|%5c|%2e|%00|\\", path):
        return "block"
    parts = path[1:].split("/")
    if parts[-1] == "":
        parts.pop()
    if any(seg in ("", ".", "..") for seg in parts):
        return "block"
    p = "/" + "/".join(parts) if parts else "/"
    if re.match(r"(?i)^/(conversations|inbox|profile|groups|calendar|accounts|api|users)(/|$)", p):
        return "block"
    if re.match(r"(?i)^/courses/", p) and not re.match(r"^/courses/" + C_ + r"(/|$)", p):
        return "block"
    item = rf"^/courses/{C_}/{kind}/{I}(/.*)?$"
    if re.match(item, p):
        return "allow"
    if phase == "lobby":
        if re.match(rf"^(/|/login(/.*)?|/courses/{C_}|/courses/{C_}/(quizzes|assignments))$", p):
            return "allow"
        return "block"
    if re.match(r"^/login(/.*)?$", p):
        return "allow"
    if open_book:
        if re.match(rf"^(/courses/{C_}/(pages|modules|files|wiki)(/.*)?|/files(/.*)?)$", p):
            return "block" if "download" in p.lower().split("/") else "allow"
    return "block"


def generated_paths() -> list[str]:
    seg_pool = ["courses", "Courses", "123", "12", "1234", "999", "quizzes", "Quizzes", "assignments", "456", "4567",
                "45", "x", "take", "Take", "login", "Login", "saml", "pages", "modules", "files", "wiki", "download",
                "inbox", "conversations", "profile", "api", "users", "files", "77", "..", ".", "%2e%2e", "%2E", "%2f",
                "%5c", "%20", "%00", "456x", "x456", "", "submissions", "history"]
    out = set()
    prefixes = ["/courses/123", "/courses/123/quizzes/456", "/courses/123/assignments/456", "/login", "/files", "/"]
    for pre in prefixes:
        for a in seg_pool:
            out.add(f"{pre}/{a}" if pre != "/" else f"/{a}")
            out.add(f"{pre}/{a}/")
            for b in seg_pool[:30]:
                out.add(f"{pre}/{a}/{b}" if pre != "/" else f"/{a}/{b}")
    out.update(["", "/", "//", "/courses", "/courses/", "/courses//", "/courses/123//quizzes/456"])
    return sorted(out)


def differential() -> None:
    section("7b. Policy port against an independent oracle written from the contract")
    paths = generated_paths()
    bad = []
    n = 0
    for p in paths:
        for phase, kind, ob in itertools.product((N.LOBBY, N.QUIZ), ("quizzes", "assignments"), (False, True)):
            want = oracle(p, phase, kind, ob)
            got = d(p, phase, kind, ob)
            n += 1
            if want != got:
                bad.append(f"{phase}/{kind}/ob={ob} {p!r}: port {got} oracle {want}")
    check(f"{len(paths)} generated paths x 8 modes = {n} decisions: port and contract oracle agree",
          not bad, "; ".join(bad[:8]) + f" ({len(bad)} differ)")
    return bad


LOOKALIKES = [
    # (url path+query, lobby, quiz, quiz+open_book)  expected decisions for course 123 / quizzes / 456
    ("/courses/12", "block", "block", "block"),
    ("/courses/1234", "block", "block", "block"),
    ("/courses/123", "allow", "block", "block"),
    ("/courses/123/", "allow", "block", "block"),
    ("/courses/123?x=1", "allow", "block", "block"),
    ("/courses/123#/inbox", "allow", "block", "block"),
    ("/courses/123/quizzes", "allow", "block", "block"),
    ("/courses/123/quizzes/", "allow", "block", "block"),
    ("/courses/123/quizzes/456", "allow", "allow", "allow"),
    ("/courses/123/quizzes/456/", "allow", "allow", "allow"),
    ("/courses/123/quizzes/456/take", "allow", "allow", "allow"),
    ("/courses/123/quizzes/456/take/", "allow", "allow", "allow"),
    ("/courses/123/quizzes/4567", "block", "block", "block"),
    ("/courses/123/quizzes/45", "block", "block", "block"),
    ("/courses/123/quizzes/456x", "block", "block", "block"),
    ("/courses/123/quizzes/0456", "block", "block", "block"),
    ("/courses/123/quizzes/456%20", "block", "block", "block"),
    ("/courses/123/quizzes/456;jsessionid=1", "block", "block", "block"),
    ("/courses/123/quizzes/456%2ftake", "block", "block", "block"),
    ("/courses/123/quizzes/456%2Ftake", "block", "block", "block"),
    ("/courses/123/quizzes/456/%2e%2e/457", "block", "block", "block"),
    ("/courses/123/quizzes/456/..%2f457", "block", "block", "block"),
    ("/courses/123/quizzes/456/../457", "block", "block", "block"),
    ("/courses/123/quizzes/456/./take", "block", "block", "block"),
    ("/courses/123/quizzes/456//take", "block", "block", "block"),
    ("/courses/123/quizzes/456%5ctake", "block", "block", "block"),
    ("/courses/123/quizzes/456\\take", "block", "block", "block"),
    ("/courses/123/quizzes/456%00", "block", "block", "block"),
    ("/courses/123/quizzes/456?q=/inbox", "allow", "allow", "allow"),
    ("/courses/123/quizzes/456?q=%2e%2e", "allow", "allow", "allow"),
    ("/courses/123/quizzes/456#/courses/999", "allow", "allow", "allow"),
    ("/courses/123/quizzes/4567?next=/courses/123/quizzes/456", "block", "block", "block"),
    ("/Courses/123/quizzes/456", "block", "block", "block"),
    ("/COURSES/123/QUIZZES/456", "block", "block", "block"),
    ("/courses/123/Quizzes/456", "block", "block", "block"),
    ("/courses/123/quizzes/456/TAKE", "allow", "allow", "allow"),
    ("/courses/\u0661\u0662\u0663/quizzes/456", "block", "block", "block"),
    ("/courses/%31%32%33/quizzes/456", "block", "block", "block"),
    ("/courses/123/assignments/456", "block", "block", "block"),
    ("/courses/123/assignments", "allow", "block", "block"),
    ("/courses/123/pages/p1", "block", "block", "allow"),
    ("/courses/123/pages", "block", "block", "allow"),
    ("/courses/123/pagesx", "block", "block", "block"),
    ("/courses/123/modules", "block", "block", "allow"),
    ("/courses/123/modules/items/9", "block", "block", "allow"),
    ("/courses/123/files", "block", "block", "allow"),
    ("/courses/123/files/77", "block", "block", "allow"),
    ("/courses/123/files/77/download", "block", "block", "block"),
    ("/courses/123/files/77/download?download_frd=1", "block", "block", "block"),
    ("/courses/123/files/77/Download", "block", "block", "block"),
    ("/courses/123/files/77?download=1", "block", "block", "block"),
    ("/courses/123/files/77?download_frd=1", "block", "block", "block"),
    ("/courses/123/files/77?Download_Frd=1", "block", "block", "block"),
    ("/courses/123/files/77?x=1&download", "block", "block", "block"),
    ("/courses/123/files/77?%64ownload=1", "block", "block", "block"),
    ("/courses/123/files/77?wrap=1", "block", "block", "allow"),
    ("/courses/123/files/77?x=download", "block", "block", "allow"),
    ("/courses/123/quizzes/456?download=1", "block", "block", "block"),
    ("/courses/123/quizzes/456/take?download_frd=1", "block", "block", "block"),
    ("/?download=1", "block", "block", "block"),
    ("/courses/123/files/77/inline_view", "block", "block", "allow"),
    ("/courses/123/wiki/w1", "block", "block", "allow"),
    ("/courses/999/pages/p1", "block", "block", "block"),
    ("/courses/999/quizzes/456", "block", "block", "block"),
    ("/files/77", "block", "block", "allow"),
    ("/files/77/download", "block", "block", "block"),
    ("/files", "block", "block", "allow"),
    ("/filesx/1", "block", "block", "block"),
    ("/file/1", "block", "block", "block"),
    ("/", "allow", "block", "block"),
    ("", "allow", "block", "block"),
    ("/?login_success=1", "allow", "block", "block"),
    ("/login", "allow", "allow", "allow"),
    ("/login/", "allow", "allow", "allow"),
    ("/login/saml", "allow", "allow", "allow"),
    ("/login/session_token?return_to=%2Fconversations", "allow", "allow", "allow"),
    ("/loginx", "block", "block", "block"),
    ("/Login", "block", "block", "block"),
    ("/login/../inbox", "block", "block", "block"),
    ("/conversations", "block", "block", "block"),
    ("/conversations/1", "block", "block", "block"),
    ("/Conversations", "block", "block", "block"),
    ("/inbox", "block", "block", "block"),
    ("/inbox?courses/123", "block", "block", "block"),
    ("/profile", "block", "block", "block"),
    ("/profile/settings", "block", "block", "block"),
    ("/groups/1", "block", "block", "block"),
    ("/calendar", "block", "block", "block"),
    ("/accounts/1", "block", "block", "block"),
    ("/api/v1/courses/123/quizzes/456", "block", "block", "block"),
    ("/users/self", "block", "block", "block"),
    ("/dashboard", "block", "block", "block"),
    ("/courses", "block", "block", "block"),
    ("/courses/123/grades", "block", "block", "block"),
    ("/courses/123/users", "block", "block", "block"),
]


def lookalikes(exam_host="canvas.dku.edu") -> None:
    section("7c. Look-alike URLs: paths (three modes) and hosts")
    bad = []
    for path, lob, quiz, book in LOOKALIKES:
        url = "https://" + exam_host + path
        for phase, ob, want in ((N.LOBBY, False, lob), (N.QUIZ, False, quiz), (N.QUIZ, True, book)):
            got = N.decide(url, exam_host, N.Scope("123", "quizzes", "456", ob), phase)
            if got != want:
                bad.append(f"{phase}/ob={ob} {path!r}: got {got} want {want}")
    check(f"{len(LOOKALIKES)} hand-written look-alike paths x lobby / quiz / quiz+open-book", not bad, "; ".join(bad[:8]))

    rules = [S.SiteRule(x) for x in (exam_host, "login.microsoftonline.com", "*.quiz-lti-iad-prod.instructure.com")]
    sc_ = N.Scope("123", "quizzes", "456", True)

    def verdict(url, phase=N.LOBBY):
        """What the navigation delegate does: site rules first, then the scope rule on the exam host."""
        if not S.policy_allows(url, rules):
            return "blocked-host"
        return "block" if N.decide(url, exam_host, sc_, phase) == N.BLOCK else "allow"

    host_cases = [
        (f"https://{exam_host}/courses/123", "allow"),
        (f"https://{exam_host.upper()}/courses/123", "allow"),
        (f"https://{exam_host}:443/courses/123", "allow"),
        (f"https://{exam_host}:8443/courses/123", "blocked-host"),
        (f"https://{exam_host}:80/courses/123", "blocked-host"),
        (f"http://{exam_host}/courses/123", "blocked-host"),
        (f"https://{exam_host}./courses/123", "blocked-host"),
        (f"https://x.{exam_host}/courses/123", "blocked-host"),
        (f"https://{exam_host}.evil.com/courses/123", "blocked-host"),
        (f"https://evil{exam_host}/courses/123", "blocked-host"),
        (f"https://evil.com/{exam_host}/courses/123", "blocked-host"),
        (f"https://evil.com?{exam_host}/courses/123", "blocked-host"),
        (f"https://evil.com#{exam_host}/courses/123", "blocked-host"),
        (f"https://user@{exam_host}/courses/123", "blocked-host"),
        (f"https://user:pw@{exam_host}/courses/123", "blocked-host"),
        (f"https://{exam_host}@evil.com/courses/123", "blocked-host"),
        (f"https://{exam_host}:443@evil.com/courses/123", "blocked-host"),
        (f"https://{exam_host}\\@evil.com/courses/123", "not-allowed"),
        (f"https://evil.com\\.{exam_host}/courses/123", "blocked-host"),
        ("https://login.microsoftonline.com/inbox", "allow"),
        ("https://login.microsoftonline.com.evil.com/inbox", "blocked-host"),
        ("https://x.quiz-lti-iad-prod.instructure.com/anything", "allow"),
        ("https://quiz-lti-iad-prod.instructure.com/anything", "allow"),
        ("https://x.quiz-lti-iad-prod.instructure.com.evil.com/a", "blocked-host"),
        ("javascript:alert(1)", "blocked-host"),
        ("data:text/html,x", "blocked-host"),
        ("file:///etc/passwd", "blocked-host"),
        ("about:blank", "blocked-host"),
        ("ftp://canvas.dku.edu/courses/123", "blocked-host"),
        (f"https://{exam_host}/inbox", "block"),
        (f"https://{exam_host}/courses/999", "block"),
        (f"https://{exam_host}/courses/123", "allow"),
    ]
    hbad = []
    for url, want in host_cases:
        got = verdict(url)
        if want == "not-allowed":
            if got == "allow":
                hbad.append(f"{url}: allowed")
            continue
        if got != want:
            hbad.append(f"{url}: got {got} want {want}")
    check(f"{len(host_cases)} look-alike hosts, schemes, ports and userinfo through site rules + scope rule",
          not hbad, "; ".join(hbad[:8]))

    # popups obey the same rules (ExternalExamController.popupPlan)
    def popup(url):
        if url and N.decide(url, exam_host, sc_, N.LOBBY) == N.BLOCK:
            return "block"
        return S.popup_plan(url, exam_host, rules, False)
    pb = []
    for url, want in [(f"https://{exam_host}/inbox", "block"), (f"https://{exam_host}/courses/999/pages/x", "block"),
                      (f"https://{exam_host}/courses/123/quizzes/456", "child"),
                      ("https://login.microsoftonline.com/x", "sameView"), ("about:blank", "child"),
                      (None, "child"), ("https://evil.example.com/", "block")]:
        got = popup(url)
        if got != want:
            pb.append(f"{url}: got {got} want {want}")
    check("popup plans obey the scope rule (blocked page: block; item page: child; sign-in host: same view)",
          not pb, "; ".join(pb))


def run() -> None:
    swift_tables()
    differential()
    lookalikes()
