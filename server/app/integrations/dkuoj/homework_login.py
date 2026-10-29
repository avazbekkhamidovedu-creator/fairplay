"""DKU OJ as the homework sign-in provider (integrations/homework_login.py).

The web view opens <DKUOJ_PUBLIC_URL>/fairplay/app-login/?state=..&challenge=.. where the
student signs in the usual DKU OJ way (Duke NetID, Shibboleth, Duo). DKU OJ then mints a
one-time ticket and sends the view to <DKUOJ_PUBLIC_URL>/fairplay/app-login/done#ticket=..&state=..
FairPlay trades the ticket with the signed call POST app-login/redeem {ticket, verifier}:
200 {netid, first_name, last_name, email}, 404 unknown / used / expired, 409 the verifier
does not match the challenge the sign-in started with.

DKUOJ_PUBLIC_URL (the address students open, not the server-to-server DKUOJ_URL) is read
here, the adapter's own settings."""
from __future__ import annotations

import logging
import os
from urllib.parse import urlencode, urlsplit

from ..homework_login import HomeworkError, Person, Provider
from .client import DkuojClient, OjError
from .config import DkuojConfig, is_loopback

log = logging.getLogger("proctora.dkuoj")

START_PATH = "/fairplay/app-login/"
FINISH_PATH = "/fairplay/app-login/done"
REDEEM_TIMEOUT_S = 10.0


def public_url() -> str:
    return os.environ.get("DKUOJ_PUBLIC_URL", "").strip().rstrip("/")


class DkuojHomework(Provider):
    id = "dkuoj"
    label = "DKU OJ"

    def check(self, settings) -> None:
        cfg = DkuojConfig.from_env()
        if not cfg.enabled:
            raise RuntimeError("FAIRPLAY_HOMEWORK_LOGIN=dkuoj needs the DKU OJ bridge (DKUOJ_URL and DKUOJ_TOKEN).")
        url = public_url()
        parts = urlsplit(url)
        try:
            host = parts.hostname
        except ValueError:
            host = None
        if not host or parts.scheme.lower() not in ("http", "https") or parts.query or parts.fragment \
                or parts.username or parts.password:
            raise RuntimeError("FAIRPLAY_HOMEWORK_LOGIN=dkuoj needs DKUOJ_PUBLIC_URL, the https address "
                               "students open DKU OJ at (like https://oj.example.edu).")
        if parts.scheme.lower() != "https" and not (getattr(settings, "dev", False) and is_loopback(host)):
            raise RuntimeError(f"DKUOJ_PUBLIC_URL {url!r} must be an https:// address "
                               "(http://localhost is accepted only with FAIRPLAY_DEV=1).")

    def start(self, settings, state: str, challenge: str) -> dict:
        base = public_url()
        host = urlsplit(base).netloc.lower()
        hosts = [host] + [h.lower() for h in settings.homework_login_hosts if h.lower() != host]
        return {"start_url": f"{base}{START_PATH}?{urlencode({'state': state, 'challenge': challenge})}",
                "finish_url": f"{base}{FINISH_PATH}", "allowed_hosts": hosts}

    def _client(self) -> DkuojClient:
        from .source import SOURCE
        return DkuojClient.from_config(SOURCE.config)

    async def redeem(self, ticket: str, verifier: str) -> Person:
        try:
            async with self._client() as client:
                _, body = await client.redeem_app_login(ticket, verifier, timeout=REDEEM_TIMEOUT_S)
            if body is None:
                raise HomeworkError(400)
        except OjError as err:
            log.warning("DKU OJ ticket exchange failed: %s", err.message)
            raise HomeworkError(503) from None
        netid = body.get("netid")
        if not isinstance(netid, str) or not netid.strip():
            log.warning("DKU OJ ticket exchange answered without a netid")
            raise HomeworkError(503)
        return Person(netid.strip(), str(body.get("first_name") or ""), str(body.get("last_name") or ""),
                      str(body.get("email") or ""))

    async def has_classes(self, netid: str) -> bool:
        try:
            async with self._client() as client:
                body = await client.student_classes(netid, timeout=REDEEM_TIMEOUT_S)
        except OjError as err:
            if err.status == 404:
                return False
            log.warning("DKU OJ class lookup failed: %s", err.message)
            raise HomeworkError(503) from None
        return bool(body.get("classes"))


PROVIDER = DkuojHomework()
