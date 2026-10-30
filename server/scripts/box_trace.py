"""Print the hosts a browser HAR file reached, to refine app/box_profiles.py.

    python scripts/box_trace.py session.har

Export the HAR from the browser's network tab after signing in to Duke Box and opening a
shared link. Prints, per host, the request count and the first path segments seen. Reads host
and path only: cookies, headers, query strings and bodies are never read or printed. Writes
nothing.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from urllib.parse import urlsplit


def summarize(har: dict) -> dict[str, dict]:
    """{host: {"requests": n, "paths": {first segment: n}}} from a HAR document."""
    hosts: dict[str, dict] = defaultdict(lambda: {"requests": 0, "paths": Counter()})
    entries = ((har.get("log") or {}).get("entries")) or []
    for entry in entries:
        url = ((entry or {}).get("request") or {}).get("url")
        if not isinstance(url, str):
            continue
        try:
            u = urlsplit(url)
            host = (u.hostname or "").lower()
        except ValueError:
            continue
        if not host:
            continue
        first = u.path.split("/")[1] if u.path.count("/") else ""
        seg = "/" + first if first else "/"
        hosts[host]["requests"] += 1
        hosts[host]["paths"][seg] += 1
    return dict(hosts)


def render(summary: dict[str, dict]) -> str:
    lines = []
    for host in sorted(summary):
        info = summary[host]
        paths = ", ".join(f"{seg} x{n}" for seg, n in sorted(info["paths"].items(), key=lambda x: (-x[1], x[0])))
        lines.append(f"{host}\t{info['requests']}\t{paths}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python scripts/box_trace.py <file.har>", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as f:
            har = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"cannot read {argv[1]}: {exc.__class__.__name__}", file=sys.stderr)
        return 1
    out = render(summarize(har))
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
