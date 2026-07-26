import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.storage import Storage


def make_media(database: Database, relative: str, source: Path) -> int:
    checksum = Storage.checksum(source)
    stat = source.stat()
    return database.execute(
        """
        INSERT INTO media(relative_path,size,mtime_ns,sha256,status)
        VALUES(?,?,?,?,'active')
        """,
        (relative, stat.st_size, stat.st_mtime_ns, checksum),
    )


class StorageTests(unittest.TestCase):
    def test_quarantine_and_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            photos = tmp_path / "photos"
            quarantine = tmp_path / "quarantine"
            photos.mkdir()
            quarantine.mkdir()
            source = photos / "2024" / "photo.jpg"
            source.parent.mkdir()
            source.write_bytes(b"photo bytes")

            database = Database(tmp_path / "data.sqlite3")
            database.initialize()
            media_id = make_media(database, "2024/photo.jpg", source)
            storage = Storage(photos, quarantine, database)

            quarantine_relative = storage.quarantine_media(media_id)
            self.assertEqual(quarantine_relative, "2024/photo.jpg")
            self.assertFalse(source.exists())
            self.assertEqual(
                (quarantine / quarantine_relative).read_bytes(), b"photo bytes"
            )

            restored = storage.restore_media(media_id)
            self.assertEqual(restored, "2024/photo.jpg")
            self.assertEqual(source.read_bytes(), b"photo bytes")

    def test_restore_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            photos = tmp_path / "photos"
            quarantine = tmp_path / "quarantine"
            photos.mkdir()
            quarantine.mkdir()
            source = photos / "photo.jpg"
            source.write_bytes(b"old")
            database = Database(tmp_path / "data.sqlite3")
            database.initialize()
            media_id = make_media(database, "photo.jpg", source)
            storage = Storage(photos, quarantine, database)
            storage.quarantine_media(media_id)
            source.write_bytes(b"new")

            with self.assertRaises(FileExistsError):
                storage.restore_media(media_id)
            self.assertEqual(source.read_bytes(), b"new")

            restored = storage.restore_media(media_id, rename_on_conflict=True)
            self.assertEqual(restored, "photo (1).jpg")
            self.assertEqual((photos / restored).read_bytes(), b"old")

    def test_permanent_delete_removes_quarantined_file_and_database_row(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            photos = tmp_path / "photos"
            quarantine = tmp_path / "quarantine"
            photos.mkdir()
            quarantine.mkdir()
            source = photos / "photo.jpg"
            source.write_bytes(b"temporary")
            database = Database(tmp_path / "data.sqlite3")
            database.initialize()
            media_id = make_media(database, "photo.jpg", source)
            storage = Storage(photos, quarantine, database)
            quarantine_relative = storage.quarantine_media(media_id)

            storage.delete_media(media_id)

            self.assertFalse((quarantine / quarantine_relative).exists())
            self.assertIsNone(database.one("SELECT id FROM media WHERE id=?", (media_id,)))
            log = database.one(
                "SELECT action FROM audit_log ORDER BY id DESC LIMIT 1"
            )
            self.assertEqual(log["action"], "delete")


if __name__ == "__main__":
    unittest.main()
