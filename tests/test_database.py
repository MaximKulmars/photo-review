import tempfile
import unittest
from pathlib import Path

from app.db import Database


class DatabaseTests(unittest.TestCase):
    def test_database_initializes_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "data.sqlite3")
            database.initialize()
            settings = database.settings()
            self.assertEqual(settings["analysis_revision"], 1)
            self.assertEqual(settings["sensitivity"], "careful")

    def test_saving_settings_increments_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "data.sqlite3")
            database.initialize()
            saved = database.save_settings(
                {"blur_threshold": 70, "unknown": "ignored"}
            )
            self.assertEqual(saved["blur_threshold"], 70)
            self.assertEqual(saved["analysis_revision"], 2)
            self.assertNotIn("unknown", saved)


if __name__ == "__main__":
    unittest.main()
