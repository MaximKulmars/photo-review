import tempfile
import unittest
from pathlib import Path

from PIL import Image

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Config
from app.db import Database
from app.library import LibraryIndexer
from app.library_api import install_library_api


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

    def test_scan_extracts_exif_capture_date_for_unsorted_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photos = root / "photos"
            target = photos / "Unsorted" / "Camera" / "old.jpg"
            target.parent.mkdir(parents=True)

            image = Image.new("RGB", (8, 8), "white")
            exif = Image.Exif()
            exif[36867] = "2022:05:04 10:30:00"
            image.save(target, exif=exif)

            database = Database(root / "data.sqlite3")
            database.initialize()
            LibraryIndexer(database, {"photos": photos}).scan("photos")

            row = database.one(
                "SELECT captured_at, date_source FROM media WHERE relative_path=?",
                ("Unsorted/Camera/old.jpg",),
            )
            self.assertEqual(row["captured_at"], "2022-05-04T10:30:00")
            self.assertEqual(row["date_source"], "metadata")

    def test_scan_indexes_standard_photo_formats(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photos = root / "photos"
            target = photos / "Unsorted" / "Formats"
            target.mkdir(parents=True)
            for suffix, image_format in {
                ".gif": "GIF",
                ".bmp": "BMP",
                ".tif": "TIFF",
                ".tiff": "TIFF",
            }.items():
                Image.new("RGB", (8, 8), "white").save(target / f"sample{suffix}", image_format)

            database = Database(root / "data.sqlite3")
            database.initialize()
            report = LibraryIndexer(database, {"photos": photos}).scan("photos")

            self.assertEqual(report.indexed, 4)
            rows = database.all(
                """
                SELECT relative_path FROM media
                WHERE collection_state='unsorted' AND index_state='indexed'
                ORDER BY relative_path
                """
            )
            self.assertEqual(
                [row["relative_path"] for row in rows],
                [
                    "Unsorted/Formats/sample.bmp",
                    "Unsorted/Formats/sample.gif",
                    "Unsorted/Formats/sample.tif",
                    "Unsorted/Formats/sample.tiff",
                ],
            )

    def test_api_creates_album_from_unsorted_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photos = root / "photos"
            quarantine = root / "quarantine"
            data = root / "data"
            unsorted_file = photos / "Unsorted" / "Manual Import" / "photo.jpg"
            (photos / "2026").mkdir(parents=True)
            quarantine.mkdir()
            data.mkdir()
            unsorted_file.parent.mkdir(parents=True)
            Image.new("RGB", (8, 8), "white").save(unsorted_file, "JPEG")

            database = Database(data / "data.sqlite3")
            database.initialize()
            indexer = LibraryIndexer(database, {"photos": photos})
            indexer.scan("photos")
            media_id = database.one(
                "SELECT id FROM media WHERE relative_path=?",
                ("Unsorted/Manual Import/photo.jpg",),
            )["id"]
            app = FastAPI()
            config = Config(
                photos_root=photos,
                videos_root=None,
                quarantine_root=quarantine,
                data_root=data,
                password="",
                session_secret="",
                auth_enabled=False,
                port=0,
                upload_max_files=50,
                upload_max_file_bytes=1024 * 1024,
                upload_max_total_bytes=10 * 1024 * 1024,
            )
            install_library_api(app, database, indexer, lambda: True, config)

            client = TestClient(app)
            response = client.post(
                "/api/library/unsorted/create-album",
                json={"year": "2026", "name": "Trip", "media_ids": [media_id]},
            )

            self.assertEqual(response.status_code, 201)
            body = response.json()
            self.assertEqual(body["album"]["relative_path"], "2026/Trip")
            self.assertEqual(body["completed"], [{"media_id": media_id, "path": "2026/Trip/photo.jpg"}])
            self.assertFalse(unsorted_file.exists())
            self.assertTrue((photos / "2026" / "Trip" / "photo.jpg").is_file())
            row = database.one("SELECT * FROM media WHERE id=?", (media_id,))
            self.assertEqual(row["collection_state"], "album")
            self.assertEqual(row["source_name"], "Manual Import")
            self.assertEqual(row["container_id"], body["album"]["id"])

    def test_api_updates_unsorted_capture_date(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photos = root / "photos"
            quarantine = root / "quarantine"
            data = root / "data"
            unsorted_file = photos / "Unsorted" / "Manual Import" / "scan.jpg"
            quarantine.mkdir(parents=True)
            data.mkdir()
            unsorted_file.parent.mkdir(parents=True)
            Image.new("RGB", (8, 8), "white").save(unsorted_file, "JPEG")

            database = Database(data / "data.sqlite3")
            database.initialize()
            indexer = LibraryIndexer(database, {"photos": photos})
            indexer.scan("photos")
            media_id = database.one(
                "SELECT id FROM media WHERE relative_path=?",
                ("Unsorted/Manual Import/scan.jpg",),
            )["id"]
            app = FastAPI()
            config = Config(
                photos_root=photos,
                videos_root=None,
                quarantine_root=quarantine,
                data_root=data,
                password="",
                session_secret="",
                auth_enabled=False,
                port=0,
                upload_max_files=50,
                upload_max_file_bytes=1024 * 1024,
                upload_max_total_bytes=10 * 1024 * 1024,
            )
            install_library_api(app, database, indexer, lambda: True, config)

            client = TestClient(app)
            response = client.patch(
                f"/api/library/unsorted/{media_id}/captured-at",
                json={"captured_at": "2020-02-03T04:05:06"},
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["captured_at"], "2020-02-03T04:05:06")
            row = database.one("SELECT captured_at, date_source FROM media WHERE id=?", (media_id,))
            self.assertEqual(row["captured_at"], "2020-02-03T04:05:06")
            self.assertEqual(row["date_source"], "manual")
            filtered = client.get("/api/library/unsorted?year=2020")
            self.assertEqual(filtered.status_code, 200)
            self.assertEqual(filtered.json()["total"], 1)


if __name__ == "__main__":
    unittest.main()
