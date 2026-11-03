"""Run the opt-in Swift fixture test on a Mac. No real lock or credentials."""
import http.client
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time


def main():
    root = Path(__file__).resolve().parent.parent
    if sys.platform != "darwin":
        raise SystemExit("This harness requires macOS and Swift; no native test was run")
    with tempfile.TemporaryDirectory() as tmp:
        ready = Path(tmp) / "ready.json"
        env = {**os.environ, "PYTHONPATH": str(root / "server") + os.pathsep + str(root / "fixture"),
               "FAIRPLAY_FIXTURE_NATIVE_KEY": secrets.token_urlsafe(32),
               "FAIRPLAY_FIXTURE_STUDENT_KEY": secrets.token_urlsafe(32), "FAIRPLAY_FIXTURE_PORT": "0"}
        server = subprocess.Popen([sys.executable, str(root / "fixture/run_fixture.py"),
                                   "--ready-file", str(ready)], env=env)
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and server.poll() is None:
                if ready.exists():
                    binding = json.loads(ready.read_text())
                    if binding["host"] != "127.0.0.1":
                        raise RuntimeError("Fixture is not loopback-only")
                    env["FAIRPLAY_FIXTURE_PORT"] = str(binding["port"])
                    connection = http.client.HTTPConnection("127.0.0.1", binding["port"], timeout=1)
                    try:
                        connection.request("GET", "/api/university/profile",
                                           headers={"x-fixture-native": env["FAIRPLAY_FIXTURE_NATIVE_KEY"]})
                        response = connection.getresponse()
                        profile = json.loads(response.read())
                        if response.status == 200 and profile.get("mode") == "mock_only" and profile.get("production_gate_verified") is False:
                            break
                    except (OSError, ValueError, http.client.HTTPException):
                        pass
                    finally:
                        connection.close()
                time.sleep(0.05)
            else:
                raise RuntimeError("Local mock fixture did not become ready")
            return subprocess.run(["swift", "test", "--filter", "UniversityFixtureIntegrationTests"],
                                  cwd=root / "mac", env=env, timeout=180).returncode
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
