from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from .db import Database
from .security import safe_path


class Storage:
    def __init__(self, photos: Path, quarantine: Path, db: Database):
        self.photos = photos.resolve()
        self.quarantine = quarantine.resolve()
        self.db = db

    @staticmethod
    def checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def quarantine_media(self, media_id: int) -> str:
        row = self.db.one(
            "SELECT * FROM media WHERE id=? AND status='active'", (media_id,)
        )
        if not row:
            raise FileNotFoundError("Фотография не найдена или уже перемещена")
        source = safe_path(self.photos, row["relative_path"])
        if not source.is_file() or source.is_symlink():
            raise FileNotFoundError("Исходный файл не найден")

        destination = safe_path(self.quarantine, row["relative_path"])
        destination = self._unique_destination(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._safe_move(source, destination, row["sha256"])
        quarantine_relative = destination.relative_to(self.quarantine).as_posix()
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE media SET status='quarantine', quarantine_path=?,
                    updated_at=CURRENT_TIMESTAMP WHERE id=?
                """,
                (quarantine_relative, media_id),
            )
            connection.execute(
                "UPDATE findings SET decision='quarantine' WHERE media_id=?",
                (media_id,),
            )
            connection.execute(
                "INSERT INTO audit_log(action, relative_path, details) VALUES(?,?,?)",
                ("quarantine", row["relative_path"], quarantine_relative),
            )
        return quarantine_relative

    def restore_media(self, media_id: int, rename_on_conflict: bool = False) -> str:
        row = self.db.one(
            "SELECT * FROM media WHERE id=? AND status='quarantine'", (media_id,)
        )
        if not row:
            raise FileNotFoundError("Файл не найден в карантине")
        source = safe_path(self.quarantine, row["quarantine_path"])
        destination = safe_path(self.photos, row["relative_path"])
        if destination.exists():
            if not rename_on_conflict:
                raise FileExistsError("В архиве уже есть файл с таким именем")
            destination = self._unique_destination(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._safe_move(source, destination, row["sha256"])
        restored_relative = destination.relative_to(self.photos).as_posix()
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE media SET relative_path=?, status='active',
                    quarantine_path=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?
                """,
                (restored_relative, media_id),
            )
            connection.execute(
                "UPDATE findings SET decision='later' WHERE media_id=?", (media_id,)
            )
            connection.execute(
                "INSERT INTO audit_log(action, relative_path, details) VALUES(?,?,?)",
                ("restore", restored_relative, row["quarantine_path"]),
            )
        return restored_relative

    def delete_media(self, media_id: int) -> None:
        row = self.db.one(
            "SELECT * FROM media WHERE id=? AND status='quarantine'", (media_id,)
        )
        if not row:
            raise FileNotFoundError("Файл не найден в карантине")
        path = safe_path(self.quarantine, row["quarantine_path"])
        if path.is_file() and not path.is_symlink():
            path.unlink()
        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO audit_log(action, relative_path, details) VALUES(?,?,?)",
                ("delete", row["relative_path"], row["quarantine_path"]),
            )
            connection.execute("DELETE FROM media WHERE id=?", (media_id,))
        self._remove_empty_parents(path.parent)

    def copy_media(self, media_id: int, destination_folder: str, rename_on_conflict: bool = False) -> str:
        row, source, destination = self._transfer_paths(
            media_id, destination_folder, rename_on_conflict
        )
        self._safe_copy(source, destination, row["sha256"])
        relative = destination.relative_to(self.photos).as_posix()
        self.db.execute(
            "INSERT INTO audit_log(action,relative_path,details) VALUES(?,?,?)",
            ("copy", row["relative_path"], relative),
        )
        return relative

    def move_media(self, media_id: int, destination_folder: str, rename_on_conflict: bool = False) -> str:
        row, source, destination = self._transfer_paths(
            media_id, destination_folder, rename_on_conflict
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._safe_move(source, destination, row["sha256"])
        relative = destination.relative_to(self.photos).as_posix()
        stat = destination.stat()
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE media SET relative_path=?, size=?, mtime_ns=?,
                    last_scan_job_id=NULL, manual_quality=0,
                    updated_at=CURRENT_TIMESTAMP WHERE id=?
                """,
                (relative, stat.st_size, stat.st_mtime_ns, media_id),
            )
            connection.execute("DELETE FROM findings WHERE media_id=?", (media_id,))
            connection.execute(
                "INSERT INTO audit_log(action,relative_path,details) VALUES(?,?,?)",
                ("move", row["relative_path"], relative),
            )
        return relative

    def _transfer_paths(self, media_id: int, destination_folder: str, rename_on_conflict: bool):
        row = self.db.one("SELECT * FROM media WHERE id=? AND status='active'", (media_id,))
        if not row:
            raise FileNotFoundError("Фотография не найдена")
        source = safe_path(self.photos, row["relative_path"])
        if not source.is_file() or source.is_symlink():
            raise FileNotFoundError("Исходный файл не найден")
        folder = safe_path(self.photos, destination_folder)
        if not folder.is_dir() or folder.is_symlink():
            raise FileNotFoundError("Папка назначения не найдена")
        destination = safe_path(self.photos, f"{destination_folder.rstrip('/')}/{source.name}" if destination_folder else source.name)
        if destination == source:
            raise ValueError("Исходная папка уже является папкой назначения")
        if destination.exists():
            if not rename_on_conflict:
                raise FileExistsError("В папке назначения уже есть файл с таким именем")
            destination = self._unique_destination(destination)
        return row, source, destination

    def _safe_move(
        self, source: Path, destination: Path, expected_sha256: str | None
    ) -> None:
        try:
            os.replace(source, destination)
            return
        except OSError:
            pass

        partial = destination.with_name(destination.name + ".partial")
        try:
            shutil.copy2(source, partial)
            actual = self.checksum(partial)
            expected = expected_sha256 or self.checksum(source)
            if actual != expected:
                raise OSError("Контрольная сумма копии не совпала")
            os.replace(partial, destination)
            source.unlink()
        finally:
            if partial.exists():
                partial.unlink()

    def _safe_copy(
        self, source: Path, destination: Path, expected_sha256: str | None
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".partial")
        try:
            shutil.copy2(source, partial)
            actual = self.checksum(partial)
            expected = expected_sha256 or self.checksum(source)
            if actual != expected:
                raise OSError("Контрольная сумма копии не совпала")
            os.replace(partial, destination)
        finally:
            if partial.exists():
                partial.unlink()

    @staticmethod
    def _unique_destination(path: Path) -> Path:
        if not path.exists():
            return path
        counter = 1
        while True:
            candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
            if not candidate.exists():
                return candidate
            counter += 1

    def _remove_empty_parents(self, start: Path) -> None:
        current = start
        while current != self.quarantine and current.is_relative_to(self.quarantine):
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent
