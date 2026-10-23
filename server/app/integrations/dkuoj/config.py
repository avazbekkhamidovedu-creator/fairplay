"""DKU OJ connection settings. Read from the environment today (DKUOJ_URL, DKUOJ_TOKEN,
DKUOJ_SYNC_SECONDS, DKUOJ_DELIVERY_RPS); only this module reads them, so a per-
organization settings table can replace the environment later without touching the core."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class DkuojConfig:
    url: str = ""
    token: str = ""
    sync_seconds: int = 120
    delivery_rps: int = 10
    public_url: str = ""          # FairPlay's own address (FAIRPLAY_PUBLIC_URL), for report links

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.token)

    @classmethod
    def from_env(cls, public_url: str = "") -> "DkuojConfig":
        return cls(url=os.environ.get("DKUOJ_URL", "").strip().rstrip("/"),
                   token=os.environ.get("DKUOJ_TOKEN", "").strip(),
                   sync_seconds=_int("DKUOJ_SYNC_SECONDS", 120),
                   delivery_rps=_int("DKUOJ_DELIVERY_RPS", 10),
                   public_url=public_url)
