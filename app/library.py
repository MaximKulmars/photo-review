from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path

from .db import Database


PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}


@dataclass(frozen=True)
class ScanReport:
    library_root: str
    media_type: str
    indexed: int
    unchanged: int
    missing: int
    containers: int
    diagnostics: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "library_root": self.library_root,
            "media_type": self.media_type,
            "indexed": self.indexed,
            "unchanged": self.unchanged,
            "missing": self.missing,
            "containers": self.containers,
            "diagnostics": list(self.diagnostics),
        }


class LibraryIndexer:
    """Indexes the filesystem structure without analysing or moving originals."""

    def __init__(self, database: Database, roots: dict[str, Path]):
        self.database = database
        self.roots = {name: path.resolve() for name, path in roots.items()}

    def scan(self, library_root: str = "photos") -> ScanReport:
        root = self.roots.get(library_root)
        if root is None:
            raise ValueError("Корень библиотеки не настроен")
        if not root.is_dir():
            raise FileNotFoundError("Корень библиотеки не найден")
        media_type = "photo" if library_root == "photos" else "video"
        extensions = PHOTO_EXTENSIONS if media_type == "photo" else VIDEO_EXTENSIONS
        seen_paths: set[str] = set()
        seen_containers: set[str] = set()
        diagnostics: list[str] = []
        indexed = unchanged = containers = 0

        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE media SET index_state='missing',
                    missing_since=COALESCE(missing_since, CURRENT_TIMESTAMP)
                WHERE library_root=? AND media_type=? AND status='active'
                """,
                (library_root, media_type),
            )
            connection.execute(
                """
                UPDATE containers SET missing_since=COALESCE(
                    missing_since, CURRENT_TIMESTAMP
                )
                WHERE library_root=? AND media_type=?
                """,
                (library_root, media_type),
            )

            for year_folder in self._visible_directories(root):
                shelf_name = year_folder.name
                if shelf_name == "Unsorted":
                    indexed += self._index_tree(
                        connection, root, year_folder, library_root, media_type,
                        extensions, None, seen_paths
                    )
                    continue
                for child in self._visible_directories(year_folder):
                    container_id = self._upsert_container(
                        connection, library_root, media_type, shelf_name, child, root
                    )
                    seen_containers.add(child.relative_to(root).as_posix())
                    containers += 1
                    indexed += self._index_tree(
                        connection, root, child, library_root, media_type,
                        extensions, container_id, seen_paths
                    )
                for file_path in self._visible_files(year_folder):
                    if file_path.suffix.lower() in extensions:
                        changed = self._upsert_media(
                            connection, root, file_path, library_root, media_type, None
                        )
                        seen_paths.add(file_path.relative_to(root).as_posix())
                        indexed += int(changed)
                        unchanged += int(not changed)

            for relative_path in seen_paths:
                connection.execute(
                    """
                    UPDATE media SET index_state='indexed', missing_since=NULL
                    WHERE library_root=? AND relative_path=? AND status='active'
                    """,
                    (library_root, relative_path),
                )
            for relative_path in seen_containers:
                connection.execute(
                    """
                    UPDATE containers SET missing_since=NULL, updated_at=CURRENT_TIMESTAMP
                    WHERE library_root=? AND media_type=? AND relative_path=?
                    """,
                    (library_root, media_type, relative_path),
                )
            missing = connection.execute(
                """
                SELECT COUNT(*) FROM media
                WHERE library_root=? AND media_type=? AND index_state='missing'
                """,
                (library_root, media_type),
            ).fetchone()[0]
        return ScanReport(
            library_root, media_type, indexed, unchanged, int(missing),
            containers, tuple(diagnostics)
        )

    def _index_tree(
        self, connection, root: Path, folder: Path, library_root: str,
        media_type: str, extensions: set[str], container_id: int | None,
        seen_paths: set[str],
    ) -> int:
        indexed = 0
        for current, directories, files in os.walk(folder, followlinks=False):
            current_path = Path(current)
            directories[:] = [
                name for name in directories
                if not name.startswith(".") and not (current_path / name).is_symlink()
            ]
            for name in files:
                path = current_path / name
                if path.is_symlink() or path.suffix.lower() not in extensions:
                    continue
                self._upsert_media(
                    connection, root, path, library_root, media_type, container_id
                )
                seen_paths.add(path.relative_to(root).as_posix())
                indexed += 1
        return indexed

    @staticmethod
    def _visible_directories(path: Path) -> list[Path]:
        return sorted(
            (child for child in path.iterdir()
             if child.is_dir() and not child.is_symlink() and not child.name.startswith(".")),
            key=lambda child: child.name.casefold(),
        )

    @staticmethod
    def _visible_files(path: Path) -> list[Path]:
        return [
            child for child in path.iterdir()
            if child.is_file() and not child.is_symlink() and not child.name.startswith(".")
        ]

    def _upsert_container(
        self, connection, library_root: str, media_type: str, year: str,
        folder: Path, root: Path,
    ) -> int:
        relative = folder.relative_to(root).as_posix()
        connection.execute(
            """
            INSERT INTO containers(
                library_root, media_type, kind, year, relative_path, name, missing_since
            ) VALUES (?, ?, 'album', ?, ?, ?, NULL)
            ON CONFLICT(library_root, media_type, kind, relative_path)
            DO UPDATE SET year=excluded.year, name=excluded.name,
                missing_since=NULL, updated_at=CURRENT_TIMESTAMP
            """,
            (library_root, media_type, year, relative, folder.name),
        )
        return int(connection.execute(
            """
            SELECT id FROM containers
            WHERE library_root=? AND media_type=? AND kind='album' AND relative_path=?
            """,
            (library_root, media_type, relative),
        ).fetchone()[0])

    def _upsert_media(
        self, connection, root: Path, path: Path, library_root: str,
        media_type: str, container_id: int | None,
    ) -> bool:
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        parent = path.parent.relative_to(root).as_posix()
        row = connection.execute(
            """
            SELECT id, size, mtime_ns, file_name, parent_relative_path, container_id
            FROM media WHERE library_root=? AND relative_path=? AND status='active'
            """,
            (library_root, relative),
        ).fetchone()
        mime_type = mimetypes.guess_type(path.name)[0]
        values = (
            media_type, library_root, path.name, parent, mime_type, container_id,
            stat.st_size, stat.st_mtime_ns, relative,
        )
        if row:
            changed = (
                row["size"] != stat.st_size or row["mtime_ns"] != stat.st_mtime_ns
                or row["file_name"] != path.name
                or row["parent_relative_path"] != parent
                or row["container_id"] != container_id
            )
            connection.execute(
                """
                UPDATE media SET media_type=?, library_root=?, file_name=?,
                    parent_relative_path=?, mime_type=?, container_id=?, size=?,
                    mtime_ns=?, index_state='indexed', missing_since=NULL,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (*values[:-1], row["id"]),
            )
            return changed
        connection.execute(
            """
            INSERT INTO media(
                relative_path, size, mtime_ns, media_type, library_root, file_name,
                parent_relative_path, mime_type, container_id, index_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'indexed')
            """,
            (
                relative, stat.st_size, stat.st_mtime_ns, media_type, library_root,
                path.name, parent, mime_type, container_id,
            ),
        )
        return True
