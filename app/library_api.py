from __future__ import annotations

import sqlite3
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import Config
from .uploads import install_upload_api
from .db import Database
from .album_service import AlbumRenameError, AlbumRenamer, normalize_single_visible_folder_name
from .library import LibraryIndexer


class ScanRequest(BaseModel):
    library_root: Literal["photos", "videos"] = "photos"


class AlbumCreateRequest(BaseModel):
    year: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)


class AlbumRenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class UnsortedToAlbumRequest(BaseModel):
    media_ids: list[int] = Field(min_length=1, max_length=500)
    container_id: int
    rename_on_conflict: bool = False


class UnsortedNewAlbumRequest(BaseModel):
    media_ids: list[int] = Field(min_length=1, max_length=500)
    year: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    rename_on_conflict: bool = False


class ManualCaptureDateRequest(BaseModel):
    captured_at: str | None = None


class MediaIdsRequest(BaseModel):
    media_ids: list[int] = Field(min_length=1, max_length=500)


def _single_visible_folder_name(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(400, f"Введите {field_name}")
    if normalized in {".", ".."} or normalized.startswith("."):
        raise HTTPException(400, f"Недопустимое {field_name}")
    if "/" in normalized or "\\" in normalized:
        raise HTTPException(400, f"В {field_name} нельзя использовать символы / и \\")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise HTTPException(400, f"В {field_name} есть недопустимые символы")
    return normalized


def _folder_inside(root: Path, name: str) -> Path:
    target = root / name
    try:
        target.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise HTTPException(400, "Недопустимый путь") from exc
    return target


def _effective_unsorted_date(item: dict[str, object]) -> tuple[str, int, int | None]:
    captured_at = item.get("captured_at")
    imported_at = str(item.get("imported_at") or "")
    if captured_at:
        date = str(captured_at)
        parsed = datetime.fromisoformat(date)
        return date, parsed.year, parsed.month

    path = "/".join(
        str(value or "")
        for value in (item.get("source_relative_path"), item.get("relative_path"))
    )
    match = re.search(r"(?<!\d)((?:19|20)\d{2})(?:[^\d]+(0?[1-9]|1[0-2]))?", path)
    if match:
        year = int(match.group(1))
        month = int(match.group(2)) if match.group(2) else None
        return f"{year:04d}-{month or 1:02d}-01T00:00:00", year, month

    parsed = datetime.fromisoformat(imported_at)
    return imported_at, parsed.year, parsed.month


def _manual_capture_date(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise HTTPException(400, "Недопустимая дата съёмки") from exc
    now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
    if parsed.year < 1800 or parsed > now:
        raise HTTPException(400, "Дата съёмки вне допустимого диапазона")
    return parsed.replace(microsecond=0).isoformat()


def install_library_api(
    app: FastAPI, database: Database, indexer: LibraryIndexer, require_login, config: Config
) -> None:
    dependencies = [Depends(require_login)]

    install_upload_api(app, database, indexer, require_login, config)
    album_renamer = AlbumRenamer(database, config)

    @app.post("/api/library/scan", dependencies=dependencies)
    def scan_library(payload: ScanRequest):
        try:
            return indexer.scan(payload.library_root).as_dict()
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.patch("/api/library/albums/{container_id}", dependencies=dependencies)
    def rename_album(container_id: int, payload: AlbumRenameRequest):
        try:
            return album_renamer.rename(container_id, payload.name)
        except AlbumRenameError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/library/shelves", dependencies=dependencies)
    def shelves(library_root: Literal["photos", "videos"] = "photos"):
        rows = database.all(
            """
            SELECT substr(relative_path, 1, instr(relative_path || '/', '/') - 1) AS year,
              COUNT(*) AS media_count, MIN(id) AS cover_media_id
            FROM media
            WHERE library_root=? AND media_type=?
              AND index_state='indexed' AND status='active'
              AND collection_state='album'
            GROUP BY year
            ORDER BY year
            """,
            (library_root, "photo" if library_root == "photos" else "video"),
        )
        containers = database.all(
            """
            SELECT year, COUNT(*) AS album_count FROM containers
            WHERE library_root=? AND media_type=? AND missing_since IS NULL
            GROUP BY year
            """,
            (library_root, "photo" if library_root == "photos" else "video"),
        )
        album_counts = {row["year"]: row["album_count"] for row in containers}
        media_counts = {row["year"]: row["media_count"] for row in rows}
        cover_ids = {row["year"]: row["cover_media_id"] for row in rows}
        years = sorted(set(media_counts) | set(album_counts))
        return {
            "items": [
                {
                    "year": year,
                    "media_count": media_counts.get(year, 0),
                    "album_count": album_counts.get(year, 0),
                    "cover_media_id": cover_ids.get(year),
                }
                for year in years
            ]
        }

    @app.get("/api/library/albums", dependencies=dependencies)
    def albums(year: str, library_root: Literal["photos", "videos"] = "photos"):
        media_type = "photo" if library_root == "photos" else "video"
        rows = database.all(
            """
            SELECT c.*, COUNT(m.id) AS media_count, MIN(m.id) AS cover_media_id
            FROM containers c LEFT JOIN media m ON m.container_id=c.id
              AND m.index_state='indexed' AND m.status='active'
              AND m.collection_state='album'
            WHERE c.library_root=? AND c.media_type=? AND c.year=?
              AND c.missing_since IS NULL
            GROUP BY c.id ORDER BY c.name COLLATE NOCASE
            """,
            (library_root, media_type, year),
        )
        return {"items": [{key: row[key] for key in row.keys()} for row in rows]}

    @app.get("/api/library/unsorted/sources", dependencies=dependencies)
    def unsorted_sources():
        rows = database.all(
            """
            SELECT source_name, COUNT(*) AS count FROM media
            WHERE library_root='photos' AND media_type='photo'
              AND collection_state='unsorted' AND index_state='indexed'
              AND status='active'
            GROUP BY source_name
            ORDER BY source_name IS NOT NULL, source_name COLLATE NOCASE
            """
        )
        return {
            "items": [
                {
                    "source_name": row["source_name"],
                    "label": row["source_name"] or "Без источника",
                    "count": row["count"],
                }
                for row in rows
            ]
        }

    @app.get("/api/library/unsorted", dependencies=dependencies)
    def unsorted_media(
        source_name: str | None = None,
        year: int | None = None,
        month: int | None = None,
        date_status: Literal["all", "captured", "missing"] = "all",
        sort: Literal["desc", "asc"] = "desc",
        page: int = 1,
        page_size: int = 48,
    ):
        where = [
            "library_root='photos'",
            "media_type='photo'",
            "collection_state='unsorted'",
            "index_state='indexed'",
            "status='active'",
        ]
        params: list[object] = []
        if source_name is not None:
            if source_name == "":
                where.append("source_name IS NULL")
            else:
                where.append("source_name=?")
                params.append(source_name)
        if date_status == "captured":
            where.append("captured_at IS NOT NULL")
        elif date_status == "missing":
            where.append("captured_at IS NULL")
        effective = "COALESCE(captured_at, imported_at)"
        if month is not None and (month < 1 or month > 12):
            raise HTTPException(400, "Недопустимый месяц")
        clause = " AND ".join(where)
        page, page_size = max(page, 1), min(max(page_size, 1), 200)
        rows = database.all(
            f"""
            SELECT id, relative_path, file_name, parent_relative_path, mime_type,
              size, width, height, captured_at, imported_at, date_source,
              source_name, source_relative_path, {effective} AS effective_date
            FROM media WHERE {clause}
            ORDER BY relative_path COLLATE NOCASE
            """,
            tuple(params),
        )
        items = [{key: row[key] for key in row.keys()} for row in rows]
        for item in items:
            effective_date, effective_year, effective_month = _effective_unsorted_date(item)
            item["effective_date"] = effective_date
            item["effective_year"] = effective_year
            item["effective_month"] = effective_month
        facet_items = items
        if year is not None:
            items = [item for item in items if item["effective_year"] == year]
        if month is not None:
            items = [item for item in items if item["effective_month"] == month]
        items.sort(
            key=lambda item: (str(item["effective_date"]), str(item["relative_path"]).casefold()),
            reverse=sort == "desc",
        )
        total = len(items)
        page_items = items[(page - 1) * page_size: page * page_size]
        years = sorted({int(item["effective_year"]) for item in facet_items}, reverse=True)
        months = sorted(
            {int(item["effective_month"]) for item in facet_items if item["effective_month"]},
            reverse=True,
        )
        return {
            "items": page_items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "facets": {
                "years": [{"year": year, "count": sum(1 for item in facet_items if item["effective_year"] == year)} for year in years],
                "months": [{"month": month, "count": sum(1 for item in facet_items if item["effective_month"] == month)} for month in months],
            },
        }

    @app.post("/api/library/unsorted/move-to-album", dependencies=dependencies)
    def move_unsorted_to_album(payload: UnsortedToAlbumRequest):
        from .storage import Storage

        row = database.one(
            """
            SELECT relative_path FROM containers
            WHERE id=? AND library_root='photos' AND media_type='photo'
              AND kind='album' AND missing_since IS NULL
            """,
            (payload.container_id,),
        )
        if row is None:
            raise HTTPException(404, "Альбом не найден")
        storage = Storage(config.photos_root, config.quarantine_root, database)
        completed, failures = [], []
        for media_id in payload.media_ids:
            media = database.one(
                """
                SELECT id FROM media
                WHERE id=? AND collection_state='unsorted' AND status='active'
                """,
                (media_id,),
            )
            if media is None:
                failures.append({"media_id": media_id, "error": "Фотография не найдена в «Неразобранном»"})
                continue
            try:
                completed.append({
                    "media_id": media_id,
                    "path": storage.move_media(media_id, row["relative_path"], payload.rename_on_conflict),
                })
            except (OSError, ValueError) as exc:
                failures.append({"media_id": media_id, "error": str(exc)})
        return JSONResponse(
            {"completed": completed, "failures": failures},
            status_code=200 if not failures else 409,
        )

    @app.post("/api/library/unsorted/quarantine", dependencies=dependencies)
    def quarantine_unsorted(payload: MediaIdsRequest):
        from .storage import Storage

        storage = Storage(config.photos_root, config.quarantine_root, database)
        moved, failures = [], []
        for media_id in payload.media_ids:
            media = database.one(
                """
                SELECT id FROM media
                WHERE id=? AND collection_state='unsorted' AND status='active'
                """,
                (media_id,),
            )
            if media is None:
                failures.append({"media_id": media_id, "error": "Фотография не найдена в «Неразобранном»"})
                continue
            try:
                moved.append({"media_id": media_id, "path": storage.quarantine_media(media_id)})
            except (OSError, ValueError) as exc:
                failures.append({"media_id": media_id, "error": str(exc)})
        return JSONResponse(
            {"moved": moved, "failures": failures},
            status_code=200 if not failures else 409,
        )

    @app.patch("/api/library/unsorted/{media_id}/captured-at", dependencies=dependencies)
    def update_unsorted_capture_date(media_id: int, payload: ManualCaptureDateRequest):
        captured_at = _manual_capture_date(payload.captured_at)
        row = database.one(
            """
            SELECT id FROM media
            WHERE id=? AND collection_state='unsorted' AND status='active'
            """,
            (media_id,),
        )
        if row is None:
            raise HTTPException(404, "Фотография не найдена в «Неразобранном»")
        database.execute(
            """
            UPDATE media SET captured_at=?, date_source=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (captured_at, "manual" if captured_at else "import", media_id),
        )
        updated = database.one(
            """
            SELECT id, relative_path, captured_at, imported_at, date_source
            FROM media WHERE id=?
            """,
            (media_id,),
        )
        return {key: updated[key] for key in updated.keys()}

    @app.post("/api/library/albums", dependencies=dependencies, status_code=201)
    def create_album(payload: AlbumCreateRequest):
        year = _single_visible_folder_name(payload.year, "название полки")
        name = _single_visible_folder_name(payload.name, "название альбома")
        root = config.library_roots.get("photos")
        if root is None:
            raise HTTPException(503, "Фототека не настроена")
        root = root.resolve()
        shelf = _folder_inside(root, year)
        if shelf.is_symlink() or not shelf.is_dir():
            raise HTTPException(404, f"Полка «{year}» не найдена")
        target = _folder_inside(shelf, name)
        if target.exists() or target.is_symlink():
            raise HTTPException(409, f"Альбом «{name}» уже существует на полке «{year}»")

        existing = database.one(
            """
            SELECT id FROM containers
            WHERE library_root='photos' AND media_type='photo' AND kind='album'
              AND year=? AND name=? COLLATE NOCASE AND missing_since IS NULL
            LIMIT 1
            """,
            (year, name),
        )
        if existing is not None:
            raise HTTPException(409, f"Альбом «{name}» уже существует на полке «{year}»")

        try:
            target.mkdir()
        except PermissionError as exc:
            raise HTTPException(403, f"Нет прав на создание альбома на полке «{year}»") from exc
        except OSError as exc:
            raise HTTPException(500, f"Не удалось создать папку альбома: {exc.strerror or 'ошибка файловой системы'}") from exc

        if not target.is_dir() or target.is_symlink():
            raise HTTPException(500, "Папка альбома была создана некорректно")
        try:
            relative_path = target.relative_to(root).as_posix()
        except ValueError as exc:
            target.rmdir()
            raise HTTPException(500, "Созданная папка находится вне фототеки") from exc

        try:
            with database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO containers(
                        library_root, media_type, kind, year, relative_path, name, missing_since
                    ) VALUES ('photos', 'photo', 'album', ?, ?, ?, NULL)
                    """,
                    (year, relative_path, name),
                )
                row = connection.execute(
                    """
                    SELECT c.*, 0 AS media_count, NULL AS cover_media_id
                    FROM containers c
                    WHERE c.library_root='photos' AND c.media_type='photo'
                      AND kind='album' AND relative_path=?
                    """,
                    (relative_path,),
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            try:
                target.rmdir()
            except OSError:
                pass
            raise HTTPException(409, f"Альбом «{name}» уже существует на полке «{year}»") from exc
        except Exception:
            try:
                target.rmdir()
            except OSError:
                pass
            raise

        if row is None:
            try:
                target.rmdir()
            except OSError:
                pass
            raise HTTPException(500, "Не удалось зарегистрировать созданный альбом")
        return {key: row[key] for key in row.keys()}

    @app.post("/api/library/unsorted/create-album", dependencies=dependencies, status_code=201)
    def create_album_from_unsorted(payload: UnsortedNewAlbumRequest):
        from .storage import Storage

        placeholders = ",".join("?" for _ in payload.media_ids)
        rows = database.all(
            f"""
            SELECT id FROM media
            WHERE id IN ({placeholders}) AND collection_state='unsorted' AND status='active'
            """,
            tuple(payload.media_ids),
        )
        found_ids = {int(row["id"]) for row in rows}
        missing = [
            {"media_id": media_id, "error": "Фотография не найдена в «Неразобранном»"}
            for media_id in payload.media_ids
            if media_id not in found_ids
        ]
        if missing:
            return JSONResponse({"album": None, "completed": [], "failures": missing}, status_code=404)

        album = create_album(AlbumCreateRequest(year=payload.year, name=payload.name))
        storage = Storage(config.photos_root, config.quarantine_root, database)
        completed, failures = [], []
        for media_id in payload.media_ids:
            try:
                completed.append({
                    "media_id": media_id,
                    "path": storage.move_media(
                        media_id, str(album["relative_path"]), payload.rename_on_conflict
                    ),
                })
            except (OSError, ValueError) as exc:
                failures.append({"media_id": media_id, "error": str(exc)})
        return JSONResponse(
            {"album": album, "completed": completed, "failures": failures},
            status_code=201 if not failures else 409,
        )

    @app.get("/api/library/media", dependencies=dependencies)
    def media(
        library_root: Literal["photos", "videos"] = "photos",
        year: str | None = None,
        container_id: int | None = None,
        page: int = 1,
        page_size: int = 48,
    ):
        media_type = "photo" if library_root == "photos" else "video"
        where = [
            "library_root=?", "media_type=?", "index_state='indexed'", "status='active'"
        ]
        params: list[object] = [library_root, media_type]
        if year is not None:
            where.append("relative_path LIKE ?")
            params.append(f"{year}/%")
        if container_id is not None:
            where.append("container_id=?")
            params.append(container_id)
        else:
            where.append("collection_state='album'")
        clause = " AND ".join(where)
        page, page_size = max(page, 1), min(max(page_size, 1), 100)
        total = database.one(
            f"SELECT COUNT(*) AS count FROM media WHERE {clause}", tuple(params)
        )["count"]
        rows = database.all(
            f"""
            SELECT id, relative_path, file_name, parent_relative_path, mime_type,
              size, mtime_ns, captured_at, container_id,
              (SELECT name FROM containers WHERE id=media.container_id) AS container_name
            FROM media WHERE {clause} ORDER BY relative_path
            LIMIT ? OFFSET ?
            """,
            (*params, page_size, (page - 1) * page_size),
        )
        return {
            "items": [{key: row[key] for key in row.keys()} for row in rows],
            "total": total, "page": page, "page_size": page_size,
        }
