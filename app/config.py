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
    staging_root: Path | None = None
    log_level: str = "INFO"
    retry_count: int = 3
    retry_delay_seconds: int = 5
    lock_ttl_seconds: int = 60
    lock_heartbeat_seconds: int = 20
    outbox_batch_size: int = 25
    outbox_max_attempts: int = 3
    startup_recovery: bool = True
    test_mode: bool = False
    environment: str = "development"

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

    @property
    def resolved_staging_root(self) -> Path:
        return self.staging_root or self.data_root / "staging"

    @property
    def log_root(self) -> Path:
        return self.data_root / "logs"

    def validate(self) -> None:
        if self.huey_db_path is None:
            raise ValueError("PHOTO_REVIEW_HUEY_DB is required")
        if self.huey_db_path == self.database_path:
            raise ValueError("Huey SQLite must be separate from the main database")
        if self.photos_root == self.quarantine_root:
            raise ValueError("Photo and quarantine roots must differ")
        if self.retry_count < 0 or self.retry_delay_seconds < 1 or self.outbox_batch_size < 1:
            raise ValueError("Retry and outbox settings must be positive")
        if self.lock_heartbeat_seconds >= self.lock_ttl_seconds:
            raise ValueError("Lock heartbeat must be shorter than lock TTL")
        if self.environment == "production" and (self.password in {"", "change-me"} or self.session_secret in {"", "change-this-session-secret"}):
            raise ValueError("Production requires non-default password and session secret")
        if self.test_mode:
            data_root = self.data_root.resolve()
            for root in (self.photos_root, self.quarantine_root, self.resolved_staging_root):
                try:
                    root.resolve().relative_to(data_root)
                except ValueError as exc:
                    raise ValueError("Test mode only permits roots inside PHOTO_REVIEW_DATA") from exc


def load_config() -> Config:
    environment = os.getenv("PHOTO_REVIEW_ENV", "development").lower()
    if environment not in {"development", "production", "test"}:
        raise ValueError("PHOTO_REVIEW_ENV must be development, production, or test")
    default_root = (Path.cwd() / ".photo-review").resolve()
    data_root = Path(os.getenv("PHOTO_REVIEW_DATA", default_root / "data")).resolve()
    required = ("PHOTO_REVIEW_PHOTOS", "PHOTO_REVIEW_QUARANTINE", "PHOTO_REVIEW_DATA")
    if environment == "production":
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise ValueError("Production requires explicit settings: " + ", ".join(missing))
    config = Config(
        photos_root=Path(os.getenv("PHOTO_REVIEW_PHOTOS", data_root / "library")).resolve(),
        videos_root=(Path(os.environ["PHOTO_REVIEW_VIDEOS"]).resolve() if os.getenv("PHOTO_REVIEW_VIDEOS") else None),
        quarantine_root=Path(
            os.getenv("PHOTO_REVIEW_QUARANTINE", data_root / "quarantine")
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
        staging_root=Path(os.getenv("PHOTO_REVIEW_STAGING", data_root / "staging")).resolve(),
        log_level=os.getenv("PHOTO_REVIEW_LOG_LEVEL", "INFO").upper(),
        retry_count=int(os.getenv("PHOTO_REVIEW_RETRY_COUNT", "3")),
        retry_delay_seconds=int(os.getenv("PHOTO_REVIEW_RETRY_DELAY_SECONDS", "5")),
        lock_ttl_seconds=int(os.getenv("PHOTO_REVIEW_LOCK_TTL_SECONDS", "60")),
        lock_heartbeat_seconds=int(os.getenv("PHOTO_REVIEW_LOCK_HEARTBEAT_SECONDS", "20")),
        outbox_batch_size=int(os.getenv("PHOTO_REVIEW_OUTBOX_BATCH_SIZE", "25")),
        outbox_max_attempts=int(os.getenv("PHOTO_REVIEW_OUTBOX_MAX_ATTEMPTS", "3")),
        startup_recovery=os.getenv("PHOTO_REVIEW_STARTUP_RECOVERY", "true").lower() in {"1", "true", "yes", "on"},
        test_mode=os.getenv("PHOTO_REVIEW_TEST_MODE", "false").lower() in {"1", "true", "yes", "on"},
        environment=environment,
    )
    config.validate()
    return config
