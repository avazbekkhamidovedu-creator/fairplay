import asyncio
import base64
import hashlib
import hmac
import json
import random
import secrets
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

from safety import PinnedTransport
from schedule import HAND_IN_FOLLOW_UPS, LEASE_SECONDS, RenewSchedule, parse_retry_after

LOCK_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
BLOB_BYTES = 92
ESCROW_ATTEMPTS = 4
OK = {"escrow": ("201",), "login": ("200", "302"), "claim": ("200",), "renew": ("200",),
      "page": ("200",), "hand_in": ("200",), "login_page": ("200",)}


@dataclass
class Config:
    clients: int
    fairplay_url: str = ""
    oj_url: str = ""
    oj_mode: str = "ingress-bypass"
    contest: str = ""
    app_key: str = ""
    ingress_key: str = ""
    users: list = field(default_factory=list)
    pages: list = field(default_factory=lambda: ["/", "/contests/", "/contest/{contest}"])
    start_window: float = 120.0
    duration: float = 600.0
    time_scale: float = 1.0
    loss: float = 0.0
    timeout: float = 10.0
    seed: int | None = None
    ca_bundle: str | None = None
    transport: object = None
    guard: object = None
    pins: dict | None = None


def lock_message(lock_id, device):
    return f"nativelock|{lock_id}|{device}"


def sign(app_key, message):
    return hmac.new(app_key.encode(), message.encode(), hashlib.sha256).hexdigest()


def make_lock(app_key, rng):
    lock_id = "".join(rng.choice(LOCK_ALPHABET) for _ in range(8))
    device = "lt-" + secrets.token_hex(8)
    blob = base64.b64encode(secrets.token_bytes(BLOB_BYTES)).decode()
    return {"lock_id": lock_id, "blob": blob, "device": device,
            "signature": sign(app_key, lock_message(lock_id, device))}


def client_cert_pem():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    from datetime import datetime, timedelta, timezone
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "loadtest-" + secrets.token_hex(4))])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(Encoding.PEM).decode()


def parse_state(body):
    try:
        envelope = json.loads(body)
        return json.loads(base64.b64decode(envelope["payload"]))
    except (ValueError, KeyError, TypeError):
        return None


class Runner:
    def __init__(self, cfg, metrics):
        self.cfg = cfg
        self.m = metrics
        self.rng = random.Random(cfg.seed)
        self.scale = cfg.time_scale
        self.http = None

    async def sleep(self, seconds):
        await asyncio.sleep(seconds * self.scale)

    async def call(self, endpoint, method, url, client_id, **kw):
        """Returns (status | 'timeout' | 'error' | 'simulated', response | None)."""
        if self.cfg.loss and self.rng.random() < self.cfg.loss:
            self.m.record_simulated(endpoint)
            return "simulated", None
        start = time.monotonic()
        try:
            resp = await self.http.request(method, url, **kw)
            status = resp.status_code
        except httpx.TimeoutException:
            resp, status = None, "timeout"
        except httpx.HTTPError:
            resp, status = None, "error"
        end = time.monotonic()
        self.m.record(endpoint, status, end - start, end)
        return status, resp

    async def run(self):
        cfg = self.cfg
        limits = httpx.Limits(max_connections=cfg.clients + 16, max_keepalive_connections=cfg.clients + 16)
        transport = cfg.transport or httpx.AsyncHTTPTransport(limits=limits, verify=cfg.ca_bundle or True)
        if cfg.pins is not None:
            transport = PinnedTransport(transport, cfg.pins)
        self.http = httpx.AsyncClient(transport=transport, timeout=cfg.timeout, follow_redirects=False,
                                      trust_env=False)
        try:
            tasks = [asyncio.create_task(self.client(i)) for i in range(cfg.clients)]
            everyone = asyncio.gather(*tasks)
            watchdog = asyncio.create_task(self.watch()) if cfg.guard else None
            waiting = {everyone} | ({watchdog} if watchdog else set())
            await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
            if watchdog and watchdog.done() and watchdog.exception():
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                everyone.exception() if everyone.done() and not everyone.cancelled() else everyone.cancel()
                raise watchdog.exception()
            await everyone
            if watchdog:
                watchdog.cancel()
        finally:
            await self.http.aclose()

    async def watch(self):
        """Half way through the steady state, resolve the targets again; abort if one went live."""
        await self.sleep(self.cfg.start_window + self.cfg.duration / 2)
        await asyncio.to_thread(self.cfg.guard)

    async def client(self, index):
        try:
            await self.sleep(self.rng.uniform(0, self.cfg.start_window))
            self.m.started_clients += 1
            await self.session(index)
            self.m.finished_clients += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            self.m.client_failures += 1

    async def session(self, index):
        cfg = self.cfg
        schedule = RenewSchedule(self.rng.random)
        if cfg.fairplay_url:
            await self.escrow(index, schedule)
        if not cfg.oj_url:
            await self.sleep(cfg.duration)
            return
        bypass = cfg.oj_mode == "ingress-bypass"
        headers = {}
        cookies = {}
        if bypass:
            headers.update({"X-FairPlay-Ingress-Key": cfg.ingress_key, "X-FairPlay-Client-Verify": "SUCCESS",
                            "X-FairPlay-Client-Cert": quote(client_cert_pem(), safe="")})
        await self.login(index, headers, cookies)
        for path in cfg.pages:
            await self.page("page", path, index, headers, cookies)
        deadline = time.monotonic() + cfg.duration * self.scale
        if not bypass:
            await self.plain_loop(index, headers, cookies, schedule, deadline)
            return
        state = await self.claim(index, headers, cookies, schedule, deadline)
        if state is None:
            return
        await self.renew_loop(index, state, headers, cookies, schedule, deadline)

    def request_headers(self, headers, cookies, post=False):
        out = dict(headers)
        if cookies:
            out["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
        if post and "csrftoken" in cookies:
            out["X-CSRFToken"] = cookies["csrftoken"]
            out["Referer"] = self.cfg.oj_url
        return out

    @staticmethod
    def keep_cookies(resp, cookies):
        if resp is not None:
            for k, v in resp.cookies.items():
                cookies[k] = v

    async def login(self, index, headers, cookies):
        cfg = self.cfg
        base = cfg.oj_url
        status, resp = await self.call("login_page", "GET", base + "/accounts/login/", index,
                                       headers=self.request_headers(headers, cookies))
        self.keep_cookies(resp, cookies)
        if index >= len(cfg.users) or status != 200:
            return
        user, password = cfg.users[index]
        form = {"username": user, "password": password, "csrfmiddlewaretoken": cookies.get("csrftoken", "")}
        status, resp = await self.call("login", "POST", base + "/accounts/login/", index, data=form,
                                       headers=self.request_headers(headers, cookies, post=True))
        self.keep_cookies(resp, cookies)

    async def page(self, endpoint, path, index, headers, cookies):
        url = self.cfg.oj_url + path.format(contest=self.cfg.contest)
        return await self.call(endpoint, "GET", url, index, headers=self.request_headers(headers, cookies))

    async def escrow(self, index, schedule):
        body = make_lock(self.cfg.app_key, self.rng)
        for _ in range(ESCROW_ATTEMPTS):
            status, resp = await self.call("escrow", "POST", self.cfg.fairplay_url + "/api/native-exit/locks",
                                           index, json=body)
            if status == 201:
                schedule.failures = 0
                return True
            transport = status in ("timeout", "error", "simulated")
            code = None if transport else status
            retry_after = parse_retry_after(resp.headers.get("retry-after")) if resp is not None else None
            wait = schedule.next(code, transport_failed=False, retry_after=retry_after)
            if not (transport or code == 429 or (code is not None and code >= 500)):
                return False
            await self.sleep(wait)
        return False

    async def claim(self, index, headers, cookies, schedule, deadline):
        cfg = self.cfg
        while time.monotonic() < deadline:
            status, resp = await self.call(
                "claim", "POST", f"{cfg.oj_url}/api/fairplay/native/claim/{cfg.contest}", index,
                data={"nonce": str(uuid.uuid4())}, headers=self.request_headers(headers, cookies, post=True))
            if status == 200:
                state = parse_state(resp.content)
                if state and state.get("session"):
                    return state
                return None
            transport = status in ("timeout", "error", "simulated")
            retry_after = parse_retry_after(resp.headers.get("retry-after")) if resp is not None else None
            if not (transport or status == 429 or (isinstance(status, int) and status >= 500)):
                return None
            await self.sleep(schedule.next(None if transport else status, retry_after=retry_after))
        return None

    def note_state(self, index, state):
        if not state:
            return
        if state.get("state") != "active":
            self.m.lease_expired(index)
        if state.get("hold"):
            self.m.hold(index)

    async def renew_once(self, index, session_id, headers, cookies, last_ok):
        status, resp = await self.call(
            "renew", "POST", f"{self.cfg.oj_url}/api/fairplay/native/session/{session_id}/renew", index,
            data={"nonce": str(uuid.uuid4())}, headers=self.request_headers(headers, cookies, post=True))
        now = time.monotonic()
        if now - last_ok[0] > LEASE_SECONDS * self.scale:
            self.m.lease_expired(index)
        retry_after = None
        if status == 200:
            self.note_state(index, parse_state(resp.content))
            last_ok[0] = now
        elif resp is not None:
            retry_after = parse_retry_after(resp.headers.get("retry-after"))
        return status, retry_after

    async def renew_loop(self, index, state, headers, cookies, schedule, deadline):
        session_id = state["session"]
        self.note_state(index, state)
        last_ok = [time.monotonic()]
        wait = schedule.interval()
        while True:
            await self.sleep(wait)
            if time.monotonic() >= deadline:
                break
            status, retry_after = await self.renew_once(index, session_id, headers, cookies, last_ok)
            transport = status in ("timeout", "error", "simulated")
            wait = schedule.next(None if transport else status, retry_after=retry_after)
        await self.page("hand_in", "/contest/{contest}/leave", index, headers, cookies)
        previous = 0.0
        for at in HAND_IN_FOLLOW_UPS:
            await self.sleep(at - previous)
            previous = at
            await self.renew_once(index, session_id, headers, cookies, last_ok)

    async def plain_loop(self, index, headers, cookies, schedule, deadline):
        path = self.cfg.pages[0] if self.cfg.pages else "/"
        while True:
            await self.sleep(schedule.interval())
            if time.monotonic() >= deadline:
                return
            await self.page("page", path, index, headers, cookies)
