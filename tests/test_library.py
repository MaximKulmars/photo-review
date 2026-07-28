import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.library import LibraryIndexer


class LibraryIndexerTests(unittest.TestCase):
    def test_unsorted_files_are_indexed_with_source_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photos = root / "photos"
            camera = photos / "Unsorted" / "Maxim Phone" / "DCIM" / "Camera"
            camera.mkdir(parents=True)
            (camera / "IMG_001.jpg").write_bytes(b"photo")
            (photos / "Unsorted" / "loose.jpg").write_bytes(b"photo")
            album = photos / "2026" / "Trip"
            album.mkdir(parents=True)
            (album / "album.jpg").write_bytes(b"photo")

            database = Database(root / "data.sqlite3")
            database.initialize()
            indexer = LibraryIndexer(database, {"photos": photos})

            report = indexer.scan("photos")

            self.assertEqual(report.indexed, 3)
            sourced = database.one(
                "SELECT * FROM media WHERE relative_path=?",
                ("Unsorted/Maxim Phone/DCIM/Camera/IMG_001.jpg",),
            )
            self.assertEqual(sourced["collection_state"], "unsorted")
            self.assertEqual(sourced["source_name"], "Maxim Phone")
            self.assertEqual(sourced["source_relative_path"], "Maxim Phone/DCIM/Camera")
            self.assertIsNotNone(sourced["imported_at"])
            self.assertEqual(sourced["date_source"], "import")

            loose = database.one(
                "SELECT source_name, source_relative_path FROM media WHERE relative_path=?",
                ("Unsorted/loose.jpg",),
            )
            self.assertIsNone(loose["source_name"])
            self.assertIsNone(loose["source_relative_path"])

            album_photo = database.one(
                "SELECT collection_state, container_id FROM media WHERE relative_path=?",
                ("2026/Trip/album.jpg",),
            )
            self.assertEqual(album_photo["collection_state"], "album")
            self.assertIsNotNone(album_photo["container_id"])


if __name__ == "__main__":
    unittest.main()
