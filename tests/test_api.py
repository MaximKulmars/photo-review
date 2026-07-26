import importlib
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        os.environ["PHOTO_REVIEW_PHOTOS"] = str(root / "photos")
        os.environ["PHOTO_REVIEW_QUARANTINE"] = str(root / "quarantine")
        os.environ["PHOTO_REVIEW_DATA"] = str(root / "data")
        os.environ["PHOTO_REVIEW_PASSWORD"] = "test-password"
        os.environ["PHOTO_REVIEW_SESSION_SECRET"] = "test-session-secret"
        module = importlib.import_module("app.main")
        cls.client_context = TestClient(module.app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        cls.temp.cleanup()

    def test_health_does_not_require_login(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_login_and_summary(self):
        wrong = self.client.post(
            "/login", data={"password": "wrong"}, follow_redirects=False
        )
        self.assertEqual(wrong.status_code, 401)
        correct = self.client.post(
            "/login", data={"password": "test-password"}, follow_redirects=False
        )
        self.assertEqual(correct.status_code, 303)
        summary = self.client.get("/api/summary")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["library"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
