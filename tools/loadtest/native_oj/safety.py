import ipaddress
import itertools
import os
import socket
from urllib.parse import urlsplit

import httpx

ENV_ALLOWED = "LOADTEST_ALLOWED_HOSTS"
MAX_CLIENTS = 6000
DENIED_NAMES = ("dku.speakupai.uz",)
DENIED_IPS = ("10.200.31.64", "10.200.30.218")


class Refused(Exception):
    pass


def _norm_ip(text):
    try:
        ip = ipaddress.ip_address(text.split("%")[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def system_resolver(host):
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({i[4][0] for i in infos})


def parse_allowlist(raw):
    entries = []
    for part in (raw or "").split(","):
        part = part.strip().lower()
        if not part:
            continue
        if "*" in part or "/" in part:
            raise Refused(f"{ENV_ALLOWED} entry {part!r} is not a plain host[:port]")
        entries.append(part)
    return entries


def split_target(url):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise Refused(f"{url!r}: only http and https URLs are accepted")
    if parts.username or parts.password:
        raise Refused(f"{url!r}: credentials in the URL are not accepted")
    try:
        host = parts.hostname
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise Refused(f"{url!r}: bad port") from None
    if not host:
        raise Refused(f"{url!r}: no host")
    return host.lower(), port


def embedded_v4(ip):
    """The IPv4 addresses an IPv6 address can carry: mapped, compatible, NAT64, 6to4, Teredo, ISATAP."""
    out = {ip}
    if isinstance(ip, ipaddress.IPv4Address):
        return out
    n = int(ip)
    low = ipaddress.IPv4Address(n & 0xFFFFFFFF)
    if ip.ipv4_mapped:
        out.add(ip.ipv4_mapped)
    if n >> 32 == 0:
        out.add(low)
    if ip in ipaddress.ip_network("64:ff9b::/96") or ip in ipaddress.ip_network("64:ff9b:1::/48"):
        out.add(low)
    if ip in ipaddress.ip_network("2002::/16"):
        out.add(ipaddress.IPv4Address((n >> 80) & 0xFFFFFFFF))
    if ip in ipaddress.ip_network("2001::/32"):
        out.add(ipaddress.IPv4Address((n >> 64) & 0xFFFFFFFF))
        out.add(ipaddress.IPv4Address((n & 0xFFFFFFFF) ^ 0xFFFFFFFF))
    if (n >> 32) & 0xFFFFFFFF in (0x00005EFE, 0x02005EFE):
        out.add(low)
    return out


def _denied_addresses(resolver):
    """Fails closed: a denied name that does not resolve means the deny list is incomplete."""
    denied = set()
    for text in DENIED_IPS:
        denied |= embedded_v4(_norm_ip(text))
    for name in DENIED_NAMES:
        try:
            found = list(resolver(name))
        except OSError as error:
            raise Refused(f"cannot resolve the denied name {name} ({error}); the deny list would be incomplete") from None
        if not found:
            raise Refused(f"cannot resolve the denied name {name}; the deny list would be incomplete")
        for address in found:
            ip = _norm_ip(address)
            if ip is None:
                raise Refused(f"the denied name {name} gave an unreadable address {address!r}")
            denied |= embedded_v4(ip)
    return denied


def check_target(url, env=None, resolver=system_resolver):
    """Returns (host, port, resolved_addresses) or raises Refused."""
    env = os.environ if env is None else env
    host, port = split_target(url)
    if host.rstrip(".") in DENIED_NAMES:
        raise Refused(f"{host} is a live host and is never allowed")
    literal = _norm_ip(host)
    if literal is not None:
        live = {ip for t in DENIED_IPS for ip in embedded_v4(_norm_ip(t))}
        if embedded_v4(literal) & live:
            raise Refused(f"{host} is a live address and is never allowed")
    allowed = parse_allowlist(env.get(ENV_ALLOWED, ""))
    if not allowed:
        raise Refused(f"{ENV_ALLOWED} is not set; list the staging hosts explicitly")
    if host not in allowed and f"{host}:{port}" not in allowed:
        raise Refused(f"{host}:{port} is not in {ENV_ALLOWED}")
    try:
        addresses = [a for a in resolver(host)] if literal is None else [str(literal)]
    except OSError as error:
        raise Refused(f"{host} does not resolve: {error}") from None
    if not addresses:
        raise Refused(f"{host} does not resolve")
    denied = _denied_addresses(resolver)
    for address in addresses:
        ip = _norm_ip(address)
        if ip is None:
            raise Refused(f"{host} resolved to an unreadable address {address!r}")
        if embedded_v4(ip) & denied:
            raise Refused(f"{host} resolves to {address}, a live address, and is never allowed")
    return host, port, addresses


def check_clients(n):
    if n < 1:
        raise Refused("--clients must be at least 1")
    if n > MAX_CLIENTS:
        raise Refused(f"--clients {n} is above the cap of {MAX_CLIENTS}")
    return n


def make_guard(urls, env=None, resolver=system_resolver, pinned=None):
    """Repeats the allow/deny check with a fresh DNS lookup. With `pinned` ({url: addresses}),
    an answer holding an address that is not in the pinned set is also refused."""
    def guard():
        for url in urls:
            host, port, addresses = check_target(url, env, resolver)
            if pinned is not None:
                extra = sorted(set(addresses) - set(pinned[url]))
                if extra:
                    raise Refused(f"{host} now resolves to {', '.join(extra)}, outside the pinned set "
                                  f"{', '.join(sorted(pinned[url]))}")
    return guard


class PinnedTransport(httpx.AsyncBaseTransport):
    """httpx transport that connects only to the pinned addresses of the pinned hosts. The URL
    host is swapped for the address; Host and the TLS server name stay the original name."""

    def __init__(self, inner, pins):
        self.inner = inner
        self.pins = {k: list(v) for k, v in pins.items()}
        self.cycle = {k: itertools.cycle(v) for k, v in self.pins.items()}

    async def handle_async_request(self, request):
        host, port = request.url.host.lower(), request.url.port or (443 if request.url.scheme == "https" else 80)
        cycle = self.cycle.get((host, port))
        if cycle is None:
            raise httpx.ConnectError(f"{host}:{port} is not a pinned target")
        original = request.headers.get("host") or (host if request.url.port is None else f"{host}:{port}")
        request.url = request.url.copy_with(host=next(cycle))
        request.headers["Host"] = original
        request.extensions = {**request.extensions, "sni_hostname": host}
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()
