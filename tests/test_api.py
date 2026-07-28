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

    def test_create_empty_album_registers_it_and_lists_it_on_shelf(self):
        self._login()
        shelf = self.module.config.photos_root / "2031"
        shelf.mkdir(exist_ok=True)

        response = self.client.post(
            "/api/library/albums",
            json={"year": " 2031 ", "name": "  Новый альбом "},
        )

        self.assertEqual(response.status_code, 201)
        created = response.json()
        self.assertEqual(created["year"], "2031")
        self.assertEqual(created["name"], "Новый альбом")
        self.assertEqual(created["relative_path"], "2031/Новый альбом")
        self.assertEqual(created["media_count"], 0)
        self.assertIsNone(created["cover_media_id"])
        self.assertTrue((shelf / "Новый альбом").is_dir())

        albums = self.client.get("/api/library/albums", params={"year": "2031"})
        self.assertEqual(albums.status_code, 200)
        self.assertEqual(albums.json()["items"], [created])

        shelves = self.client.get("/api/library/shelves")
        shelf_data = next(item for item in shelves.json()["items"] if item["year"] == "2031")
        self.assertEqual(shelf_data["album_count"], 1)
        self.assertEqual(shelf_data["media_count"], 0)

    def test_create_album_rejects_invalid_names_duplicates_and_missing_shelf(self):
        self._login()
        shelf = self.module.config.photos_root / "2032"
        shelf.mkdir(exist_ok=True)

        created = self.client.post(
            "/api/library/albums", json={"year": "2032", "name": "Отпуск"}
        )
        self.assertEqual(created.status_code, 201)

        duplicate = self.client.post(
            "/api/library/albums", json={"year": "2032", "name": "Отпуск"}
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertTrue((shelf / "Отпуск").is_dir())

        invalid = self.client.post(
            "/api/library/albums", json={"year": "2032", "name": "../outside"}
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertFalse((self.module.config.photos_root / "outside").exists())

        missing_shelf = self.client.post(
            "/api/library/albums", json={"year": "2099", "name": "Отпуск"}
        )
        self.assertEqual(missing_shelf.status_code, 404)

if __name__ == "__main__":
    unittest.main()
