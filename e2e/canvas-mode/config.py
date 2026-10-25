import os

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
CANVAS = f"https://{CANVAS_HOST}"
QUIZ_URL = f"{CANVAS}/courses/1/quizzes/1"
TOKEN = "fake-canvas-teacher-token-0001"
DEVICE = "mac-e2e-device-0001"
LOADTEST_USERS = 152
