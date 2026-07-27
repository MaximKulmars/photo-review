import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db import Database
from app.storage import Storage


def add_media(database: Database, relative_path: str, source: Path) -> int:
    stat = source.stat()
    return database.execute(
        """
        INSERT INTO media(relative_path, size, mtime_ns, sha256)
        VALUES (?, ?, ?, ?)
        """,
        (
            relative_path,
            stat.st_size,
            stat.st_mtime_ns,
            Storage.checksum(source),
        ),
    )


class StorageSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.photos = self.root / "photos"
        self.quarantine = self.root / "quarantine"
        self.photos.mkdir()
        self.quarantine.mkdir()
        self.database = Database(self.root / "data.sqlite3")
        self.database.initialize()
        self.storage = Storage(self.photos, self.quarantine, self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def test_cross_device_move_verifies_copy_before_removing_source(self):
        source = self.photos / "photo.jpg"
        source.write_bytes(b"verified contents")
        media_id = add_media(self.database, "photo.jpg", source)
        original_replace = __import__("os").replace
        calls = 0

        def replace_with_cross_device_fallback(left, right):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("different filesystem")
            return original_replace(left, right)

        with patch("app.storage.os.replace", side_effect=replace_with_cross_device_fallback):
            relative = self.storage.quarantine_media(media_id)

        self.assertFalse(source.exists())
        self.assertEqual(
            (self.quarantine / relative).read_bytes(), b"verified contents"
        )
        self.assertFalse((self.quarantine / "photo.jpg.partial").exists())

    def test_copy_checksum_failure_leaves_source_and_cleans_partial(self):
        source = self.photos / "source.jpg"
        destination = self.photos / "destination"
        source.write_bytes(b"original")
        destination.mkdir()
        media_id = add_media(self.database, "source.jpg", source)

        with patch.object(Storage, "checksum", return_value="wrong"):
            with self.assertRaises(OSError):
                self.storage.copy_media(media_id, "destination")

        self.assertEqual(source.read_bytes(), b"original")
        self.assertFalse((destination / "source.jpg").exists())
        self.assertFalse((destination / "source.jpg.partial").exists())
        self.assertIsNone(
            self.database.one(
                "SELECT id FROM audit_log WHERE action='copy'"
            )
        )

    def test_quarantine_conflict_never_overwrites_existing_file(self):
        source = self.photos / "photo.jpg"
        source.write_bytes(b"new")
        (self.quarantine / "photo.jpg").write_bytes(b"existing")
        media_id = add_media(self.database, "photo.jpg", source)

        relative = self.storage.quarantine_media(media_id)

        self.assertEqual(relative, "photo (1).jpg")
        self.assertEqual((self.quarantine / "photo.jpg").read_bytes(), b"existing")
        self.assertEqual((self.quarantine / relative).read_bytes(), b"new")


if __name__ == "__main__":
    unittest.main()
