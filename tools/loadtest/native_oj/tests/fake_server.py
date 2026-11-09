import asyncio
import base64
import hashlib
import hmac
import json
import random
import re
import uuid
from urllib.parse import parse_qs

APP_KEY = "test-app-key-not-secret"


class FakeServer:
    def __init__(self, renew_latency=0.0, p429=0.0, p503=0.0, retry_after=None, seed=1, lease_state="active"):
        self.renew_latency = renew_latency
        self.p429 = p429
        self.p503 = p503
        self.retry_after = retry_after
        self.lease_state = lease_state
        self.rng = random.Random(seed)
        self.locks = {}
        self.renew_times = {}
        self.hits = {}
        self.ingress_ok = 0
        self.server = None
        self.port = None

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    async def handle(self, reader, writer):
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                lines = head.decode().split("\r\n")
                method, path, _ = lines[0].split(" ", 2)
                headers = {k.lower(): v for k, v in (l.split(": ", 1) for l in lines[1:] if ": " in l)}
                body = await reader.readexactly(int(headers.get("content-length", 0)))
                status, extra, payload = await self.route(method, path, headers, body)
                data = payload if isinstance(payload, bytes) else payload.encode()
                out = [f"HTTP/1.1 {status} X", f"Content-Length: {len(data)}", "Content-Type: application/json"]
                out += [f"{k}: {v}" for k, v in extra.items()]
                writer.write(("\r\n".join(out) + "\r\n\r\n").encode() + data)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    def state(self, session, state=None):
        payload = {"v": 2, "session": session, "state": state or self.lease_state}
        return json.dumps({"payload": base64.b64encode(json.dumps(payload).encode()).decode(), "signature": ""})

    async def route(self, method, path, headers, body):
        self.hits[path.split("?")[0].rsplit("/", 1)[-1] if "renew" in path else path] = \
            self.hits.get(path.split("?")[0].rsplit("/", 1)[-1] if "renew" in path else path, 0) + 1
        if path == "/api/native-exit/locks" and method == "POST":
            doc = json.loads(body)
            want = hmac.new(APP_KEY.encode(), f"nativelock|{doc['lock_id']}|{doc['device']}".encode(),
                            hashlib.sha256).hexdigest()
            if not hmac.compare_digest(want, doc["signature"]):
                return 403, {}, "{}"
            if len(base64.b64decode(doc["blob"])) != 92 or not re.fullmatch(r"[A-Z2-9]{8}", doc["lock_id"]):
                return 422, {}, "{}"
            if doc["lock_id"] in self.locks:
                return 409, {}, "{}"
            self.locks[doc["lock_id"]] = doc["device"]
            return 201, {}, '{"ok":true}'
        if path.startswith("/api/fairplay/native/"):
            if headers.get("x-fairplay-client-verify") != "SUCCESS" or not headers.get("x-fairplay-client-cert"):
                return 404, {}, "{}"
            self.ingress_ok += 1
            if path.startswith("/api/fairplay/native/claim/"):
                return 200, {}, self.state(str(uuid.uuid4()))
            m = re.fullmatch(r"/api/fairplay/native/session/([^/]+)/renew", path)
            if m:
                await asyncio.sleep(self.renew_latency)
                self.renew_times.setdefault(m.group(1), []).append(asyncio.get_running_loop().time())
                roll = self.rng.random()
                if roll < self.p429:
                    return 429, ({"Retry-After": str(self.retry_after)} if self.retry_after else {}), "{}"
                if roll < self.p429 + self.p503:
                    return 503, {}, "{}"
                return 200, {}, self.state(m.group(1))
        if method == "POST" and path == "/accounts/login/":
            return 302, {"Set-Cookie": "sessionid=s; Path=/"}, ""
        if method == "GET":
            return 200, {"Set-Cookie": "csrftoken=abc; Path=/"}, "<html></html>"
        return 404, {}, "{}"
