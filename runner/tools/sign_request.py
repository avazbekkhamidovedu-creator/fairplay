#!/usr/bin/env python3
"""Prints curl-ready signing headers for a request body.

usage: sign_request.py TENANT SECRET [BODY_FILE|-]
Reads the body from BODY_FILE or stdin (empty body for GET requests).
"""
import hashlib
import hmac
import secrets
import sys
import time


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    tenant, secret = sys.argv[1], sys.argv[2]
    src = sys.argv[3] if len(sys.argv) > 3 else None
    if src is None:
        body = b""
    elif src == "-":
        body = sys.stdin.buffer.read()
    else:
        with open(src, "rb") as fh:
            body = fh.read()
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)
    msg = f"{ts}.{nonce}.{hashlib.sha256(body).hexdigest()}"
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    for name, value in (("X-FP-Tenant", tenant), ("X-FP-Ts", ts), ("X-FP-Nonce", nonce), ("X-FP-Sig", sig)):
        print(f"-H '{name}: {value}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
