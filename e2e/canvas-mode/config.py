import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SCRATCH = os.environ.get("CANVAS_E2E_DIR", "/tmp/canvas-mode-e2e")
VENV_PY = os.environ.get("CANVAS_E2E_SERVER_PY", "python3")
WEB_DIST = os.environ.get("CANVAS_E2E_WEB_DIST", os.path.join(SCRATCH, "webdist"))
SEL_PKGS = os.environ.get("CANVAS_E2E_SELENIUM", os.path.join(SCRATCH, "selpkgs"))

APP_KEY = "canvas-e2e-app-key-0123456789abcdef"
SECRET = "canvas-e2e-secret-0123456789abcdef"
SERVER_PORT = 8765
SERVER = f"http://127.0.0.1:{SERVER_PORT}"
ADMIN = "http://127.0.0.1:9444"
CANVAS_HOST = "canvas.fake.test"
SSO_HOST = "sso.fake.test"
OTHER_HOST = "other.fake.test"
GO_HOST = "go.fake.test"
SAML_HOST = "saml.fake.test"
CANVAS = f"https://{CANVAS_HOST}"
QUIZ_URL = f"{CANVAS}/courses/1/quizzes/1"
TOKEN = "fake-canvas-teacher-token-0001"
DEVICE = "mac-e2e-device-0001"
LOADTEST_USERS = 164
BURST_USERS = 152


def _app_version() -> str:
    """The version the Mac app under test reports (mac/Sources/Proctora/Config.swift)."""
    try:
        with open(os.path.join(REPO, "mac", "Sources", "Proctora", "Config.swift"), encoding="utf-8") as f:
            return re.search(r'static let version = "([0-9.]+)"', f.read()).group(1)
    except Exception:
        return "0.7.2"


APP_VERSION = _app_version()
V = [int(x) for x in APP_VERSION.split(".")]
APP_VERSION_OLDER = ".".join(str(x) for x in (V[0], V[1], max(V[2] - 1, 0))) if V[2] else f"{V[0]}.{max(V[1] - 1, 0)}.9"
