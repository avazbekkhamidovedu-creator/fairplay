"""A small stand-in for DKU OJ's side of the homework sign-in (no network, no real DKU OJ).

An ASGI app with the sign-in form, the "/" landing page, the ticket minting page
(/fairplay/app-login/) and the signed redeem call (POST /api/fairplay/v1/app-login/redeem).
The signature check is written out here independently of app.integrations.dkuoj.signing.
`FakeTransport` lets the FairPlay server's DKU OJ client talk to it (and play "unreachable").
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from urllib.parse import parse_qs, quote, unquote

import httpx

TOKEN = "fairplay-shared-token-0123456789abcdef"
PUBLIC = "https://oj.example.edu"
INSTALLATION_ID = "7c1d2a54-3b9e-4f60-8a1d-5e2b9c0f4d37"
TICKET_TTL_S = 60


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def challenge_of(verifier: str) -> str:
    return b64url(hashlib.sha256(verifier.encode()).digest())


class FakeDkuojLogin:
    def __init__(self):
        self.users = {
            "ab123": {"first_name": "Ada", "last_name": "Byron", "email": "ab123@duke.edu", "classes": [{"id": 7, "name": "CS 101"}]},
            "cd456": {"first_name": "Chen", "last_name": "Dai", "email": "cd456@duke.edu", "classes": [{"id": 7, "name": "CS 101"}]},
            "noclass": {"first_name": "No", "last_name": "Class", "email": "n@duke.edu", "classes": []},
            "prof1": {"first_name": "Pat", "last_name": "Prof", "email": "p@duke.edu", "classes": [{"id": 7, "name": "CS 101"}]},
        }
        self.session_netid: str | None = None
        self.tickets: dict[str, dict] = {}
        self.clock = time.time
        self.redeem_calls = 0
        self.last_redeem: dict | None = None
        self.down = False

    def login(self, netid: str) -> None:
        self.session_netid = netid

    def mint(self, state: str, challenge: str, netid: str | None = None) -> str:
        t = secrets.token_urlsafe(24)
        self.tickets[t] = {"netid": netid or self.session_netid, "state": state, "challenge": challenge,
                           "exp": self.clock() + TICKET_TTL_S, "used": False}
        return t

    def signed(self, method: str, raw_target: str, headers: dict, body: bytes) -> bool:
        if headers.get("authorization"):
            return False
        ts, nonce = headers.get("x-fp-timestamp", ""), headers.get("x-fp-nonce", "")
        if not ts.isdigit() or abs(time.time() - int(ts)) > 60 or len(nonce) != 32:
            return False
        key = hmac.new(TOKEN.encode(), b"fairplay-bridge v2 fp->oj", hashlib.sha256).digest()
        canonical = "\n".join(["v2", method, raw_target, ts, nonce, "", hashlib.sha256(body).hexdigest()])
        want = hmac.new(key, canonical.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(want, headers.get("x-fp-signature", ""))

    async def __call__(self, scope, receive, send):
        assert scope["type"] == "http"
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        method, path = scope["method"], scope["path"]
        query = parse_qs(scope["query_string"].decode())
        raw_target = scope["raw_path"].decode() + (("?" + scope["query_string"].decode()) if scope["query_string"] else "")
        status, hdrs, out = await self.route(method, path, query, raw_target, headers, body)
        if isinstance(out, (dict, list)):
            out = json.dumps(out).encode()
            hdrs = {**hdrs, "content-type": "application/json"}
        if path.startswith("/api/fairplay/v1/"):
            from app.integrations.dkuoj import signing
            hdrs = {**hdrs, **signing.sign_headers(
                TOKEN, signing.OJ_TO_FP, f"RESPONSE {method} {status}", raw_target,
                out if isinstance(out, bytes) else str(out).encode(), acting_user=headers.get("x-fp-nonce"))}
        elif isinstance(out, str):
            out = out.encode()
            hdrs = {"content-type": "text/html; charset=utf-8", **hdrs}
        await send({"type": "http.response.start", "status": status,
                    "headers": [(k.encode(), v.encode()) for k, v in hdrs.items()]})
        await send({"type": "http.response.body", "body": out})

    async def route(self, method, path, query, raw_target, headers, body):
        if path == "/" and method == "GET":
            return 200, {}, "<html><title>DKU OJ</title>home</html>"
        if path == "/accounts/login" and method == "GET":
            return 200, {}, "<form method=post action=/accounts/login><input name=netid><input type=password name=pw></form>"
        if path == "/accounts/login" and method == "POST":
            netid = parse_qs(body.decode()).get("netid", [""])[0]
            if netid not in self.users:
                return 200, {}, "<p>wrong sign-in</p>"
            self.session_netid = netid
            nxt = unquote(query.get("next", ["/"])[0])
            return 302, {"location": nxt}, b""
        if path == "/fairplay/app-login/" and method == "GET":
            state, challenge = query.get("state", [""])[0], query.get("challenge", [""])[0]
            if not state or not challenge:
                return 400, {}, "bad request"
            if self.session_netid is None:
                return 302, {"location": "/accounts/login?next=" + quote(raw_target, safe="")}, b""
            t = self.mint(state, challenge)
            return 302, {"location": f"/fairplay/app-login/done#ticket={t}&state={state}"}, b""
        if path == "/fairplay/app-login/done" and method == "GET":
            return 200, {}, "<html>You can close this window.</html>"
        if path.startswith("/api/fairplay/v1/") and query.get("installation_id") != [INSTALLATION_ID]:
            return 409, {}, {"detail": "wrong installation"}
        if path == "/api/fairplay/v1/installation" and method == "GET":
            return 200, {}, {"protocol": 1, "installation_id": INSTALLATION_ID,
                             "request_nonce": headers.get("x-fp-nonce")}
        if path == "/api/fairplay/v1/app-login/redeem" and method == "POST":
            return self.redeem(method, raw_target, headers, body)
        m = re.fullmatch(r"/api/fairplay/v1/students/([^/]+)/classes", path)
        if m and method == "GET":
            if not self.signed(method, raw_target, headers, body):
                return 401, {}, {"detail": "bad signature"}
            u = self.users.get(unquote(m.group(1)))
            if u is None:
                return 404, {}, {"detail": "unknown student"}
            return 200, {}, {"classes": [{"id": c["id"], "name": c["name"], "course": {"id": 1, "name": "Course"},
                                          "teachers": ["Pat Prof"]} for c in u["classes"]]}
        return 404, {}, {"detail": "Not Found"}

    def redeem(self, method, raw_target, headers, body):
        self.redeem_calls += 1
        if not self.signed(method, raw_target, headers, body):
            return 401, {}, {"detail": "bad signature"}
        req = json.loads(body or b"{}")
        self.last_redeem = req
        t = self.tickets.get(req.get("ticket"))
        if t is None or t["used"] or self.clock() > t["exp"]:
            return 404, {}, {"detail": "unknown ticket"}
        t["used"] = True
        if not hmac.compare_digest(challenge_of(str(req.get("verifier") or "")), t["challenge"]):
            return 409, {}, {"detail": "challenge mismatch"}
        u = self.users[t["netid"]]
        return 200, {}, {"netid": t["netid"], "first_name": u["first_name"], "last_name": u["last_name"],
                         "email": u["email"]}


class FakeTransport(httpx.AsyncBaseTransport):
    def __init__(self, fake: FakeDkuojLogin):
        self.fake = fake
        self.inner = httpx.ASGITransport(app=fake)

    async def handle_async_request(self, request):
        if self.fake.down:
            raise httpx.ConnectError("down", request=request)
        return await self.inner.handle_async_request(request)
