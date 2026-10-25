"""Runs the real FairPlay server (uvicorn, sqlite) with the Canvas client pointed at the fake Canvas.

The server refuses private and loopback addresses for LMS calls. The tests do not weaken that
check: like server/tests/test_lms.py they inject a transport through lms.set_transport_factory.
This one sends the request over real HTTPS to the fake Canvas on 127.0.0.1:443, keeping the
Canvas host name in the URL and the Host header.
"""
import logging
import os
import sys

import httpx
import uvicorn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C  # noqa: E402

sys.path.insert(0, os.path.join(C.REPO, "server"))
os.environ.setdefault("FAIRPLAY_LMS_HOSTS", C.CANVAS_HOST)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
    os.environ.pop(k, None)
os.environ["PROCTORA_SEED_LOADTEST_USERS"] = str(C.LOADTEST_USERS)

from app import lms  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


class ToFakeCanvas(httpx.AsyncBaseTransport):
    def __init__(self):
        self.inner = httpx.AsyncHTTPTransport(verify=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != C.CANVAS_HOST:
            raise httpx.ConnectError("only the fake Canvas is reachable")
        request.extensions = {**request.extensions, "sni_hostname": C.CANVAS_HOST}
        request.url = request.url.copy_with(host="127.0.0.1")
        return await self.inner.handle_async_request(request)


def main() -> None:
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpcore").setLevel(logging.INFO)
    lms.set_transport_factory(lambda provider_id, base_url: ToFakeCanvas())
    work = os.path.join(C.SCRATCH, "server")
    os.makedirs(work, exist_ok=True)
    s = Settings(database_url=f"sqlite+aiosqlite:///{work}/e2e.db", redis_url=None, secret=C.SECRET,
                 pbkdf2_iterations=1000, require_app=True, app_keys=[C.APP_KEY], dev=False,
                 seed_demo=True, single_process=True, static_dir=C.WEB_DIST,
                 uploads_dir=f"{work}/uploads", seed_loadtest_users=C.LOADTEST_USERS,
                 login_ip_limit=100000)
    uvicorn.run(create_app(s), host="127.0.0.1", port=C.SERVER_PORT, log_level="debug")


if __name__ == "__main__":
    main()
