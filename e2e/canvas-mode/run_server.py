"""Runs the real FairPlay server (uvicorn, sqlite) for the Canvas mode run. It never talks to Canvas."""
import logging
import os
import sys

import uvicorn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C  # noqa: E402

sys.path.insert(0, os.path.join(C.REPO, "server"))
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
    os.environ.pop(k, None)
os.environ["PROCTORA_SEED_LOADTEST_USERS"] = str(C.LOADTEST_USERS)

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def main() -> None:
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpcore").setLevel(logging.INFO)
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
