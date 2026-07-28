from __future__ import annotations

from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from .config import Config
from .uploads import install_upload_api
from .db import Database
from .library import LibraryIndexer


class ScanRequest(BaseModel):
    library_root: Literal["photos", "videos"] = "photos"


def install_library_api(
    app: FastAPI, database: Database, indexer: LibraryIndexer, require_login, config: Config
) -> None:
    dependencies = [Depends(require_login)]

    install_upload_api(app, database, indexer, require_login, config)
    @app.post("/api/library/scan", dependencies=dependencies)
    def scan_library(payload: ScanRequest):
        try:
            return indexer.scan(payload.library_root).as_dict()
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/library/shelves", dependencies=dependencies)
    def shelves(library_root: Literal["photos", "videos"] = "photos"):
        rows = database.all(
            """
            SELECT substr(relative_path, 1, instr(relative_path || '/', '/') - 1) AS year,
              COUNT(*) AS media_count, MIN(id) AS cover_media_id
            FROM media
            WHERE library_root=? AND media_type=?
              AND index_state='indexed' AND status='active'
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
            WHERE c.library_root=? AND c.media_type=? AND c.year=?
              AND c.missing_since IS NULL
            GROUP BY c.id ORDER BY c.name COLLATE NOCASE
            """,
            (library_root, media_type, year),
        )
        return {"items": [{key: row[key] for key in row.keys()} for row in rows]}

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
