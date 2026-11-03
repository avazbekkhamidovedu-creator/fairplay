"""Real loopback TCP test; still a simulated native lock, not a Mac security test."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import unittest
import httpx
from admission_fixture import sign_lock


class LoopbackTCPTests(unittest.TestCase):
    def test_loopback_runner_full_lifecycle(self):
        root = Path(__file__).resolve().parent.parent
        native, student = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        with tempfile.TemporaryDirectory() as tmp:
            ready = Path(tmp) / "ready.json"
            env = {**os.environ, "PYTHONPATH": str(root / "server") + os.pathsep + str(root / "fixture"),
                   "FAIRPLAY_FIXTURE_NATIVE_KEY": native, "FAIRPLAY_FIXTURE_STUDENT_KEY": student,
                   "FAIRPLAY_FIXTURE_PORT": "0"}
            process = subprocess.Popen([sys.executable, str(root / "fixture/run_fixture.py"),
                                        "--ready-file", str(ready)], env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and time.monotonic() < deadline and process.poll() is None:
                    time.sleep(0.02)
                self.assertTrue(ready.exists(), "fixture did not bind a loopback socket")
                address = json.loads(ready.read_text())
                self.assertEqual(address["host"], "127.0.0.1")
                self.assertNotIn(native, ready.read_text())
                with httpx.Client(base_url=f'http://127.0.0.1:{address["port"]}', timeout=2,
                                  follow_redirects=False, trust_env=False) as client:
                    headers = {"x-fixture-native": native, "x-device": "tcp_device", "x-app-session": "tcp_session"}
                    while True:
                        try:
                            profile = client.get("/api/university/profile", headers=headers)
                            break
                        except httpx.TransportError:
                            if time.monotonic() >= deadline:
                                raise
                            time.sleep(0.02)
                    self.assertEqual(profile.status_code, 200)
                    self.assertEqual(profile.json()["mode"], "mock_only")
                    self.assertFalse(profile.json()["production_gate_verified"])
                    self.assertEqual(client.get("/api/university/profile").status_code, 401)
                    a = client.post("/mock-lms/launch", headers={"x-fixture-student": student}, json={
                        "launch_url": profile.json()["protected_launches"][0]["launch_url"],
                        "device": "tcp_device", "session": "tcp_session"}).json()
                    question_path = f'/mock-lms/attempts/{a["id"]}/questions'
                    self.assertEqual(client.get(question_path, headers=headers).status_code, 403)
                    grant = client.post(f'/api/attempts/{a["id"]}/lock', headers=headers, json={
                        "nonce": a["nonce"], "locked": True,
                        "proof": sign_lock(native, a, a["nonce"], True)})
                    self.assertEqual(grant.status_code, 200)
                    self.assertEqual(client.get(question_path, headers=headers).status_code, 403)
                    admitted = client.post("/mock-lms/admit", headers=headers, json=grant.json())
                    self.assertEqual(admitted.status_code, 200)
                    self.assertEqual(client.get(question_path, headers=headers).status_code, 200)
                    submitted = client.post(f'/mock-lms/attempts/{a["id"]}/submit', headers=headers)
                    self.assertEqual(submitted.json()["state"], "submitted")
                    self.assertEqual(client.get(question_path, headers=headers).status_code, 403)
            finally:
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
