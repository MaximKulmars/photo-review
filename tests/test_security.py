import tempfile
import unittest
from pathlib import Path

from app.security import password_matches, safe_path


class SecurityTests(unittest.TestCase):
    def test_password_comparison(self):
        self.assertTrue(password_matches("секрет", "секрет"))
        self.assertFalse(password_matches("другой", "секрет"))

    def test_safe_path_stays_inside_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(
                safe_path(root, "2024/photo.jpg"), root / "2024/photo.jpg"
            )

    def test_safe_path_rejects_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                safe_path(Path(directory), "../outside.jpg")


if __name__ == "__main__":
    unittest.main()
