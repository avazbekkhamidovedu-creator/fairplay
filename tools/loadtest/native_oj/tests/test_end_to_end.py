import asyncio
import json
import threading
import time

import metrics
import run
import safety
from client import Config, Runner
from fake_server import APP_KEY, FakeServer

SCALE = 0.02


def cfg_for(server, **kw):
    base = dict(clients=20, fairplay_url=server.url, oj_url=server.url, contest="exam1", app_key=APP_KEY,
                ingress_key="i", users=[("u%d" % i, "p") for i in range(500)], start_window=20.0,
                duration=100.0, time_scale=SCALE, seed=7, timeout=5.0)
    base.update(kw)
    return Config(**base)


async def go(server, **kw):
    cfg = cfg_for(server, **kw)
    return await run.execute(cfg)


def with_server(coro_fn, **server_kw):
    async def inner():
        server = await FakeServer(**server_kw).start()
        try:
            return server, await coro_fn(server)
        finally:
            await server.stop()
    return asyncio.run(inner())


def test_full_flow_counts_and_metrics():
    server, s = with_server(go)
    ep = s["endpoints"]
    assert ep["escrow"]["status"] == {"201": 20} and len(server.locks) == 20
    assert ep["claim"]["status"] == {"200": 20}
    assert ep["page"]["requests"] == 60 and ep["hand_in"]["requests"] == 20
    assert ep["renew"]["requests"] >= 20 * 3 + 20 * 3 and ep["renew"]["rps"] > 0
    assert s["error_requests"] == 0 and s["count_429"] == 0 and s["clients_lease_expired"] == 0
    assert s["clients_finished"] == 20 and server.ingress_ok > 0
    assert ep["renew"]["p99_ms"] >= ep["renew"]["p50_ms"]


def test_renew_cadence_jitter_bounds_and_handin_followups():
    server, s = with_server(lambda sv: go(sv, clients=6, duration=400.0, start_window=2.0))
    gaps, tails = [], []
    for times in server.renew_times.values():
        diffs = [b - a for a, b in zip(times, times[1:])]
        steady, tail = diffs[:-3], diffs[-3:]
        assert len(tail) == 3
        gaps += [d / SCALE for d in steady]
        tails.append([d / SCALE for d in tail])
    assert len(gaps) > 60
    assert min(gaps) > 16 * 0.9 and max(gaps) < 24 * 1.15
    for t in tails:
        assert 1.8 < t[1] < 6
        assert 2.8 < t[2] < 8


def test_latency_is_measured():
    _, s = with_server(lambda sv: go(sv, clients=5, duration=60.0), renew_latency=0.05)
    r = s["endpoints"]["renew"]
    assert 45 <= r["p50_ms"] < 400 and r["p99_ms"] >= r["p50_ms"]


def test_429_and_503_counted_and_backoff_recovers():
    server, s = with_server(lambda sv: go(sv, clients=20, duration=300.0), p429=0.2, p503=0.1, retry_after=1)
    r = s["endpoints"]["renew"]["status"]
    assert r.get("429", 0) > 0 and r.get("503", 0) > 0 and r["200"] > 0
    assert s["count_429"] == r["429"]
    assert s["verdict"]["result"] == "FAIL"
    assert s["error_rate"] > 0.1


def test_retry_after_is_honoured():
    server, s = with_server(lambda sv: go(sv, clients=1, duration=400.0, start_window=0.0), p429=1.0, retry_after=25)
    times = next(iter(server.renew_times.values()))
    gaps = [(b - a) / SCALE for a, b in zip(times, times[1:])][:-3]
    assert len(gaps) > 5 and min(gaps) >= 24.0


def test_simulated_loss_is_separate_from_errors():
    server, s = with_server(lambda sv: go(sv, clients=30, duration=200.0, loss=0.05, seed=3))
    assert sum(s["simulated_timeouts"].values()) > 0
    assert s["error_requests"] == 0


def test_lease_expired_signal_counted():
    _, s = with_server(lambda sv: go(sv, clients=5, duration=60.0), lease_state="expired")
    assert s["clients_lease_expired"] == 5


def test_ingress_headers_are_sent_on_native_calls():
    async def f(sv):
        return await run.execute(cfg_for(sv, oj_mode="ingress-bypass", ingress_key=""))
    server, s = with_server(f)
    assert s["endpoints"]["claim"]["status"] == {"200": 20}


def test_plain_mode_only_pages():
    server, s = with_server(lambda sv: go(sv, oj_mode="plain", fairplay_url="", duration=60.0))
    assert "claim" not in s["endpoints"] and "renew" not in s["endpoints"]
    assert s["endpoints"]["login_page"]["requests"] == 20 and s["endpoints"]["login"]["requests"] == 20


def test_bad_app_key_shows_403s():
    _, s = with_server(lambda sv: go(sv, clients=5, oj_url="", app_key="wrong", duration=10.0))
    assert s["endpoints"]["escrow"]["status"] == {"403": 5} and s["error_requests"] == 5


def test_timeout_is_counted(monkeypatch):
    _, s = with_server(lambda sv: go(sv, clients=2, duration=60.0, timeout=0.05), renew_latency=0.3)
    assert s["endpoints"]["renew"]["status"].get("timeout", 0) > 0


def test_json_writer(tmp_path):
    out = tmp_path / "m.json"
    metrics.write_json({"a": 1}, str(out))
    assert json.loads(out.read_text()) == {"a": 1}


def flipping_resolver(flip_after):
    calls = {"n": 0}
    lock = threading.Lock()

    def resolve(host):
        if host == "dku.speakupai.uz":
            return ["198.51.100.7"]
        with lock:
            calls["n"] += 1
            return ["10.200.31.64"] if calls["n"] > flip_after else ["192.0.2.10"]
    return resolve


def test_half_time_dns_recheck_aborts_run():
    async def f(sv):
        env = {"LOADTEST_ALLOWED_HOSTS": "stg.example.test"}
        guard = safety.make_guard(["http://stg.example.test"], env, flipping_resolver(1))
        guard()
        cfg = cfg_for(sv, clients=10, duration=2000.0, guard=guard, oj_url="")
        try:
            await run.execute(cfg)
        except safety.Refused as error:
            return str(error)
    t0 = time.monotonic()
    _, message = with_server(f)
    assert "resolves to 10.200.31.64" in message
    assert time.monotonic() - t0 < 30



def test_guard_runs_at_half_of_steady_state():
    seen = []

    async def f(sv):
        cfg = cfg_for(sv, clients=2, duration=400.0, start_window=2.0, oj_url="",
                      guard=lambda: seen.append(time.monotonic()))
        start = time.monotonic()
        await run.execute(cfg)
        return start
    _, start = with_server(f)
    assert len(seen) == 1
    assert abs((seen[0] - start) / SCALE - (2.0 + 200.0)) < 15


def test_main_aborts_when_dns_flipped_at_start(capsys):
    env = {"LOADTEST_ALLOWED_HOSTS": "stg.example.test", "LOADTEST_APP_KEY": "k"}
    base = flipping_resolver(1)
    code = run.main(["--clients", "1", "--i-understand-this-is-staging", "--yes",
                     "--fairplay-url", "http://stg.example.test"], env, base)
    assert code == 3 and "ABORTED" in capsys.readouterr().err


def test_run_connects_only_through_pins_and_ignores_proxy_env(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")

    async def f(sv):
        cfg = cfg_for(sv, clients=3, duration=40.0, oj_url="")
        cfg.fairplay_url = f"http://stg.example.test:{sv.port}"
        cfg.pins = {("stg.example.test", sv.port): ["127.0.0.1"]}
        runner = Runner(cfg, metrics.Metrics())
        await runner.run()
        assert runner.http.trust_env is False
        return runner.m.summary(3, {"escrow": ("201",)})
    server, s = with_server(f)
    assert s["endpoints"]["escrow"]["status"] == {"201": 3} and len(server.locks) == 3


def test_run_refuses_unpinned_host():
    async def f(sv):
        cfg = cfg_for(sv, clients=2, duration=10.0, oj_url="")
        cfg.pins = {("elsewhere.test", sv.port): ["127.0.0.1"]}
        return await run.execute(cfg)
    _, s = with_server(f)
    assert set(s["endpoints"]["escrow"]["status"]) == {"error"}
