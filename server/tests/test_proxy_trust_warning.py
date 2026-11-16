"""Pilot 0.8.8: startup warning for forwarded-allow-ips=* on a public listener."""
from app.config import proxy_trust_warning


def test_warns_for_star_on_a_public_listener():
    argv = ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--forwarded-allow-ips=*"]
    assert "fake its IP" in proxy_trust_warning(argv, {})
    assert proxy_trust_warning(["uvicorn", "--host=0.0.0.0", "--forwarded-allow-ips", "*"], {})
    assert proxy_trust_warning(["uvicorn"], {"UVICORN_HOST": "0.0.0.0", "FORWARDED_ALLOW_IPS": "*"})


def test_quiet_when_local_or_not_star():
    assert proxy_trust_warning(["uvicorn", "--host", "127.0.0.1", "--forwarded-allow-ips=*"], {}) is None
    assert proxy_trust_warning(["uvicorn", "--host", "0.0.0.0", "--forwarded-allow-ips=127.0.0.1"], {}) is None
    assert proxy_trust_warning(["uvicorn"], {}) is None
