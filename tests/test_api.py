import importlib
import os
import tempfile
import shutil
from io import BytesIO
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image


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
        cls.module = module
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



    def _login(self):
        self.client.post("/login", data={"password": "test-password"}, follow_redirects=False)

    @staticmethod
    def _image_bytes(color="red"):
        output = BytesIO()
        Image.new("RGB", (4, 4), color=color).save(output, format="PNG")
        return output.getvalue()

    def _album_id(self):
        album = self.module.config.photos_root / "2024" / "Vacation"
        if album.parent.exists():
            shutil.rmtree(album.parent)
        album.mkdir(parents=True, exist_ok=True)
        self.module.library_indexer.scan()
        return self.module.database.one(
            "SELECT id FROM containers WHERE relative_path='2024/Vacation'"
        )["id"]

    def test_upload_saves_and_indexes_photo_in_current_album(self):
        self._login()
        album_id = self._album_id()
        response = self.client.post(
            f"/api/library/albums/{album_id}/photos",
            files=[("files", ("camera.png", self._image_bytes(), "image/png"))],
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["successful_count"], 1)
        self.assertTrue((self.module.config.photos_root / "2024" / "Vacation" / "camera.png").is_file())
        self.assertIsNotNone(self.module.database.one(
            "SELECT id FROM media WHERE relative_path='2024/Vacation/camera.png' AND container_id=?",
            (album_id,),
        ))

    def test_upload_renames_conflicting_files_without_overwrite(self):
        self._login()
        album_id = self._album_id()
        folder = self.module.config.photos_root / "2024" / "Vacation"
        original = self._image_bytes("blue")
        (folder / "camera.png").write_bytes(original)
        response = self.client.post(
            f"/api/library/albums/{album_id}/photos",
            files=[("files", ("camera.png", self._image_bytes("green"), "image/png"))],
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["stored_name"], "camera_1.png")
        self.assertEqual((folder / "camera.png").read_bytes(), original)

    def test_upload_rejects_invalid_image_without_creating_file(self):
        self._login()
        album_id = self._album_id()
        response = self.client.post(
            f"/api/library/albums/{album_id}/photos",
            files=[("files", ("fake.png", b"not an image", "image/png"))],
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["error_code"], "INVALID_IMAGE")
        self.assertFalse((self.module.config.photos_root / "2024" / "Vacation" / "fake.png").exists())

if __name__ == "__main__":
    unittest.main()
