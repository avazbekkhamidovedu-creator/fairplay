import unittest
from app.university_profile import (UniversityProfile, LaunchContext, ProtectedLaunch,
                                    exact_https_origin, validate_verified_launch, validate_protected_launches)


class UniversityProfileTests(unittest.TestCase):
    def setUp(self):
        self.profile = UniversityProfile("school", "https://lms.example.edu", "https://exam.example.edu",
                                         ("https://lms.example.edu", "https://sso.example.edu"), "canvas", 1)
        self.context = LaunchContext("school", "canvas", "student", "exam", "attempt", "session", "device", 1)
        self.claims = dict(university_id="school", adapter_id="canvas", sub="student", exam_id="exam",
                           attempt_id="attempt", app_session_id="session", device_id="device",
                           aud="https://exam.example.edu", purpose="fairplay.exam.launch.v1",
                           profile_revision=1, jti="nonce", iat=1000, exp=1120)

    def test_valid_semantics(self):
        validate_verified_launch(self.claims, self.context, self.profile, 1000)

    def test_origin_normalization(self):
        self.assertEqual(exact_https_origin("https://LMS.example.edu:443"), "https://lms.example.edu")

    def test_ambiguous_origins_rejected(self):
        for origin in ("http://lms.example.edu", "https://*.example.edu", "https://lms.example.edu/",
                       "https://user@lms.example.edu", "https://lms.example.edu.", "https://lms.example.edu:444",
                       "https://127.0.0.1", "https://lms.example.edu#", "https://lms.example.edu?",
                       "https://lms.example.edu/%2e", "https://lms.example.edu\\evil", "https://lms.example.edu\n"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                exact_https_origin(origin)

    def test_binding_mismatch(self):
        for key in ("university_id", "adapter_id", "sub", "exam_id", "attempt_id", "app_session_id",
                    "device_id", "aud", "purpose", "profile_revision", "jti"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_verified_launch({**self.claims, key: None}, self.context, self.profile, 1000)

    def test_time_boundaries(self):
        for update, now in (({}, 999), ({}, 1120), ({"exp": 1121}, 1000), ({"iat": True}, 1000),
                            ({"exp": 1000}, 1000), ({"exp": "1120"}, 1000)):
            with self.subTest(update=update, now=now), self.assertRaises(ValueError):
                validate_verified_launch({**self.claims, **update}, self.context, self.profile, now)

    def test_browser_cannot_override_exam(self):
        for key in ("url", "return_url", "allowed_domains", "lock_policy", "question_url"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_verified_launch({**self.claims, key: "anything"}, self.context, self.profile, 1000)

    def test_stale_profile(self):
        p = UniversityProfile("school", "https://lms.example.edu", "https://exam.example.edu",
                              ("https://lms.example.edu",), "canvas", 2)
        with self.assertRaises(ValueError):
            validate_verified_launch(self.claims, self.context, p, 1000)

    def test_profile_rejects_duplicates_and_absent_portal(self):
        for origins in (("https://sso.example.edu",), ("https://lms.example.edu", "https://LMS.example.edu:443")):
            with self.subTest(origins=origins), self.assertRaises(ValueError):
                UniversityProfile("school", "https://lms.example.edu", "https://exam.example.edu", origins, "canvas", 1)

    def test_exact_protected_mapping(self):
        self.assertEqual(validate_protected_launches(self.profile, (
            ProtectedLaunch("https://LMS.example.edu:443/courses/1/quizzes/2", "exam"),
        )), {"https://lms.example.edu/courses/1/quizzes/2": "exam"})

    def test_mapping_rejects_foreign_and_ambiguous_urls(self):
        for url in ("https://evil.example.edu/courses/1/quizzes/2",
                    "https://lms.example.edu/a?redirect=evil", "https://lms.example.edu/a#b",
                    "https://lms.example.edu/a/../b", "https://lms.example.edu/a//b",
                    "https://lms.example.edu/a%2fb"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_protected_launches(self.profile, (ProtectedLaunch(url, "exam"),))

    def test_duplicate_launches_rejected(self):
        entry = ProtectedLaunch("https://lms.example.edu/courses/1/quizzes/2", "exam")
        with self.assertRaises(ValueError):
            validate_protected_launches(self.profile, (entry, entry))

    def test_fairplay_origin_cannot_be_browsed(self):
        for origin in ("https://exam.example.edu", "https://EXAM.example.edu:443"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                UniversityProfile("school", "https://lms.example.edu", "https://exam.example.edu",
                                  ("https://lms.example.edu", origin), "canvas", 1)

    def test_only_native_supported_canvas_launch_paths(self):
        for path in ("/test", "/courses/1", "/courses/a/quizzes/2", "/courses/1/quizzes/a",
                     "/courses/1/exams/2", "/courses/1/quizzes/2/take", "/courses/1/quizzes/2/",
                     "/courses/" + "1" * 21 + "/quizzes/2", "/courses/1/quizzes/" + "2" * 21,
                     "/courses/1/%71uizzes/2", "/courses/1/quizzes/%32"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_protected_launches(self.profile, (ProtectedLaunch("https://lms.example.edu" + path, "exam"),))
        for kind in ("quizzes", "assignments"):
            path = "/courses/" + "1" * 20 + "/" + kind + "/" + "2" * 20
            self.assertEqual(len(validate_protected_launches(self.profile, (
                ProtectedLaunch("https://lms.example.edu" + path, "exam"),))), 1)

    def test_native_mapping_count_boundary(self):
        entries = tuple(ProtectedLaunch(f"https://lms.example.edu/courses/1/quizzes/{i}", f"exam_{i}")
                        for i in range(1001))
        self.assertEqual(len(validate_protected_launches(self.profile, entries[:1000])), 1000)
        for invalid in ((), entries):
            with self.subTest(count=len(invalid)), self.assertRaises(ValueError):
                validate_protected_launches(self.profile, invalid)


if __name__ == "__main__":
    unittest.main()
