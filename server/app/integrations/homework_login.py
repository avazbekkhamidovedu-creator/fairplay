"""Homework sign-in: a student signs in on a school system's own web page inside the app's
web view, and FairPlay trades the one-time ticket that page hands back for a "homework"
token (security.HOMEWORK_SCOPE). Nothing school-specific lives here: a provider (today
only DKU OJ, integrations/dkuoj/homework_login.py) builds the start address, redeems the
ticket and tells whether the person may sign in. FAIRPLAY_HOMEWORK_LOGIN picks the
provider; empty = off."""
from __future__ import annotations

from dataclasses import dataclass


class HomeworkError(Exception):
    """status 400 = ticket unknown / used / expired / not the one this sign-in started;
    503 = the provider could not be reached or answered something unusable."""

    def __init__(self, status: int):
        super().__init__(f"homework sign-in provider answered {status}")
        self.status = status


@dataclass
class Person:
    netid: str
    first_name: str
    last_name: str
    email: str = ""


class Provider:
    id = ""
    label = ""

    def check(self, settings) -> None:
        """Startup check; RuntimeError says what to change."""

    def start(self, settings, state: str, challenge: str) -> dict:
        """{"start_url", "finish_url", "allowed_hosts"} for one sign-in attempt."""
        raise NotImplementedError

    async def redeem(self, ticket: str, verifier: str) -> Person:
        raise NotImplementedError

    async def has_classes(self, netid: str) -> bool:
        raise NotImplementedError


def _providers() -> dict[str, Provider]:
    from .dkuoj.homework_login import PROVIDER as dkuoj
    return {dkuoj.id: dkuoj}


def active(settings) -> Provider | None:
    return _providers().get(getattr(settings, "homework_login", "") or "")


def check(settings) -> None:
    name = getattr(settings, "homework_login", "") or ""
    if not name:
        return
    provider = _providers().get(name)
    if provider is None:
        raise RuntimeError(f"FAIRPLAY_HOMEWORK_LOGIN={name!r} is not a known provider "
                           f"({', '.join(sorted(_providers()))}), or leave it empty.")
    provider.check(settings)


def health(settings) -> dict | None:
    provider = active(settings)
    return {"provider": provider.id, "label": provider.label} if provider else None
