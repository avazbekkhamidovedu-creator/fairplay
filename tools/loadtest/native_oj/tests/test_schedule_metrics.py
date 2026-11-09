import metrics
import schedule


def test_interval_bounds():
    for r, want in ((0.0, 16.0), (0.5, 20.0), (0.999999, 24.0)):
        assert abs(schedule.RenewSchedule(lambda r=r: r).interval() - want) < 1e-3
    s = schedule.RenewSchedule()
    assert all(16.0 <= s.interval() <= 24.0 for _ in range(5000))


def test_backoff_steps_cap_and_full_jitter():
    s = schedule.RenewSchedule(lambda: 1.0)
    assert [s.next(503) for _ in range(6)] == [2, 4, 8, 16, 16, 16]
    s = schedule.RenewSchedule(lambda: 0.5)
    assert s.next(None) == 1.0 and s.next(429) == 2.0
    s = schedule.RenewSchedule()
    waits = [s.next(500) for _ in range(200)]
    assert max(waits) <= 16 and min(waits) >= 0


def test_retry_after_floor_and_cap_and_reset():
    s = schedule.RenewSchedule(lambda: 0.0)
    assert s.next(429, retry_after=7) == 7
    assert s.next(429, retry_after=500) == 30
    assert s.next(200) == 16.0 and s.failures == 0
    assert schedule.parse_retry_after("12") == 12 and schedule.parse_retry_after("-1") is None
    assert schedule.parse_retry_after("Wed, 21 Oct") is None


def test_other_status_keeps_pace_and_failures():
    s = schedule.RenewSchedule(lambda: 0.5)
    s.next(503)
    assert s.next(403) == 20.0 and s.failures == 1


def test_percentiles():
    v = list(range(1, 101))
    assert metrics.percentile(v, 50) == 50 and metrics.percentile(v, 95) == 95 and metrics.percentile(v, 99) == 99
    assert metrics.percentile([], 50) is None


def make(clients=5000, renew_ms=100, n=1000, errors=0, n429=0, lease=0):
    m = metrics.Metrics()
    for i in range(n):
        m.record("renew", 200, renew_ms / 1000, i * 0.01)
    for i in range(errors):
        m.record("renew", 503, 0.01, 100 + i)
    for i in range(n429):
        m.record("renew", 429, 0.01, 200 + i)
    for c in range(lease):
        m.lease_expired(c)
    return m.summary(clients, {"renew": ("200",)})


def test_verdict_pass_and_each_failure():
    assert make()["verdict"]["result"] == "PASS"
    assert make(renew_ms=1000)["verdict"]["result"] == "FAIL"
    assert make(renew_ms=999)["verdict"]["result"] == "PASS"
    assert make(lease=1)["verdict"]["result"] == "FAIL"
    assert make(errors=1, n=10000)["verdict"]["result"] == "PASS"
    assert make(errors=10, n=1000)["verdict"]["result"] == "FAIL"
    assert make(n429=1, n=100000)["verdict"]["result"] == "FAIL"

def test_labels_by_size():
    assert make()["verdict"]["label"] == "PASS: 5,000-client bar met"
    assert make(clients=1000)["verdict"]["label"] == "PASS at 1000 clients (this is NOT a 5,000-client proof)"
    assert make(clients=6000)["verdict"]["label"] == "PASS: 5,000-client bar met"
    small = make(clients=1000, renew_ms=2000)["verdict"]
    assert small["label"].startswith("FAIL at 1000 clients: ") and "renew p99" in small["label"]
    assert make(clients=100, n429=1)["verdict"]["result"] == "FAIL"
    assert make(clients=100, lease=2)["verdict"]["result"] == "FAIL"


def test_escrow_only_is_not_a_fail():
    m = metrics.Metrics()
    for i in range(50):
        m.record("escrow", 201, 0.01, i)
    v = m.summary(500, {"escrow": ("201",)})["verdict"]
    assert v["result"] == "ESCROW_ONLY" and v["label"] == "escrow-only: renew thresholds not evaluated"
    m.record("escrow", 500, 0.01, 99)
    for i in range(10):
        m.record("escrow", 429, 0.01, 100 + i)
    assert m.summary(500, {"escrow": ("201",)})["verdict"]["result"] == "FAIL"


def test_hold_fails_and_empty_run_fails():
    m = metrics.Metrics()
    for i in range(100):
        m.record("renew", 200, 0.01, i)
    m.hold(3)
    v = m.summary(5000, {"renew": ("200",)})["verdict"]
    assert v["result"] == "FAIL" and any("hold signal" in r for r in v["reasons"])
    assert metrics.Metrics().summary(5000)["verdict"]["result"] == "FAIL"
