from __future__ import annotations

import logging
import os
import tempfile
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from PIL import Image, UnidentifiedImageError

from .config import Config
from .db import Database
from .library import LibraryIndexer, PHOTO_EXTENSIONS
from .security import safe_path

logger = logging.getLogger(__name__)

_FORMATS_BY_EXTENSION = {
    ".jpg": {"JPEG"}, ".jpeg": {"JPEG"}, ".png": {"PNG"},
    ".webp": {"WEBP"}, ".heic": {"HEIF"}, ".heif": {"HEIF"},
}
_CHUNK_SIZE = 1024 * 1024


def _error(name: str, code: str, message: str) -> dict[str, object]:
    return {"original_name": name, "status": "error", "error_code": code, "message": message}


def _safe_name(name: str | None) -> str:
    if not name or Path(name).name != name or "/" in name or "\\" in name:
        raise ValueError("INVALID_NAME")
    if any(ord(char) < 32 for char in name) or name in {".", ".."}:
        raise ValueError("INVALID_NAME")
    if Path(name).suffix.lower() not in PHOTO_EXTENSIONS:
        raise ValueError("UNSUPPORTED_FORMAT")
    return name


def _album_directory(database: Database, config: Config, container_id: int) -> tuple[str, Path]:
    row = database.one(
        """
        SELECT name, relative_path FROM containers
        WHERE id=? AND library_root='photos' AND media_type='photo'
          AND kind='album' AND missing_since IS NULL
        """,
        (container_id,),
    )
    if row is None:
        raise HTTPException(404, "Album not found")
    root = config.photos_root.resolve()
    try:
        folder = safe_path(root, row["relative_path"])
    except ValueError as exc:
        raise HTTPException(409, "Album directory is unavailable") from exc
    current = root
    for part in Path(row["relative_path"]).parts:
        current = current / part
        if current.is_symlink():
            raise HTTPException(409, "Album directory is unavailable")
    if not folder.is_dir() or folder.is_symlink() or not os.access(folder, os.W_OK):
        raise HTTPException(409, "Album directory is unavailable")
    return str(row["name"]), folder


def _verify_image(path: Path, suffix: str) -> None:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            actual_format = image.format
            image.load()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("INVALID_IMAGE") from exc
    if actual_format not in _FORMATS_BY_EXTENSION[suffix]:
        raise ValueError("INVALID_IMAGE")


async def _write_temp(upload: UploadFile, folder: Path, max_bytes: int) -> tuple[Path, int]:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".photo-review-upload-", dir=folder)
    temporary = Path(temporary_name)
    size = 0
    try:
        with os.fdopen(descriptor, "wb") as target:
            while chunk := await upload.read(_CHUNK_SIZE):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("FILE_TOO_LARGE")
                target.write(chunk)
        if size == 0:
            raise ValueError("EMPTY_FILE")
        return temporary, size
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _destination(folder: Path, name: str, temporary: Path) -> tuple[Path, bool]:
    stem, suffix = Path(name).stem, Path(name).suffix
    for index in range(10_000):
        candidate_name = name if index == 0 else f"{stem}_{index}{suffix}"
        candidate = folder / candidate_name
        try:
            with candidate.open("xb"):
                pass
        except FileExistsError:
            continue
        try:
            os.replace(temporary, candidate)
        except Exception:
            candidate.unlink(missing_ok=True)
            raise
        return candidate, index > 0
    raise OSError("No available destination file name")


def install_upload_api(
    app: FastAPI, database: Database, indexer: LibraryIndexer, require_login, config: Config
) -> None:
    dependencies = [Depends(require_login)]

    @app.post("/api/library/albums/{container_id}/photos", dependencies=dependencies)
    async def upload_album_photos(
        container_id: int, request: Request, files: list[UploadFile] = File(...)
    ):
        if not files:
            raise HTTPException(400, "No photos were selected")
        if len(files) > config.upload_max_files:
            raise HTTPException(413, f"At most {config.upload_max_files} photos may be uploaded")
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > config.upload_max_total_bytes + 64 * 1024:
            raise HTTPException(413, "The total upload is too large")

        album_name, folder = _album_directory(database, config, container_id)
        upload_id = uuid.uuid4().hex
        results: list[dict[str, object]] = []
        successful = 0
        total_bytes = 0

        for upload in files:
            original_name = upload.filename or "file"
            temporary: Path | None = None
            try:
                safe_name = _safe_name(upload.filename)
                temporary, size = await _write_temp(upload, folder, config.upload_max_file_bytes)
                total_bytes += size
                if total_bytes > config.upload_max_total_bytes:
                    raise ValueError("TOTAL_TOO_LARGE")
                suffix = Path(safe_name).suffix.lower()
                _verify_image(temporary, suffix)
                destination, renamed = _destination(folder, safe_name, temporary)
                temporary = None
                resolved_destination = destination.resolve()
                if resolved_destination.parent != folder.resolve() or not resolved_destination.is_file():
                    destination.unlink(missing_ok=True)
                    raise ValueError("SAVE_FAILED")
                if resolved_destination.stat().st_size != size or not os.access(resolved_destination, os.R_OK):
                    destination.unlink(missing_ok=True)
                    raise ValueError("SAVE_FAILED")
                try:
                    media_id = indexer.index_album_file(container_id, resolved_destination)
                except Exception:
                    logger.exception("upload indexing failed upload_id=%s album_id=%s file=%s", upload_id, container_id, safe_name)
                    results.append({
                        "original_name": original_name, "stored_name": destination.name,
                        "status": "saved_unindexed", "error_code": "INDEX_FAILED",
                        "message": f"{original_name} was saved but is not indexed yet. Do not upload it again.",
                    })
                    continue
                successful += 1
                results.append({
                    "original_name": original_name, "stored_name": destination.name, "renamed": renamed,
                    "status": "success", "photo": {"id": media_id, "name": destination.name},
                })
            except ValueError as exc:
                messages = {
                    "INVALID_NAME": ("INVALID_NAME", "Invalid file name."),
                    "UNSUPPORTED_FORMAT": ("UNSUPPORTED_FORMAT", "Only JPEG, PNG, WebP, HEIC, and HEIF images are supported."),
                    "EMPTY_FILE": ("EMPTY_FILE", "The file is empty."),
                    "FILE_TOO_LARGE": ("FILE_TOO_LARGE", "The file exceeds the size limit."),
                    "TOTAL_TOO_LARGE": ("TOTAL_TOO_LARGE", "The upload exceeds the total size limit."),
                    "INVALID_IMAGE": ("INVALID_IMAGE", "The file could not be verified as an image."),
                    "SAVE_FAILED": ("SAVE_FAILED", "The file could not be verified in the album directory."),
                }
                code, message = messages.get(str(exc), ("UPLOAD_FAILED", "The file could not be uploaded."))
                results.append(_error(original_name, code, f"{original_name}: {message}"))
            except OSError:
                logger.exception("upload write failed upload_id=%s album_id=%s file=%s", upload_id, container_id, original_name)
                results.append(_error(original_name, "SAVE_FAILED", f"{original_name}: the file could not be saved in the album directory."))
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
                await upload.close()

        failed = len(results) - successful
        logger.info("album upload completed upload_id=%s album_id=%s requested=%s successful=%s failed=%s", upload_id, container_id, len(files), successful, failed)
        return {
            "upload_id": upload_id, "album_id": container_id, "album_name": album_name,
            "requested_count": len(files), "successful_count": successful,
            "failed_count": failed, "results": results,
        }

