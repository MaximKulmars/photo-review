from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
import pytesseract
from PIL import ExifTags, Image, ImageOps
from pillow_heif import register_heif_opener

from .db import Database
from .security import relative_posix, safe_path
from .semantic import semantic_findings

register_heif_opener()

SUPPORTED = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
KNOWN_UNSUPPORTED = {
    ".raw",
    ".cr2",
    ".cr3",
    ".nef",
    ".arw",
    ".dng",
    ".orf",
    ".rw2",
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
}

CATEGORIES = {
    "quality": "Качественные изображения",
    "exact": "Точные дубликаты",
    "similar": "Похожие фотографии",
    "blurry": "Размытые и некачественные",
    "dark": "Слишком тёмные",
    "screenshot": "Скриншоты",
    "document": "Документы и экраны",
    "saved": "Мемы и сохранённые картинки",
    "accidental": "Вероятные случайные кадры",
}

EXIF_NAME = {value: key for key, value in ExifTags.TAGS.items()}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def perceptual_hash(gray: np.ndarray) -> str:
    resized = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    dct = cv2.dct(np.float32(resized))
    low = dct[:8, :8]
    values = low.flatten()
    median = float(np.median(values[1:]))
    bits = values > median
    number = 0
    for bit in bits:
        number = (number << 1) | int(bit)
    return f"{number:016x}"


def hamming(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _captured_at(exif: dict[int, Any]) -> str | None:
    for label in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
        raw = exif.get(EXIF_NAME.get(label))
        if raw:
            try:
                return datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S").isoformat()
            except ValueError:
                continue
    return None


def _camera_present(exif: dict[int, Any]) -> bool:
    return bool(exif.get(EXIF_NAME.get("Make")) or exif.get(EXIF_NAME.get("Model")))


def _ocr_text(rgb: np.ndarray) -> str:
    try:
        height, width = rgb.shape[:2]
        scale = min(1.0, 1400 / max(width, height))
        if scale < 1:
            rgb = cv2.resize(rgb, None, fx=scale, fy=scale)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        return pytesseract.image_to_string(
            gray, lang=os.getenv("PHOTO_REVIEW_OCR_LANGS", "rus+eng"), timeout=20
        ).strip()
    except (RuntimeError, pytesseract.TesseractError, pytesseract.TesseractNotFoundError):
        return ""


def inspect_image(path: Path, settings: dict[str, Any], thumbnail: Path) -> dict[str, Any]:
    with Image.open(path) as source:
        source.load()
        exif = dict(source.getexif())
        image = ImageOps.exif_transpose(source).convert("RGB")
        width, height = image.size
        preview = image.copy()
        preview.thumbnail((1600, 1600))
        rgb = np.asarray(preview)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

        thumbnail.parent.mkdir(parents=True, exist_ok=True)
        thumb = image.copy()
        thumb.thumbnail((480, 480))
        thumb.save(thumbnail, "JPEG", quality=82, optimize=True)

    brightness = float(np.mean(gray))
    contrast = float(np.std(gray))
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    edges = cv2.Canny(gray, 80, 180)
    edge_density = float(np.count_nonzero(edges) / edges.size)
    ratio = max(width, height) / max(1, min(width, height))
    no_camera = not _camera_present(exif)

    should_ocr = (
        no_camera
        or brightness > 155
        or edge_density > 0.10
        or ratio > 1.75
    )
    text = _ocr_text(rgb) if should_ocr else ""
    text_length = len("".join(character for character in text if character.isalnum()))

    findings: list[tuple[str, str, float]] = []
    if sharpness < float(settings["blur_threshold"]):
        findings.append(
            (
                "blurry",
                f"Низкая резкость: {sharpness:.0f} (порог {settings['blur_threshold']:.0f})",
                max(0.0, 1 - sharpness / max(float(settings["blur_threshold"]), 1)),
            )
        )
    if brightness < float(settings["dark_threshold"]):
        findings.append(
            (
                "dark",
                f"Очень тёмный кадр: яркость {brightness:.0f}",
                max(0.0, 1 - brightness / max(float(settings["dark_threshold"]), 1)),
            )
        )

    common_screen = any(
        abs(width / max(height, 1) - candidate) < 0.035
        for candidate in (16 / 9, 9 / 16, 19.5 / 9, 9 / 19.5, 4 / 3)
    )
    if no_camera and common_screen and sharpness > 100 and edge_density > 0.06:
        findings.append(
            (
                "screenshot",
                "Похоже на скриншот: нет данных камеры и совпадают пропорции экрана",
                min(1.0, 0.55 + edge_density),
            )
        )

    if text_length >= int(settings["ocr_min_chars"]):
        findings.append(
            (
                "document",
                f"На изображении найдено много текста: около {text_length} символов",
                min(1.0, text_length / 250),
            )
        )
    elif no_camera and text_length >= 16 and edge_density > 0.08:
        findings.append(
            (
                "saved",
                "Нет данных камеры, но присутствуют текст и чёткая графика",
                min(1.0, 0.45 + text_length / 200),
            )
        )

    if (contrast < 13 and edge_density < 0.02) or (
        ratio > 2.7 and no_camera and text_length == 0
    ):
        findings.append(
            (
                "accidental",
                "Мало деталей или необычные пропорции кадра",
                min(1.0, 0.65 + max(0.0, (13 - contrast) / 30)),
            )
        )

    existing_categories = {finding[0] for finding in findings}
    for finding in semantic_findings(
        brightness=brightness,
        contrast=contrast,
        sharpness=sharpness,
        edge_density=edge_density,
        text_length=text_length,
        no_camera=no_camera,
        common_screen=common_screen,
        ratio=ratio,
    ):
        if finding[0] not in existing_categories:
            findings.append(finding)

    return {
        "width": width,
        "height": height,
        "captured_at": _captured_at(exif),
        "phash": perceptual_hash(gray),
        "brightness": brightness,
        "sharpness": sharpness,
        "edge_density": edge_density,
        "text_length": text_length,
        "findings": findings,
    }


class JobManager:
    def __init__(self, db: Database, photos_root: Path, thumbnail_root: Path):
        self.db = db
        self.photos_root = photos_root.resolve()
        self.thumbnail_root = thumbnail_root.resolve()
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._lock = threading.Lock()

    def start_worker(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._worker_loop, name="photo-review-worker", daemon=True
            )
            self._thread.start()

    def create_job(self, scope: str, duplicate_scope: str) -> int:
        if duplicate_scope not in {"scope", "archive"}:
            raise ValueError("Неверная область поиска дубликатов")
        scope_path = safe_path(self.photos_root, scope)
        if not scope_path.is_dir():
            raise FileNotFoundError("Выбранная папка не существует")
        relative = relative_posix(self.photos_root, scope_path)
        job_id = self.db.execute(
            "INSERT INTO jobs(scope, duplicate_scope, state) VALUES(?,?,'queued')",
            ("" if relative == "." else relative, duplicate_scope),
        )
        self.start_worker()
        self._wake.set()
        return job_id

    def set_state(self, job_id: int, action: str) -> None:
        transitions = {
            "pause": (("running",), "paused"),
            "resume": (("paused",), "queued"),
            "cancel": (("running", "paused", "queued"), "cancelled"),
        }
        if action not in transitions:
            raise ValueError("Неизвестное действие")
        old_states, new = transitions[action]
        placeholders = ",".join("?" for _ in old_states)
        with self.db.connect() as connection:
            cursor = connection.execute(
                f"UPDATE jobs SET state=?, message=? WHERE id=? "
                f"AND state IN ({placeholders})",
                (new, None, job_id, *old_states),
            )
            if not cursor.rowcount:
                raise ValueError("Это действие сейчас недоступно")
        if action == "cancel":
            self._prune_jobs()
        self._wake.set()

    def _worker_loop(self) -> None:
        while True:
            job = self.db.one(
                "SELECT * FROM jobs WHERE state='queued' ORDER BY id LIMIT 1"
            )
            if not job:
                self._wake.wait(10)
                self._wake.clear()
                continue
            self._run_job(int(job["id"]))

    def _discover(self, scope: str) -> tuple[list[Path], int]:
        root = safe_path(self.photos_root, scope)
        supported: list[Path] = []
        unsupported = 0
        for current, directories, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            directories[:] = [
                name
                for name in directories
                if not (current_path / name).is_symlink() and not name.startswith(".")
            ]
            for name in files:
                path = current_path / name
                if path.is_symlink():
                    continue
                suffix = path.suffix.lower()
                if suffix in SUPPORTED:
                    supported.append(path)
                elif suffix in KNOWN_UNSUPPORTED:
                    unsupported += 1
        return supported, unsupported

    def _run_job(self, job_id: int) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE jobs SET state='running', started_at=CURRENT_TIMESTAMP,
                    finished_at=NULL, processed=0, skipped=0, errors=0,
                    message='Поиск файлов' WHERE id=?
                """,
                (job_id,),
            )
        job = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job:
            return
        try:
            files, unsupported = self._discover(job["scope"])
            self.db.execute(
                "UPDATE jobs SET total=?, unsupported=?, message='Анализ фотографий' WHERE id=?",
                (len(files), unsupported, job_id),
            )
            settings = self.db.settings()
            for path in files:
                state = self.db.one("SELECT state FROM jobs WHERE id=?", (job_id,))
                if not state or state["state"] in {"paused", "cancelled"}:
                    return
                self._process_file(job_id, path, settings)

            self.db.execute(
                "UPDATE jobs SET message='Группировка дубликатов' WHERE id=?",
                (job_id,),
            )
            self._group_duplicates(job_id, job["scope"], job["duplicate_scope"], settings)
            self.db.execute(
                """
                UPDATE jobs SET state='completed', message='Готово',
                    finished_at=CURRENT_TIMESTAMP WHERE id=?
                """,
                (job_id,),
            )
            self._prune_jobs()
        except Exception as exc:
            self.db.execute(
                """
                UPDATE jobs SET state='failed', message=?,
                    finished_at=CURRENT_TIMESTAMP WHERE id=?
                """,
                (str(exc)[:500], job_id),
            )
            self._prune_jobs()

    def _prune_jobs(self) -> None:
        """Keep a compact execution history; media rows point only at the newest scan."""
        with self.db.connect() as connection:
            stale = connection.execute(
                """
                SELECT id FROM jobs WHERE state IN ('completed','failed','cancelled')
                ORDER BY id DESC LIMIT -1 OFFSET 10
                """
            ).fetchall()
            if stale:
                connection.executemany("DELETE FROM jobs WHERE id=?", [(row["id"],) for row in stale])

    def _process_file(
        self, job_id: int, path: Path, settings: dict[str, Any]
    ) -> None:
        relative = relative_posix(self.photos_root, path)
        stat = path.stat()
        existing = self.db.one(
            "SELECT * FROM media WHERE relative_path=?", (relative,)
        )
        revision = int(settings["analysis_revision"])
        if (
            existing
            and existing["size"] == stat.st_size
            and existing["mtime_ns"] == stat.st_mtime_ns
            and existing["analysis_revision"] == revision
            and existing["status"] == "active"
            and not existing["error"]
        ):
            self.db.execute(
                "UPDATE media SET last_scan_job_id=? WHERE id=?",
                (job_id, existing["id"]),
            )
            self.db.execute(
                "UPDATE jobs SET processed=processed+1, skipped=skipped+1 WHERE id=?",
                (job_id,),
            )
            return

        try:
            sha = file_sha256(path)
            if existing:
                media_id = int(existing["id"])
            else:
                media_id = self.db.execute(
                    """
                    INSERT INTO media(relative_path,size,mtime_ns)
                    VALUES(?,?,?)
                    """,
                    (relative, stat.st_size, stat.st_mtime_ns),
                )
            thumbnail = self.thumbnail_root / f"{media_id}.jpg"
            result = inspect_image(path, settings, thumbnail)
            with self.db.connect() as connection:
                connection.execute(
                    """
                    UPDATE media SET size=?, mtime_ns=?, width=?, height=?,
                        captured_at=?, sha256=?, phash=?, brightness=?, sharpness=?,
                        edge_density=?, text_length=?, status='active',
                        analysis_revision=?, last_scan_job_id=?, manual_quality=0,
                        error=NULL, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (
                        stat.st_size,
                        stat.st_mtime_ns,
                        result["width"],
                        result["height"],
                        result["captured_at"],
                        sha,
                        result["phash"],
                        result["brightness"],
                        result["sharpness"],
                        result["edge_density"],
                        result["text_length"],
                        revision, job_id,
                        media_id,
                    ),
                )
                connection.execute("DELETE FROM findings WHERE media_id=?", (media_id,))
                for category, reason, score in result["findings"]:
                    connection.execute(
                        """
                        INSERT INTO findings(media_id,category,reason,score)
                        VALUES(?,?,?,?)
                        """,
                        (media_id, category, reason, score),
                    )
            self.db.execute(
                "UPDATE jobs SET processed=processed+1 WHERE id=?", (job_id,)
            )
        except Exception as exc:
            if existing:
                media_id = int(existing["id"])
                self.db.execute(
                    """
                    UPDATE media SET size=?,mtime_ns=?,error=?,
                        analysis_revision=?,last_scan_job_id=?,manual_quality=0,
                        updated_at=CURRENT_TIMESTAMP WHERE id=?
                    """,
                    (stat.st_size, stat.st_mtime_ns, str(exc)[:500], revision, job_id, media_id),
                )
            else:
                self.db.execute(
                    """
                    INSERT INTO media(relative_path,size,mtime_ns,error,analysis_revision,last_scan_job_id,manual_quality)
                    VALUES(?,?,?,?,?,?,0)
                    """,
                    (relative, stat.st_size, stat.st_mtime_ns, str(exc)[:500], revision, job_id),
                )
            self.db.execute(
                "UPDATE jobs SET processed=processed+1, errors=errors+1 WHERE id=?",
                (job_id,),
            )

    def _group_duplicates(
        self, job_id: int, scope: str, duplicate_scope: str, settings: dict[str, Any]
    ) -> None:
        scope_prefix = f"{scope.rstrip('/')}/%" if scope else "%"
        if duplicate_scope == "scope":
            rows = self.db.all(
                """
                SELECT * FROM media WHERE status='active' AND error IS NULL
                  AND relative_path LIKE ?
                """,
                (scope_prefix,),
            )
        else:
            rows = self.db.all(
                "SELECT * FROM media WHERE status='active' AND error IS NULL"
            )
        ids_in_scope = {
            int(row["id"])
            for row in rows
            if not scope or row["relative_path"] == scope or row["relative_path"].startswith(scope + "/")
        }
        with self.db.connect() as connection:
            if ids_in_scope:
                placeholders = ",".join("?" for _ in ids_in_scope)
                connection.execute(
                    f"DELETE FROM findings WHERE category IN ('exact','similar') "
                    f"AND media_id IN ({placeholders})",
                    tuple(ids_in_scope),
                )

            hashes: dict[str, list[Any]] = defaultdict(list)
            for row in rows:
                if row["sha256"]:
                    hashes[row["sha256"]].append(row)
            for digest, group in hashes.items():
                if len(group) < 2:
                    continue
                best = self._best(group)
                for row in group:
                    if int(row["id"]) not in ids_in_scope or row["manual_quality"]:
                        continue
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO findings
                        (media_id,category,reason,score,group_key,suggested_best,decision)
                        VALUES(?,?,?,?,?,?, 'pending')
                        """,
                        (
                            row["id"],
                            "exact",
                            f"Найдены {len(group)} полностью одинаковых файла",
                            1.0,
                            f"sha:{digest}",
                            int(row["id"] == best),
                        ),
                    )

            exact_pairs = {
                tuple(sorted((int(a["id"]), int(b["id"]))))
                for group in hashes.values()
                if len(group) > 1
                for index, a in enumerate(group)
                for b in group[index + 1 :]
            }
            buckets: dict[str, list[Any]] = defaultdict(list)
            for row in rows:
                if not row["phash"]:
                    continue
                for offset in range(0, 16, 4):
                    buckets[row["phash"][offset : offset + 4]].append(row)

            parent = {int(row["id"]): int(row["id"]) for row in rows}

            def find(value: int) -> int:
                while parent[value] != value:
                    parent[value] = parent[parent[value]]
                    value = parent[value]
                return value

            def union(left: int, right: int) -> None:
                lroot, rroot = find(left), find(right)
                if lroot != rroot:
                    parent[rroot] = lroot

            seen: set[tuple[int, int]] = set()
            threshold = int(settings["similar_distance"])
            for bucket in buckets.values():
                for index, left in enumerate(bucket):
                    for right in bucket[index + 1 :]:
                        pair = tuple(sorted((int(left["id"]), int(right["id"]))))
                        if pair in seen or pair in exact_pairs:
                            continue
                        seen.add(pair)
                        left_ratio = left["width"] / max(1, left["height"])
                        right_ratio = right["width"] / max(1, right["height"])
                        if abs(math.log(max(left_ratio, 0.01) / max(right_ratio, 0.01))) > 0.12:
                            continue
                        if hamming(left["phash"], right["phash"]) <= threshold:
                            union(pair[0], pair[1])

            groups: dict[int, list[Any]] = defaultdict(list)
            for row in rows:
                groups[find(int(row["id"]))].append(row)
            for root, group in groups.items():
                if len(group) < 2:
                    continue
                best = self._best(group)
                group_key = f"phash:{root}"
                for row in group:
                    if int(row["id"]) not in ids_in_scope or row["manual_quality"]:
                        continue
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO findings
                        (media_id,category,reason,score,group_key,suggested_best,decision)
                        VALUES(?,?,?,?,?,?, 'pending')
                        """,
                        (
                            row["id"],
                            "similar",
                            f"Группа из {len(group)} визуально похожих фотографий",
                            0.8,
                            group_key,
                            int(row["id"] == best),
                        ),
                    )

    @staticmethod
    def _best(rows: list[Any]) -> int:
        def quality(row: Any) -> tuple[float, int, int]:
            pixels = int(row["width"] or 0) * int(row["height"] or 0)
            return (float(row["sharpness"] or 0), pixels, int(row["size"] or 0))

        return int(max(rows, key=quality)["id"])
