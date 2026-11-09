import pytest

import run
import safety

ENV = {"LOADTEST_ALLOWED_HOSTS": "staging.example.test,127.0.0.1:9000,dku.speakupai.uz,10.200.31.64,sneaky.test",
       "LOADTEST_APP_KEY": "k"}
TABLE = {"staging.example.test": ["192.0.2.10"], "sneaky.test": ["10.200.30.218"],
         "dku.speakupai.uz": ["198.51.100.7"], "alias.test": ["198.51.100.7"], "mapped.test": ["::ffff:10.200.31.64"]}


def resolver(host):
    if host in TABLE:
        return TABLE[host]
    raise OSError("no such host")


def check(url, env=ENV):
    return safety.check_target(url, env, resolver)


def test_allowlisted_staging_is_accepted():
    assert check("https://staging.example.test")[2] == ["192.0.2.10"]
    assert check("http://127.0.0.1:9000")[:2] == ("127.0.0.1", 9000)


def test_not_in_allowlist_and_port_mismatch():
    with pytest.raises(safety.Refused):
        check("http://127.0.0.1:9001")
    with pytest.raises(safety.Refused):
        check("https://other.example.test")


def test_empty_or_missing_allowlist_refuses():
    for env in ({}, {"LOADTEST_ALLOWED_HOSTS": ""}, {"LOADTEST_ALLOWED_HOSTS": " , "}):
        with pytest.raises(safety.Refused):
            check("https://staging.example.test", env)


def test_wildcards_refused():
    with pytest.raises(safety.Refused):
        check("https://staging.example.test", {"LOADTEST_ALLOWED_HOSTS": "*"})


@pytest.mark.parametrize("url", ["https://dku.speakupai.uz", "https://dku.speakupai.uz.", "http://10.200.31.64",
                                 "http://10.200.30.218:8000", "http://[::ffff:10.200.31.64]/"])
def test_live_hosts_refused_even_if_allowlisted(url):
    env = dict(ENV, LOADTEST_ALLOWED_HOSTS=ENV["LOADTEST_ALLOWED_HOSTS"] + ",10.200.30.218,[::ffff:10.200.31.64]")
    with pytest.raises(safety.Refused):
        check(url, env)


def test_name_resolving_to_live_address_refused():
    with pytest.raises(safety.Refused, match="resolves to"):
        check("https://sneaky.test")


def test_name_resolving_to_what_the_live_name_resolves_to_refused():
    env = dict(ENV, LOADTEST_ALLOWED_HOSTS="alias.test,mapped.test")
    with pytest.raises(safety.Refused):
        check("https://alias.test", env)
    with pytest.raises(safety.Refused):
        check("https://mapped.test", env)


def test_unresolvable_refused_and_bad_urls():
    env = dict(ENV, LOADTEST_ALLOWED_HOSTS="nowhere.test")
    with pytest.raises(safety.Refused):
        check("https://nowhere.test", env)
    for url in ("ftp://staging.example.test", "https://u:p@staging.example.test", "staging.example.test"):
        with pytest.raises(safety.Refused):
            check(url)


def test_client_cap():
    assert safety.check_clients(6000) == 6000
    with pytest.raises(safety.Refused):
        safety.check_clients(6001)
    with pytest.raises(safety.Refused):
        safety.check_clients(0)


def args(*extra):
    return run.build_parser().parse_args(["--clients", "10", "--fairplay-url", "https://staging.example.test", *extra])


def test_staging_flag_required():
    with pytest.raises(safety.Refused, match="staging"):
        run.prepare(args(), ENV, resolver)
    cfg, targets = run.prepare(args("--i-understand-this-is-staging"), ENV, resolver)
    assert cfg.clients == 10 and targets


def test_main_refuses_without_network_and_prints_target(capsys):
    code = run.main(["--clients", "7000", "--i-understand-this-is-staging", "--fairplay-url", "https://staging.example.test"],
                    ENV, resolver)
    assert code == 2 and "cap" in capsys.readouterr().err
    code = run.main(["--clients", "10", "--i-understand-this-is-staging", "--fairplay-url", "http://10.200.31.64"],
                    ENV, resolver)
    assert code == 2


def test_countdown_is_five_seconds(monkeypatch, capsys):
    waited = []
    monkeypatch.setattr(run, "execute", lambda cfg: _done())
    code = run.main(["--clients", "1", "--i-understand-this-is-staging", "--fairplay-url", "https://staging.example.test"],
                    ENV, resolver, sleep=waited.append)
    out = capsys.readouterr().out
    assert waited == [5] and "192.0.2.10" in out and code in (0, 1)


async def _done():
    import metrics
    return metrics.Metrics().summary(1)


LIVE_FORMS = ["::ffff:10.200.31.64", "64:ff9b::0ac8:1f40", "2002:0ac8:1f40::1",
              "::10.200.31.64", "64:ff9b:1::0ac8:1f40", "::5efe:0ac8:1f40"]


def test_embedded_forms_map_back_to_live_ipv4():
    import ipaddress
    teredo_server = "2001:0:0ac8:1f40::1"
    teredo_client = "2001:0:4136:e378:8000:63bf:f537:e0bf"  # client 10.200.31.64 xor ffffffff = f537e0bf
    for text in LIVE_FORMS + [teredo_server, teredo_client]:
        assert ipaddress.IPv4Address("10.200.31.64") in safety.embedded_v4(ipaddress.ip_address(text)) \
            or ipaddress.IPv4Address("10.200.30.218") in safety.embedded_v4(ipaddress.ip_address(text)), text


@pytest.mark.parametrize("address", ["64:ff9b::0ac8:1f40", "64:ff9b::0ac8:1eda", "2002:0ac8:1f40::1",
                                     "2002:0ac8:1eda:1::1", "::ffff:10.200.30.218", "2001:0:0ac8:1f40::1",
                                     "2001:0:4136:e378:8000:63bf:f537:e125", "::5efe:0ac8:1f40",
                                     "64:ff9b:1::0ac8:1f40"])
def test_resolved_embedded_forms_refused(address):
    table = dict(TABLE, **{"v6.test": [address]})
    env = dict(ENV, LOADTEST_ALLOWED_HOSTS="v6.test")
    with pytest.raises(safety.Refused, match="live address"):
        safety.check_target("https://v6.test", env, lambda h: table[h])


def test_literal_embedded_forms_refused():
    env = dict(ENV, LOADTEST_ALLOWED_HOSTS="[64:ff9b::ac8:1f40],[2002:ac8:1f40::1]")
    for url in ("http://[64:ff9b::ac8:1f40]/", "http://[2002:ac8:1f40::1]/"):
        with pytest.raises(safety.Refused):
            check(url, env)


def test_unrelated_embedded_v4_is_fine():
    table = dict(TABLE, **{"ok6.test": ["64:ff9b::c000:20a", "2002:c000:20a::1"]})
    env = dict(ENV, LOADTEST_ALLOWED_HOSTS="ok6.test")
    assert safety.check_target("https://ok6.test", env, lambda h: table[h])[0] == "ok6.test"


def test_fail_closed_when_denied_name_does_not_resolve():
    def broken(host):
        if host == "dku.speakupai.uz":
            raise OSError("temporary failure")
        return ["192.0.2.10"]
    with pytest.raises(safety.Refused, match="deny list would be incomplete"):
        safety.check_target("https://staging.example.test", ENV, broken)
    with pytest.raises(safety.Refused, match="deny list would be incomplete"):
        safety.check_target("https://staging.example.test", ENV,
                            lambda h: [] if h == "dku.speakupai.uz" else ["192.0.2.10"])


def test_literal_ips_denied_even_when_dns_is_down():
    def down(host):
        raise OSError("no dns")
    for url in ("http://10.200.31.64", "http://10.200.30.218", "https://dku.speakupai.uz"):
        with pytest.raises(safety.Refused, match="never allowed"):
            safety.check_target(url, dict(ENV, LOADTEST_ALLOWED_HOSTS="10.200.31.64,10.200.30.218"), down)


def test_guard_compares_pinned_set():
    answers = {"stg.test": ["192.0.2.10", "192.0.2.11"]}
    res = lambda h: answers.get(h, ["198.51.100.7"])
    env = {"LOADTEST_ALLOWED_HOSTS": "stg.test"}
    url = "https://stg.test"
    _, _, addrs = safety.check_target(url, env, res)
    guard = safety.make_guard([url], env, res, {url: addrs})
    guard()
    answers["stg.test"] = ["192.0.2.11"]
    guard()
    answers["stg.test"] = ["192.0.2.10", "192.0.2.99"]
    with pytest.raises(safety.Refused, match="outside the pinned set"):
        guard()


def test_prepare_pins_the_checked_addresses():
    cfg, _ = run.prepare(args("--i-understand-this-is-staging"), ENV, resolver)
    assert cfg.pins == {("staging.example.test", 443): ["192.0.2.10"]}


class Capture:
    def __init__(self):
        self.seen = []

    async def handle_async_request(self, request):
        import httpx
        self.seen.append(request)
        return httpx.Response(200, request=request)

    async def aclose(self):
        pass


def test_pinned_transport_connects_by_ip_with_name_kept():
    import asyncio
    import httpx
    inner = Capture()
    t = safety.PinnedTransport(inner, {("stg.test", 443): ["192.0.2.10", "192.0.2.11"]})

    async def go():
        async with httpx.AsyncClient(transport=t, trust_env=False) as c:
            for _ in range(3):
                await c.get("https://stg.test/x")
            with pytest.raises(httpx.ConnectError):
                await c.get("https://other.test/x")
            with pytest.raises(httpx.ConnectError):
                await c.get("https://stg.test:8443/x")
    asyncio.run(go())
    assert [r.url.host for r in inner.seen] == ["192.0.2.10", "192.0.2.11", "192.0.2.10"]
    assert all(r.headers["host"] == "stg.test" for r in inner.seen)
    assert all(r.extensions["sni_hostname"] == "stg.test" for r in inner.seen)
