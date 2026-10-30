"""Settings read from the environment (at app creation time, so tests can override)."""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

log = logging.getLogger("proctora")

DEV_SECRET = "proctora-dev-secret-change-me"   # never accepted any more (audit 2026-10-12 #12)
# Shared with dev builds of the Mac app (mac/build.sh without FAIRPLAY_APP_KEY). Only
# accepted when FAIRPLAY_DEV=1; production must set FAIRPLAY_APP_KEY.
DEV_APP_KEY = "fairplay-dev-app-key-not-secret"


# Parts of FairPlay that are switched off unless FAIRPLAY_FEATURES lists them.
OPTIONAL_FEATURES = ("scan", "lms", "practice")


def async_db_url(url: str) -> str:
    """Hosts such as Render hand out "postgres://" / "postgresql://"; the server talks to
    Postgres through asyncpg, so name that driver (2026-10-20)."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url


def initial_users(text: str) -> list[tuple[str, str, str, str]]:
    """FAIRPLAY_INITIAL_USERS: "username:password:Full Name:role;..." (role teacher or
    student). Accounts for a server without the demo seed, kept in the host's secret
    environment. Malformed entries are skipped (and logged by the caller)."""
    out = []
    for item in (text or "").split(";"):
        parts = [p.strip() for p in item.split(":")]
        if len(parts) != 4:
            continue
        username, password, name, role = parts
        if username and len(password) >= 8 and name and role in ("teacher", "student"):
            out.append((username, password, name, role))
    return out


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _list(name: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


WEBTEST_ID_RE = re.compile(r"[a-z0-9-]{1,32}")
WEBTEST_HOST_RE = re.compile(r"(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
WEBTEST_MAX = 12


def parse_webtest_sites(raw, dev: bool = False) -> list[dict]:
    """Validated HOMEWORK_WEBTEST_SITES entries: {id, label, url, host, hosts, box}. RuntimeError on a bad value."""
    import json
    from urllib.parse import urlsplit
    if isinstance(raw, str):
        if not raw.strip():
            return []
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise RuntimeError(f"HOMEWORK_WEBTEST_SITES is not valid JSON ({exc}).") from None
    if not isinstance(raw, list):
        raise RuntimeError("HOMEWORK_WEBTEST_SITES must be a JSON list of {id, label, url} objects.")
    if len(raw) > WEBTEST_MAX:
        raise RuntimeError(f"HOMEWORK_WEBTEST_SITES has {len(raw)} entries; at most {WEBTEST_MAX}.")
    out, seen = [], set()
    for i, e in enumerate(raw):
        where = f"HOMEWORK_WEBTEST_SITES entry {i + 1}"
        if not isinstance(e, dict):
            raise RuntimeError(f"{where} must be an object.")
        unknown = set(e) - {"id", "label", "url", "hosts", "box"}
        if unknown:
            raise RuntimeError(f"{where} has unknown keys: {', '.join(sorted(map(str, unknown)))}.")
        sid, label, url = e.get("id"), e.get("label"), e.get("url")
        if not isinstance(sid, str) or not WEBTEST_ID_RE.fullmatch(sid):
            raise RuntimeError(f"{where}: id must be 1-32 characters of a-z, 0-9 and -.")
        if sid in seen:
            raise RuntimeError(f"{where}: id {sid!r} is used twice.")
        seen.add(sid)
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 40:
            raise RuntimeError(f"{where} ({sid}): label must be 1-40 characters.")
        if not isinstance(url, str):
            raise RuntimeError(f"{where} ({sid}): url is required.")
        try:
            parts = urlsplit(url)
            host, _ = parts.hostname, parts.port
        except ValueError:
            raise RuntimeError(f"{where} ({sid}): url is not a valid address.") from None
        if parts.username is not None or parts.password is not None or "@" in parts.netloc:
            raise RuntimeError(f"{where} ({sid}): url must not contain a user name or password.")
        loopback = host in ("localhost", "127.0.0.1", "::1")
        if not host or not (parts.scheme == "https" or (dev and parts.scheme == "http" and loopback)):
            raise RuntimeError(f"{where} ({sid}): url must be https:// (http://localhost only with FAIRPLAY_DEV=1).")
        hosts = e.get("hosts", [])
        if not isinstance(hosts, list) or not all(isinstance(h, str) for h in hosts):
            raise RuntimeError(f"{where} ({sid}): hosts must be a list of host names.")
        hosts = [h.strip().lower() for h in hosts]
        for h in hosts:
            if not WEBTEST_HOST_RE.fullmatch(h):
                raise RuntimeError(f"{where} ({sid}): host {h!r} must be a host name or *.suffix (no port or path).")
        box = e.get("box", False)
        if not isinstance(box, bool):
            raise RuntimeError(f"{where} ({sid}): box must be true or false.")
        out.append({"id": sid, "label": label.strip(), "url": url, "host": host.lower(), "hosts": hosts, "box": box})
    return out


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _canvas_min_version(name: str, default: str) -> str:
    """A minimum Canvas app version like 0.7.2 (a leading v is dropped), or "" for not supported
    yet. Anything else stops the server at startup: a typo must never open the gate."""
    raw = os.environ.get(name)
    value = default if raw is None else raw.strip()
    if value[:1] in ("v", "V"):
        value = value[1:] or "?"
    if value and not re.fullmatch(r"[0-9]+(\.[0-9]+)*", value):
        raise ValueError(f"{name} must be a version like 0.7.2 or empty, not {raw!r}")
    return value


MIN_ADMIN_TEST_CODE = 12


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: async_db_url(os.environ.get(
        "DATABASE_URL", "sqlite+aiosqlite:///./data/proctora.db")))
    redis_url: str | None = field(default_factory=lambda: os.environ.get("REDIS_URL") or None)
    # Token/LMS sealing secret. Empty -> resolved at startup by check_secure(): a random
    # secret persisted under data/ in dev (FAIRPLAY_DEV=1), otherwise the server refuses to start.
    secret: str = field(default_factory=lambda: os.environ.get("PROCTORA_SECRET")
                        or os.environ.get("FAIRPLAY_SECRET") or "",
                        repr=False)
    # FAIRPLAY_DEV=1: local laptop demo (Run Server.command). Relaxes the startup checks only.
    dev: bool = field(default_factory=lambda: _flag("FAIRPLAY_DEV", False))
    # Audit #1-#3: exams can only be started from the FairPlay app (signed proof) and every
    # attempt is bound to one app session. Off only for local tests / browser demos.
    require_app: bool = field(default_factory=lambda: _flag("FAIRPLAY_REQUIRE_APP", True))
    # Student pages and student API answer a bare 404 to anything that is not a FairPlay app
    # (app/browser_gate.py): strict | headers | off.
    browser_gate: str = field(default_factory=lambda: (
        os.environ.get("FAIRPLAY_BROWSER_GATE", "headers").strip().lower() or "headers"))
    # HMAC keys compiled into the Mac app (comma-separated; several = key rotation).
    app_keys: list[str] = field(default_factory=lambda: _list("FAIRPLAY_APP_KEY"), repr=False)
    # Extra browser origins allowed to call the API (the UI itself is same-origin).
    cors_origins: list[str] = field(default_factory=lambda: _list("FAIRPLAY_CORS_ORIGINS"))
    token_ttl_s: int = 43200                 # teachers: 12 h
    admin_test_code: str = field(default_factory=lambda: os.environ.get("FAIRPLAY_ADMIN_TEST_CODE", "").strip(),
                                 repr=False)
    # Audit S-15: student tokens travel through the exam page and the app; shorter life.
    student_token_ttl_s: int = field(default_factory=lambda: _int("FAIRPLAY_STUDENT_TOKEN_TTL_S", 8 * 3600))
    db_pool_size: int = field(default_factory=lambda: _int("DB_POOL_SIZE", 10))
    db_max_overflow: int = field(default_factory=lambda: _int("DB_MAX_OVERFLOW", 20))
    pbkdf2_iterations: int = field(default_factory=lambda: _int("PROCTORA_PBKDF2_ITERATIONS", 200000))
    seed_loadtest_users: int = field(default_factory=lambda: _int("PROCTORA_SEED_LOADTEST_USERS", 0))
    # Audit 2026-10-12 S-01: the demo logins (teacher/teacher123, alice/alice123 ...) exist
    # only on a dev server (FAIRPLAY_DEV=1) or when asked for explicitly (tests, a demo box).
    seed_demo: bool = field(default_factory=lambda: _flag("FAIRPLAY_SEED_DEMO", False))
    # 2026-10-20: real accounts for a hosted test server (see initial_users).
    initial_users: list = field(default_factory=lambda: initial_users(os.environ.get("FAIRPLAY_INITIAL_USERS", "")))
    # Audit S-08: without Redis every worker has its own memory store (exam-cache
    # invalidation, nonces, lockouts, beat replay guard all break with >1 process). A real
    # server needs REDIS_URL, or must say it runs one process (FAIRPLAY_SINGLE_PROCESS=1).
    single_process: bool = field(default_factory=lambda: _flag("FAIRPLAY_SINGLE_PROCESS", False))
    workers: int = field(default_factory=lambda: _int("WEB_CONCURRENCY", 1))
    # Audit S-17: Postgres max_connections and how many API containers share it (0 = unchecked).
    db_max_connections: int = field(default_factory=lambda: _int("FAIRPLAY_DB_MAX_CONNECTIONS", 0))
    replicas: int = field(default_factory=lambda: _int("FAIRPLAY_REPLICAS", 1))
    # Sign-in tries per IP per minute (audit S-05). Raise it for load tests from one machine.
    login_ip_limit: int = field(default_factory=lambda: _int("FAIRPLAY_LOGIN_IP_LIMIT", 300))
    # perf 0.6.2: a whole school usually reaches the server from ONE public address (NAT):
    # 5,000 students signing in before an exam are 5,000 tries from that address. Networks
    # listed here (CIDRs, e.g. "203.0.113.0/24,2001:db8::/48") get this per-IP limit
    # instead; the per-account limits stay the same for everyone.
    login_school_nets: list[str] = field(default_factory=lambda: _list("FAIRPLAY_LOGIN_SCHOOL_NETS"))
    login_school_ip_limit: int = field(default_factory=lambda: _int("FAIRPLAY_LOGIN_SCHOOL_IP_LIMIT", 6000))
    # PBKDF2 checks running at once per worker process (they are CPU-bound threads; more
    # than about one per core only slows the other requests down during a sign-in rush).
    login_hash_slots: int = field(default_factory=lambda: _int("FAIRPLAY_LOGIN_HASH_SLOTS", 4))
    # API requests in progress at once per worker process; more wait in line (app/admission.py).
    max_inflight: int = field(default_factory=lambda: _int("FAIRPLAY_MAX_INFLIGHT", 128))
    # Students see their grades and past exams only when this is on (FAIRPLAY_STUDENT_GRADES).
    student_grades: bool = field(default_factory=lambda: _flag("FAIRPLAY_STUDENT_GRADES", False))
    # The earlier student screens (class joining, course cards, grades list) stay in the web build
    # but are shown only when this is on (listed in /api/health features).
    legacy_student_ui: bool = field(default_factory=lambda: _flag("FAIRPLAY_LEGACY_STUDENT_UI", False))
    static_dir: str = field(default_factory=lambda: os.environ.get(
        "PROCTORA_STATIC_DIR",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")))
    sql_echo: bool = field(default_factory=lambda: os.environ.get("SQL_ECHO") == "1")
    # 2026-10-13: optional parts, off unless listed (FAIRPLAY_FEATURES=scan,lms,practice).
    # Off = their API routes are not mounted and the teacher UI hides them.
    features: list[str] = field(default_factory=lambda: [
        f for f in _list("FAIRPLAY_FEATURES") if f in OPTIONAL_FEATURES])
    # slice 6: question images and other uploads (local disk; S3-compatible later)
    uploads_dir: str = field(default_factory=lambda: os.environ.get(
        "FAIRPLAY_UPLOADS_DIR",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "uploads")))

    # S2 (2026-10-20): oldest app version allowed to start or keep beating, per platform.
    # "" = no minimum. Compared numerically by dot-separated parts (app_version.py).
    min_app_version_mac: str = field(default_factory=lambda: os.environ.get("MIN_APP_VERSION_MAC", "").strip())
    min_app_version_windows: str = field(
        default_factory=lambda: os.environ.get("MIN_APP_VERSION_WINDOWS", "").strip())
    min_app_version_ipad: str = field(default_factory=lambda: os.environ.get("MIN_APP_VERSION_IPAD", "").strip())

    # Oldest app version that may start a Canvas exam, per platform. "" = Canvas exams are
    # not supported on that platform yet (the start is refused). An env var set to "" turns
    # the Mac default off.
    canvas_min_version_mac: str = field(
        default_factory=lambda: _canvas_min_version("FAIRPLAY_CANVAS_MIN_VERSION_MAC", "0.7.2"))
    canvas_min_version_windows: str = field(
        default_factory=lambda: _canvas_min_version("FAIRPLAY_CANVAS_MIN_VERSION_WINDOWS", ""))
    canvas_min_version_ipad: str = field(
        default_factory=lambda: _canvas_min_version("FAIRPLAY_CANVAS_MIN_VERSION_IOS", ""))

    # Optional: this server's public address, so integrations (app.integrations) can send
    # absolute integrity-report links. Unset: the links are sent as paths (/teacher/exams/...).
    public_url: str = field(default_factory=lambda: os.environ.get("FAIRPLAY_PUBLIC_URL", "").strip().rstrip("/"))

    # Homework sign-in (app/integrations/homework_login.py): "" = off, "dkuoj" = the DKU OJ web
    # sign-in in the app's web view. HOMEWORK_LOGIN_HOSTS: extra sign-in hosts the web view may
    # open (comma list); HOMEWORK_LOGIN_RESUME_PATHS: paths on the provider the web view may
    # return to after the sign-in.
    homework_login: str = field(default_factory=lambda: os.environ.get("FAIRPLAY_HOMEWORK_LOGIN", "").strip().lower())
    homework_login_hosts: list[str] = field(default_factory=lambda: _list("HOMEWORK_LOGIN_HOSTS"))
    homework_login_resume_paths: list[str] = field(
        default_factory=lambda: _list("HOMEWORK_LOGIN_RESUME_PATHS") or ["/"])
    # HOMEWORK_WEBTEST_SITES: JSON list of {"id","label","url","hosts"?,"box"?}; the sites a student
    # may open in the restricted web window after the homework sign-in. "" = off.
    homework_webtest_sites: str = field(default_factory=lambda: os.environ.get("HOMEWORK_WEBTEST_SITES", "").strip())

    def canvas_min_version(self, platform: str | None) -> str | None:
        p = (platform or "").strip().lower()
        v = {"mac": self.canvas_min_version_mac, "windows": self.canvas_min_version_windows,
             "ipad": self.canvas_min_version_ipad, "ios": self.canvas_min_version_ipad}.get(p, "")
        return v or None

    def min_app_version(self, platform: str | None) -> str | None:
        v = {"mac": self.min_app_version_mac, "windows": self.min_app_version_windows,
             "ipad": self.min_app_version_ipad}.get(
            (platform or "").strip().lower(), "")
        return v or None

    def feature(self, name: str) -> bool:
        return name in self.features

    def login_limit_for(self, ip: str) -> int:
        """Sign-in tries per minute allowed from this address (audit S-05, perf 0.6.2)."""
        if not self.login_school_nets or not ip:
            return self.login_ip_limit
        nets = getattr(self, "_school_nets", None)
        if nets is None:
            import ipaddress
            nets = []
            for n in self.login_school_nets:
                try:
                    nets.append(ipaddress.ip_network(n, strict=False))
                except ValueError:
                    log.warning("FAIRPLAY_LOGIN_SCHOOL_NETS: ignoring %r (not a network)", n)
            self._school_nets = nets
        try:
            import ipaddress
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return self.login_ip_limit
        if any(addr.version == n.version and addr in n for n in nets):
            return max(self.login_ip_limit, self.login_school_ip_limit)
        return self.login_ip_limit

    @property
    def demo_logins(self) -> bool:
        """Demo accounts are created and the login page may show their passwords."""
        return self.dev or self.seed_demo

    def check_secure(self) -> None:
        """Startup checks. Raises RuntimeError instead of running with public defaults."""
        if self.secret in ("", DEV_SECRET) or len(self.secret) < 16:
            if not self.dev:
                raise RuntimeError(
                    "PROCTORA_SECRET is missing, too short (<16 chars) or the old public default. "
                    "Set it to a long random string (python3 -c 'import secrets;print(secrets.token_urlsafe(48))'), "
                    "or set FAIRPLAY_DEV=1 for a local demo.")
            self.secret = _dev_secret(os.path.dirname(self.uploads_dir))
            log.warning("FAIRPLAY_DEV=1: using a random local secret stored in data/secret.key")
        if self.admin_test_code and len(self.admin_test_code) < MIN_ADMIN_TEST_CODE:
            raise RuntimeError(
                f"FAIRPLAY_ADMIN_TEST_CODE is too short ({len(self.admin_test_code)} chars). Use at least "
                f"{MIN_ADMIN_TEST_CODE} random characters, or unset it.")
        if not self.app_keys:
            if self.dev:
                self.app_keys = [DEV_APP_KEY]
                log.warning("FAIRPLAY_DEV=1: accepting the public dev app key; set FAIRPLAY_APP_KEY for real exams")
            elif self.require_app:
                raise RuntimeError(
                    "FAIRPLAY_APP_KEY is not set. Use the same value you pass to mac/build.sh "
                    "(or FAIRPLAY_REQUIRE_APP=0 to allow exams in a normal browser).")
        elif DEV_APP_KEY in self.app_keys and not self.dev:
            raise RuntimeError("FAIRPLAY_APP_KEY contains the public dev key")
        if not self.require_app:
            log.warning("FAIRPLAY_REQUIRE_APP=0: exams can be taken in a normal browser")
        need = max(1, self.replicas) * max(1, self.workers) * (self.db_pool_size + self.db_max_overflow)
        if self.db_max_connections and need > self.db_max_connections:
            raise RuntimeError(
                f"Database pools can open {need} connections ({self.replicas} containers x "
                f"{self.workers} workers x {self.db_pool_size}+{self.db_max_overflow}) but Postgres "
                f"allows {self.db_max_connections}. Lower DB_POOL_SIZE / DB_MAX_OVERFLOW.")
        if not self.redis_url and not self.dev and (self.workers > 1 or not self.single_process):
            raise RuntimeError(
                "REDIS_URL is not set. With more than one worker the in-memory store breaks "
                "exam-cache invalidation, single-use challenges, code lockouts and the beat "
                "replay guard. Set REDIS_URL, or run ONE process with FAIRPLAY_SINGLE_PROCESS=1 "
                "(and WEB_CONCURRENCY=1).")
        if self.homework_login:
            from .integrations import homework_login
            homework_login.check(self)
            parse_webtest_sites(self.homework_webtest_sites, self.dev)
        self.warn_insecure()

    def warn_insecure(self) -> None:
        if not self.redis_url:
            log.warning("REDIS_URL is not set; using in-memory store (single process, dev only)")


def _dev_secret(data_dir: str) -> str:
    """Random secret kept in <data>/secret.key so dev tokens survive restarts."""
    import secrets as _secrets
    path = os.path.join(data_dir or ".", "secret.key")
    try:
        with open(path) as f:
            v = f.read().strip()
            if len(v) >= 32:
                return v
    except OSError:
        pass
    v = _secrets.token_urlsafe(48)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(v)
    except OSError:
        log.warning("could not store %s; dev tokens end when the server stops", path)
    return v
