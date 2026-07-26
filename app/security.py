from __future__ import annotations

import hashlib
import hmac
from pathlib import Path


def password_digest(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def password_matches(password: str, expected: str) -> bool:
    return hmac.compare_digest(password_digest(password), password_digest(expected))


def safe_path(root: Path, relative: str) -> Path:
    """Resolve a user/db relative path without allowing an escape from root."""
    if "\x00" in relative:
        raise ValueError("Недопустимый путь")
    candidate = (root / relative).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("Путь выходит за пределы подключённой папки") from exc
    return candidate


def relative_posix(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()

