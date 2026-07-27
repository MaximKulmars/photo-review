import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.library import LibraryIndexer


class LibraryIndexerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "photos"
        self.root.mkdir()
        self.database = Database(Path(self.temporary.name) / "data.sqlite3")
        self.database.initialize()
        self.indexer = LibraryIndexer(self.database, {"photos": self.root})

    def tearDown(self):
        self.temporary.cleanup()

    def test_indexes_shelves_nested_media_and_empty_albums_without_moving(self):
        (self.root / "2024" / "Пустой альбом").mkdir(parents=True)
        album = self.root / "2024" / "Отпуск" / "От друзей"
        album.mkdir(parents=True)
        original = album / "photo.jpg"
        original.write_bytes(b"photo")
        direct = self.root / "2024" / "scan.png"
        direct.write_bytes(b"scan")
        (self.root / "Unsorted").mkdir()
        (self.root / "Unsorted" / "new.webp").write_bytes(b"new")

        report = self.indexer.scan()

        self.assertEqual(report.indexed, 3)
        self.assertEqual(report.containers, 2)
        self.assertEqual(original.read_bytes(), b"photo")
        containers = self.database.all(
            "SELECT name FROM containers ORDER BY name"
        )
        self.assertEqual([row["name"] for row in containers], ["Отпуск", "Пустой альбом"])
        photo = self.database.one(
            "SELECT media_type, library_root, parent_relative_path, index_state "
            "FROM media WHERE relative_path='2024/Отпуск/От друзей/photo.jpg'"
        )
        self.assertEqual(photo["media_type"], "photo")
        self.assertEqual(photo["library_root"], "photos")
        self.assertEqual(photo["parent_relative_path"], "2024/Отпуск/От друзей")
        self.assertEqual(photo["index_state"], "indexed")

    def test_indexes_named_and_range_shelves_without_moving_files(self):
        named = self.root / "до 2013 года" / "Семья"
        ranged = self.root / "2013-2017" / "Отпуск"
        named.mkdir(parents=True)
        ranged.mkdir(parents=True)
        (named / "one.jpg").write_bytes(b"one")
        (ranged / "two.jpg").write_bytes(b"two")
        report = self.indexer.scan()
        self.assertEqual(report.diagnostics, ())
        self.assertEqual(report.containers, 2)
        self.assertEqual([row["year"] for row in self.database.all("SELECT year FROM containers ORDER BY year")], ["2013-2017", "до 2013 года"])
        self.assertEqual((named / "one.jpg").read_bytes(), b"one")
    def test_rescan_marks_missing(self):
        path = self.root / "2024" / "photo.jpg"
        path.parent.mkdir()
        path.write_bytes(b"photo")
        first = self.indexer.scan()
        path.unlink()

        report = self.indexer.scan()

        self.assertEqual(first.diagnostics, ())
        self.assertEqual(report.missing, 1)
        self.assertEqual(
            self.database.one(
                "SELECT index_state FROM media WHERE relative_path='2024/photo.jpg'"
            )["index_state"],
            "missing",
        )


if __name__ == "__main__":
    unittest.main()
