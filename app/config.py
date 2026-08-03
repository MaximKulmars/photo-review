from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    photos_root: Path
    videos_root: Path | None
    quarantine_root: Path
    data_root: Path
    password: str
    session_secret: str
    auth_enabled: bool
    port: int
    upload_max_files: int
    upload_max_file_bytes: int
    upload_max_total_bytes: int
    huey_db_path: Path | None = None
    huey_immediate: bool = False

    @property
    def library_roots(self) -> dict[str, Path]:
        roots = {"photos": self.photos_root}
        if self.videos_root is not None:
            roots["videos"] = self.videos_root
        return roots

    @property
    def database_path(self) -> Path:
        return self.data_root / "photo-review.sqlite3"

    @property
    def thumbnail_root(self) -> Path:
        return self.data_root / "thumbnails"

    @property
    def model_root(self) -> Path:
        return self.data_root / "models"


def load_config() -> Config:
    data_root = Path(os.getenv("PHOTO_REVIEW_DATA", "/data")).resolve()
    return Config(
        photos_root=Path(os.getenv("PHOTO_REVIEW_PHOTOS", "/photos")).resolve(),
        videos_root=(Path(os.environ["PHOTO_REVIEW_VIDEOS"]).resolve() if os.getenv("PHOTO_REVIEW_VIDEOS") else None),
        quarantine_root=Path(
            os.getenv("PHOTO_REVIEW_QUARANTINE", "/quarantine")
        ).resolve(),
        data_root=data_root,
        password=os.getenv("PHOTO_REVIEW_PASSWORD", "change-me"),
        session_secret=os.getenv(
            "PHOTO_REVIEW_SESSION_SECRET", "change-this-session-secret"
        ),
        auth_enabled=os.getenv("PHOTO_REVIEW_AUTH_ENABLED", "true").lower()
        in {"1", "true", "yes", "on"},
        port=int(os.getenv("PHOTO_REVIEW_PORT", "8080")),
        upload_max_files=int(os.getenv("PHOTO_REVIEW_UPLOAD_MAX_FILES", "50")),
        upload_max_file_bytes=int(os.getenv("PHOTO_REVIEW_UPLOAD_MAX_FILE_BYTES", str(100 * 1024 * 1024))),
        upload_max_total_bytes=int(os.getenv("PHOTO_REVIEW_UPLOAD_MAX_TOTAL_BYTES", str(1024 * 1024 * 1024))),
        huey_db_path=Path(os.getenv("PHOTO_REVIEW_HUEY_DB", data_root / "queue" / "huey.sqlite3")).resolve(),
        huey_immediate=os.getenv("PHOTO_REVIEW_HUEY_IMMEDIATE", "false").lower() in {"1", "true", "yes", "on"},
    )
