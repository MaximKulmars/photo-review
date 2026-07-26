import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from app.analyzer import JobManager, inspect_image
from app.db import Database


class AnalyzerTests(unittest.TestCase):
    def test_inspect_image_creates_thumbnail_and_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.png"
            image = Image.new("RGB", (640, 360), "white")
            draw = ImageDraw.Draw(image)
            for offset in range(20, 600, 40):
                draw.line((offset, 20, offset, 340), fill="black", width=3)
            image.save(source)
            thumbnail = root / "thumb.jpg"
            settings = Database(root / "settings.sqlite3")
            settings.initialize()

            result = inspect_image(source, settings.settings(), thumbnail)

            self.assertEqual(result["width"], 640)
            self.assertEqual(result["height"], 360)
            self.assertEqual(len(result["phash"]), 16)
            self.assertTrue(thumbnail.is_file())

    def test_job_finds_exact_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photos = root / "photos"
            photos.mkdir()
            first = photos / "one.jpg"
            second = photos / "copy.jpg"
            Image.new("RGB", (320, 240), "#627b69").save(first, quality=90)
            second.write_bytes(first.read_bytes())

            database = Database(root / "data.sqlite3")
            database.initialize()
            manager = JobManager(database, photos, root / "thumbnails")
            job_id = database.execute(
                """
                INSERT INTO jobs(scope,duplicate_scope,state)
                VALUES('','scope','queued')
                """
            )

            manager._run_job(job_id)

            job = database.one("SELECT * FROM jobs WHERE id=?", (job_id,))
            self.assertEqual(job["state"], "completed")
            self.assertEqual(job["processed"], 2)
            findings = database.all(
                "SELECT * FROM findings WHERE category='exact'"
            )
            self.assertEqual(len(findings), 2)


if __name__ == "__main__":
    unittest.main()
