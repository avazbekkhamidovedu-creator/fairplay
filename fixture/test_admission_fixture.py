import unittest
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from admission_fixture import create_app, sign_lock


class AdmissionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.native = "test-only-native-key"
        self.student = "test-only-student-key"
        self.client = TestClient(create_app(native_key=self.native, student_key=self.student, clock=lambda: self.now))
        self.headers = {"x-fixture-native": self.native, "x-app-session": "session1", "x-device": "device1"}
        self.a = self.client.post("/mock-lms/launch", headers={"x-fixture-student": self.student}, json={
            "launch_url": "https://lms.example.edu/courses/1/quizzes/2", "device": "device1", "session": "session1"}).json()

    def lock(self, **override):
        body = dict(locked=True, nonce=self.a["nonce"], proof=sign_lock(self.native, self.a, self.a["nonce"], True))
        return self.client.post(f'/api/attempts/{self.a["id"]}/lock', headers=self.headers, json={**body, **override})

    def admit(self, token):
        return self.client.post("/mock-lms/admit", headers=self.headers, json={"admission": token})

    def questions(self, headers=None):
        return self.client.get(f'/mock-lms/attempts/{self.a["id"]}/questions', headers=self.headers if headers is None else headers)

    def activate(self):
        response = self.lock()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.admit(response.json()["admission"]).status_code, 200)

    def test_profile_is_authenticated_and_mock_only(self):
        self.assertEqual(self.client.get("/api/university/profile").status_code, 401)
        r = self.client.get("/api/university/profile", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["production_gate_verified"])
        self.assertEqual(r.json()["protected_launches"][0]["exam_id"], "exam_fixture")

    def test_complete_lifecycle_questions_only_after_admission(self):
        self.assertNotIn("questions", self.a)
        self.assertEqual(self.questions().status_code, 403)
        grant = self.lock()
        self.assertNotIn("questions", grant.json())
        self.assertEqual(self.questions().status_code, 403)
        self.assertEqual(self.admit(grant.json()["admission"]).status_code, 200)
        self.assertEqual(self.questions().json()["questions"][0]["id"], "q1")
        submitted = self.client.post(f'/mock-lms/attempts/{self.a["id"]}/submit', headers=self.headers)
        self.assertEqual(submitted.status_code, 200)
        self.assertIn("receipt", submitted.json())
        self.assertEqual(self.questions().status_code, 403)

    def test_lock_failure_or_forgery_never_releases_questions(self):
        for override in ({"locked": False}, {"proof": "forged"}, {"nonce": "wrong"}, {"locked": "true"}):
            with self.subTest(override=override):
                self.assertEqual(self.lock(**override).status_code, 403)
                self.assertEqual(self.questions().status_code, 403)

    def test_browser_and_wrong_binding_denied(self):
        self.activate()
        for headers in ({}, {**self.headers, "x-device": "other"}, {**self.headers, "x-app-session": "other"}):
            with self.subTest(headers=headers):
                self.assertIn(self.questions(headers).status_code, (401, 403))

    def test_challenge_replay_and_concurrent_lock(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(lambda _: self.lock().status_code, range(2)))
        self.assertEqual(sorted(codes), [200, 409])

    def test_one_use_admission_and_concurrent_replay(self):
        token = self.lock().json()["admission"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(lambda _: self.admit(token).status_code, range(2)))
        self.assertEqual(sorted(codes), [200, 403])

    def test_expired_challenge(self):
        self.now += 120
        self.assertEqual(self.lock().status_code, 409)
        self.assertEqual(self.questions().status_code, 403)

    def test_expired_admission(self):
        token = self.lock().json()["admission"]
        self.now += 30
        self.assertEqual(self.admit(token).status_code, 403)

    def test_lost_lock_revokes(self):
        self.activate()
        r = self.client.post(f'/api/attempts/{self.a["id"]}/heartbeat', headers=self.headers, json={"locked": False})
        self.assertEqual(r.json()["state"], "revoked")
        self.assertEqual(self.questions().status_code, 403)
        r = self.client.post(f'/api/attempts/{self.a["id"]}/heartbeat', headers=self.headers, json={"locked": True})
        self.assertEqual(r.status_code, 409)

    def test_missing_heartbeat_fails_closed(self):
        self.activate()
        self.now += 20
        self.assertEqual(self.questions().status_code, 403)
        self.assertEqual(self.client.post(f'/mock-lms/attempts/{self.a["id"]}/submit', headers=self.headers).status_code, 403)

    def test_context_override_and_unknown_exam_denied(self):
        for update in ({"student": "someone_else"}, {"exam_id": "other"}, {"return_url": "https://evil.example.edu"},
                       {"launch_url": "https://lms.example.edu/courses/1/quizzes/3"}):
            r = self.client.post("/mock-lms/launch", headers={"x-fixture-student": self.student}, json={
                "launch_url": "https://lms.example.edu/courses/1/quizzes/2", "device": "device1", "session": "session1", **update})
            self.assertIn(r.status_code, (400, 403))

    def test_live_heartbeat_extends_only_current_session(self):
        self.activate()
        self.now += 19
        self.assertEqual(self.client.post(f'/api/attempts/{self.a["id"]}/heartbeat', headers=self.headers, json={"locked": True}).status_code, 200)
        self.now += 19
        self.assertEqual(self.questions().status_code, 200)


if __name__ == "__main__":
    unittest.main()
