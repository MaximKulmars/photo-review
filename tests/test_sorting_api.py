import importlib
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class SortingApiRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        os.environ["PHOTO_REVIEW_PHOTOS"] = str(cls.root / "photos")
        os.environ["PHOTO_REVIEW_QUARANTINE"] = str(cls.root / "quarantine")
        os.environ["PHOTO_REVIEW_DATA"] = str(cls.root / "data")
        os.environ["PHOTO_REVIEW_AUTH_ENABLED"] = "false"
        import app.main

        cls.module = importlib.reload(app.main)
        cls.client_context = TestClient(cls.module.app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        cls.temporary.cleanup()

    def setUp(self):
        for table in ("findings", "media", "jobs", "audit_log"):
            self.module.database.execute(f"DELETE FROM {table}")
        for folder in self.module.config.photos_root.iterdir():
            if folder.is_dir():
                for child in folder.iterdir():
                    if child.is_file():
                        child.unlink()
                folder.rmdir()
            elif folder.is_file():
                folder.unlink()

    def add_media(self, relative_path: str, contents: bytes = b"photo") -> int:
        path = self.module.config.photos_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        stat = path.stat()
        return self.module.database.execute(
            """
            INSERT INTO media(relative_path, size, mtime_ns, sha256)
            VALUES (?, ?, ?, ?)
            """,
            (
                relative_path,
                stat.st_size,
                stat.st_mtime_ns,
                self.module.storage.checksum(path),
            ),
        )

    def test_folder_listing_and_creation_preserve_physical_structure(self):
        (self.module.config.photos_root / "2024").mkdir()
        response = self.client.get("/api/folders")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["directories"][0]["path"], "2024")

        created = self.client.post(
            "/api/folders", json={"parent": "2024", "name": "Отпуск"}
        )
        self.assertEqual(created.status_code, 200)
        self.assertTrue(
            (self.module.config.photos_root / "2024" / "Отпуск").is_dir()
        )
        conflict = self.client.post(
            "/api/folders", json={"parent": "2024", "name": "Отпуск"}
        )
        self.assertEqual(conflict.status_code, 409)

    def test_move_api_updates_disk_database_and_audit(self):
        media_id = self.add_media("inbox/photo.jpg", b"move me")
        (self.module.config.photos_root / "sorted").mkdir()

        response = self.client.post(
            "/api/media/transfer",
            json={
                "media_ids": [media_id],
                "destination": "sorted",
                "operation": "move",
                "rename_on_conflict": False,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            (self.module.config.photos_root / "inbox" / "photo.jpg").exists()
        )
        self.assertEqual(
            (
                self.module.config.photos_root / "sorted" / "photo.jpg"
            ).read_bytes(),
            b"move me",
        )
        self.assertEqual(
            self.module.database.one(
                "SELECT relative_path FROM media WHERE id=?", (media_id,)
            )["relative_path"],
            "sorted/photo.jpg",
        )
        self.assertEqual(
            self.module.database.one(
                "SELECT action FROM audit_log ORDER BY id DESC LIMIT 1"
            )["action"],
            "move",
        )

    def test_review_keep_action_preserves_file_and_records_decision(self):
        media_id = self.add_media("photo.jpg")
        job_id = self.module.database.execute(
            """
            INSERT INTO jobs(scope, duplicate_scope, state)
            VALUES('', 'scope', 'completed')
            """
        )
        self.module.database.execute(
            "UPDATE media SET last_scan_job_id=? WHERE id=?",
            (job_id, media_id),
        )
        finding_id = self.module.database.execute(
            """
            INSERT INTO findings(media_id, category, reason, score)
            VALUES(?, 'blurry', 'test', 0.5)
            """,
            (media_id,),
        )

        response = self.client.post(
            "/api/review/action",
            json={"finding_ids": [finding_id], "action": "keep"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            (self.module.config.photos_root / "photo.jpg").is_file()
        )
        self.assertEqual(
            self.module.database.one(
                "SELECT decision FROM findings WHERE id=?", (finding_id,)
            )["decision"],
            "keep",
        )

    def test_delete_requires_exact_confirmation(self):
        media_id = self.add_media("photo.jpg")
        moved = self.client.post(
            "/api/media/quarantine",
            json={"media_ids": [media_id], "rename_on_conflict": False},
        )
        self.assertEqual(moved.status_code, 200)

        rejected = self.client.post(
            "/api/quarantine/delete",
            json={"media_ids": [media_id], "confirmation": "delete"},
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertIsNotNone(
            self.module.database.one("SELECT id FROM media WHERE id=?", (media_id,))
        )


if __name__ == "__main__":
    unittest.main()
