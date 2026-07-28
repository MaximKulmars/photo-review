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

    def test_move_unsorted_media_to_album_updates_collection_state(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            photos = tmp_path / "photos"
            quarantine = tmp_path / "quarantine"
            unsorted_file = photos / "Unsorted" / "Manual Import" / "photo.jpg"
            album = photos / "2026" / "Trip"
            unsorted_file.parent.mkdir(parents=True)
            album.mkdir(parents=True)
            quarantine.mkdir()
            unsorted_file.write_bytes(b"photo bytes")

            database = Database(tmp_path / "data.sqlite3")
            database.initialize()
            container_id = database.execute(
                """
                INSERT INTO containers(
                    library_root, media_type, kind, year, relative_path, name
                ) VALUES ('photos', 'photo', 'album', '2026', '2026/Trip', 'Trip')
                """
            )
            media_id = database.execute(
                """
                INSERT INTO media(
                    relative_path, size, mtime_ns, sha256, status,
                    library_root, media_type, file_name, parent_relative_path,
                    collection_state, source_name, source_relative_path
                ) VALUES (?, ?, ?, ?, 'active', 'photos', 'photo', ?, ?,
                    'unsorted', 'Manual Import', 'Manual Import')
                """,
                (
                    "Unsorted/Manual Import/photo.jpg",
                    unsorted_file.stat().st_size,
                    unsorted_file.stat().st_mtime_ns,
                    Storage.checksum(unsorted_file),
                    "photo.jpg",
                    "Unsorted/Manual Import",
                ),
            )
            storage = Storage(photos, quarantine, database)

            moved = storage.move_media(media_id, "2026/Trip")

            self.assertEqual(moved, "2026/Trip/photo.jpg")
            row = database.one("SELECT * FROM media WHERE id=?", (media_id,))
            self.assertEqual(row["collection_state"], "album")
            self.assertEqual(row["container_id"], container_id)
            self.assertEqual(row["source_name"], "Manual Import")
            self.assertFalse(unsorted_file.exists())
            self.assertEqual((album / "photo.jpg").read_bytes(), b"photo bytes")


if __name__ == "__main__":
    unittest.main()
