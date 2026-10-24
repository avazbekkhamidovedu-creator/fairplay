"""DKU OJ connection settings. Read from the environment today (DKUOJ_URL, DKUOJ_TOKEN,
DKUOJ_SYNC_SECONDS, DKUOJ_DELIVERY_RPS, DKUOJ_ALLOW_INSECURE); only this module reads them,
so a per-organization settings table can replace the environment later without touching
the core.

Security review 2026-10-24:
- DKUOJ_TOKEN may hold two comma-separated tokens while the shared token is rotated
  (docs/dkuoj-bridge-v2.md §1): FairPlay signs with the FIRST and accepts a call signed
  with ANY of them (each token gives its own pair of direction keys).
- a DKUOJ_URL with plain http:// to another machine is refused at startup (the token's
  signatures don't hide the answers: rosters, packages, grades travel in clear text),
  unless FAIRPLAY_DEV=1 or DKUOJ_ALLOW_INSECURE=1. http://localhost and other loopback
  addresses are fine (both servers on one machine, or a TLS proxy in front)."""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

MAX_TOKENS = 2


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def parse_tokens(raw: str) -> list[str]:
    return [t.strip() for t in (raw or "").split(",") if t.strip()]


def is_loopback(host: str) -> bool:
    host = (host or "").strip("[]").lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass
class DkuojConfig:
    url: str = ""
    token: str = ""               # the token FairPlay signs with (the first of `tokens`)
    tokens: list[str] = field(default_factory=list)    # every token a DKU OJ call may be signed with
    sync_seconds: int = 120
    delivery_rps: int = 10
    public_url: str = ""          # FairPlay's own address (FAIRPLAY_PUBLIC_URL), for report links

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.token)

    def __post_init__(self):
        if not self.tokens and self.token:
            self.tokens = [self.token]
        if self.tokens and not self.token:
            self.token = self.tokens[0]

    @property
    def verify_tokens(self) -> list[str]:
        return list(self.tokens) or ([self.token] if self.token else [])

    @classmethod
    def from_env(cls, public_url: str = "") -> "DkuojConfig":
        tokens = parse_tokens(os.environ.get("DKUOJ_TOKEN", ""))
        return cls(url=os.environ.get("DKUOJ_URL", "").strip().rstrip("/"),
                   token=tokens[0] if tokens else "", tokens=tokens,
                   sync_seconds=_int("DKUOJ_SYNC_SECONDS", 120),
                   delivery_rps=_int("DKUOJ_DELIVERY_RPS", 10),
                   public_url=public_url)

    def check(self, dev: bool = False) -> None:
        """Startup checks (RuntimeError with what to change)."""
        if len(self.tokens) > MAX_TOKENS:
            raise RuntimeError(f"DKUOJ_TOKEN holds {len(self.tokens)} tokens; at most {MAX_TOKENS} "
                               "(the new one first, the old one second, while DKU OJ is switched over).")
        if not self.url:
            return
        parts = urlsplit(self.url)
        scheme = parts.scheme.lower()
        if scheme not in ("http", "https") or not parts.hostname:
            raise RuntimeError(f"DKUOJ_URL {self.url!r} is not an http(s) address like https://oj.example.edu")
        if scheme == "http" and not is_loopback(parts.hostname) and not (dev or _flag("DKUOJ_ALLOW_INSECURE")):
            raise RuntimeError(
                f"DKUOJ_URL {self.url!r} uses plain http:// to another machine: rosters, exam "
                "packages and grades would cross the network unencrypted. Use https://, or set "
                "DKUOJ_ALLOW_INSECURE=1 (a test network only).")
