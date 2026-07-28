from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from .config import Config
from .db import Database
from .security import safe_path

logger = logging.getLogger(__name__)


class AlbumRenameError(ValueError):
    pass


def normalize_single_visible_folder_name(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise AlbumRenameError(f"Введите {field_name}")
    if normalized in {".", ".."} or normalized.startswith("."):
        raise AlbumRenameError(f"Недопустимое {field_name}")
    if "/" in normalized or "\\" in normalized:
        raise AlbumRenameError(f"В {field_name} нельзя использовать символы / и \\")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise AlbumRenameError(f"В {field_name} есть недопустимые символы")
    if any(character in normalized for character in (":", "*", "?", '"', "<", ">", "|")):
        raise AlbumRenameError(f"В {field_name} есть недопустимые символы")
    return normalized


class AlbumRenamer:
    def __init__(self, database: Database, config: Config):
        self.database = database
        self.config = config

    def rename(self, container_id: int, requested_name: str) -> dict[str, object]:
        name = normalize_single_visible_folder_name(requested_name, "название альбома")
        root = self.config.photos_root.resolve()
        source: Path | None = None
        target: Path | None = None
        renamed = False
        try:
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                album = connection.execute(
                    """
                    SELECT * FROM containers WHERE id=? AND library_root='photos'
                      AND media_type='photo' AND kind='album' AND missing_since IS NULL
                    """,
                    (container_id,),
                ).fetchone()
                if album is None:
                    raise AlbumRenameError("Альбом не найден")
                old_relative = str(album["relative_path"])
                old_name = str(album["name"])
                if name == old_name:
                    return {key: album[key] for key in album.keys()}

                source = safe_path(root, old_relative)
                target_relative = f"{album['year']}/{name}"
                target = safe_path(root, target_relative)
                if source.parent != target.parent or not source.is_dir() or source.is_symlink():
                    raise AlbumRenameError("Папка альбома не найдена. Обновите библиотеку и проверьте файлы на сервере")
                if target.exists() and target != source:
                    raise AlbumRenameError("Альбом с таким названием уже существует на этой полке")
                conflict = connection.execute(
                    """
                    SELECT id FROM containers WHERE library_root='photos' AND media_type='photo'
                      AND kind='album' AND year=? AND name=? COLLATE NOCASE
                      AND id<>? AND missing_since IS NULL LIMIT 1
                    """,
                    (album["year"], name, container_id),
                ).fetchone()
                if conflict is not None:
                    raise AlbumRenameError("Альбом с таким названием уже существует на этой полке")

                if name.casefold() == old_name.casefold():
                    temporary = source.with_name(f".photo-review-rename-{uuid.uuid4().hex}")
                    os.rename(source, temporary)
                    try:
                        os.rename(temporary, target)
                    except Exception:
                        os.rename(temporary, source)
                        raise
                else:
                    os.rename(source, target)
                renamed = True

                prefix_length = len(old_relative) + 1
                connection.execute(
                    "UPDATE containers SET name=?, relative_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (name, target_relative, container_id),
                )
                connection.execute(
                    """
                    UPDATE media SET relative_path=? || substr(relative_path, ?),
                      parent_relative_path=? || substr(parent_relative_path, ?),
                      updated_at=CURRENT_TIMESTAMP
                    WHERE container_id=? AND library_root='photos'
                      AND (relative_path=? OR relative_path LIKE ?)
                    """,
                    (target_relative, prefix_length, target_relative, prefix_length, container_id,
                     old_relative, f"{old_relative}/%"),
                )
                updated = connection.execute("SELECT * FROM containers WHERE id=?", (container_id,)).fetchone()
                return {key: updated[key] for key in updated.keys()}
        except AlbumRenameError:
            raise
        except Exception as exc:
            if renamed and source is not None and target is not None:
                try:
                    if target.exists() and not source.exists():
                        os.rename(target, source)
                except Exception:
                    logger.critical("album rename rollback failed album_id=%s", container_id, exc_info=True)
                    raise AlbumRenameError("Переименование не завершено из-за ошибки хранилища. Подробности записаны в журнал") from exc
            logger.exception("album rename failed album_id=%s", container_id)
            raise AlbumRenameError("Не удалось завершить переименование. Изменения отменены") from exc
