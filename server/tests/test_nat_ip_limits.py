"""Pilot 0.8.8: per-address limits are higher for a school network (FAIRPLAY_LOGIN_SCHOOL_NETS),
the global and per-account caps stay, and a client-claimed device id is never a key."""
import pytest

from app import routes_homework as rh
from app.config import Settings

from .test_homework_login import START, fake, hw, oj_env  # noqa: F401  (fixtures)


def test_ip_limit_helper():
    s = Settings(login_school_nets=["10.1.0.0/16", "2001:db8::/48"])
    assert s.ip_limit("10.1.2.3", 20, 600) == 600
    assert s.ip_limit("2001:db8::7", 20, 600) == 600
    assert s.ip_limit("203.0.113.9", 20, 600) == 20
    assert s.ip_limit("", 20, 600) == 20 and s.ip_limit("unknown", 20, 600) == 20
    assert s.ip_limit("10.1.2.3", 20, 5) == 20            # never below the base limit
    assert Settings(login_school_nets=[]).ip_limit("10.1.2.3", 20, 600) == 20


@pytest.mark.parametrize("ip,school,expect_429_at", [("10.1.2.3", True, None), ("198.51.100.4", False, 21)])
def test_homework_start_limit_depends_on_the_network(hw, monkeypatch, ip, school, expect_429_at):
    hw.app.state.settings.login_school_nets = ["10.1.0.0/16"]
    monkeypatch.setattr(rh, "client_ip", lambda request: ip)
    first_429 = None
    for i in range(1, 31):
        # a device id in the body must not change anything
        r = hw.post(START, json={"device_id": f"dev-{i}"})
        if r.status_code == 429 and first_429 is None:
            first_429 = i
    assert first_429 == expect_429_at


def test_global_cap_still_applies_to_a_school_network(hw, monkeypatch):
    hw.app.state.settings.login_school_nets = ["10.1.0.0/16"]
    monkeypatch.setattr(rh, "client_ip", lambda request: "10.1.2.3")
    monkeypatch.setattr(rh, "START_GLOBAL_PER_MIN", 3)
    codes = [hw.post(START, json={}).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
