from __future__ import annotations

import json
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from .analyzer import CATEGORIES, JobManager
from .config import Config, load_config
from .db import Database
from .security import password_matches, safe_path
from .storage import Storage

BASE_DIR = Path(__file__).parent
config: Config = load_config()
database = Database(config.database_path)
storage = Storage(config.photos_root, config.quarantine_root, database)
jobs = JobManager(database, config.photos_root, config.thumbnail_root)
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@asynccontextmanager
async def lifespan(_: FastAPI):
    for path in (
        config.photos_root,
        config.quarantine_root,
        config.data_root,
        config.thumbnail_root,
        config.model_root,
    ):
        path.mkdir(parents=True, exist_ok=True)
    database.initialize()
    jobs.start_worker()
    yield


app = FastAPI(title="Разбор фотографий", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.session_secret,
    same_site="strict",
    https_only=False,
    max_age=60 * 60 * 24 * 30,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


def require_login(request: Request) -> None:
    if request.session.get("authenticated") is not True:
        raise HTTPException(status_code=401, detail="Требуется вход")


def row_dict(row) -> dict:
    return {key: row[key] for key in row.keys()}


class JobRequest(BaseModel):
    scope: str = ""
    duplicate_scope: Literal["scope", "archive"] = "scope"


class ReviewAction(BaseModel):
    finding_ids: list[int] = Field(min_length=1, max_length=500)
    action: Literal["keep", "later", "quarantine"]


class MediaIds(BaseModel):
    media_ids: list[int] = Field(min_length=1, max_length=500)
    rename_on_conflict: bool = False


class DeleteRequest(BaseModel):
    media_ids: list[int] = Field(min_length=1, max_length=500)
    confirmation: str


class SettingsRequest(BaseModel):
    blur_threshold: float = Field(ge=5, le=500)
    dark_threshold: float = Field(ge=5, le=100)
    similar_distance: int = Field(ge=1, le=16)
    ocr_min_chars: int = Field(ge=10, le=500)
    sensitivity: Literal["careful", "balanced", "broad"]


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"default_password": config.password == "change-me"},
    )


@app.post("/login")
def login(request: Request, password: str = Form(...)):
    if not password_matches(password, config.password):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": "Неверный пароль",
                "default_password": config.password == "change-me",
            },
            status_code=401,
        )
    request.session.clear()
    request.session["authenticated"] = True
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(401)
async def unauthorized(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"categories": CATEGORIES},
    )


@app.get("/api/summary", dependencies=[Depends(require_login)])
def summary():
    counts = database.all(
        """
        SELECT category, decision, COUNT(*) AS count
        FROM findings f JOIN media m ON m.id=f.media_id
        WHERE m.status='active'
        GROUP BY category, decision
        """
    )
    latest_job = database.one("SELECT * FROM jobs ORDER BY id DESC LIMIT 1")
    library = database.one(
        """
        SELECT COUNT(*) AS total,
          SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active,
          SUM(CASE WHEN status='quarantine' THEN 1 ELSE 0 END) AS quarantine,
          SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS errors
        FROM media
        """
    )
    unsupported = (
        latest_job["unsupported"] if latest_job and latest_job["unsupported"] else 0
    )
    return {
        "categories": [row_dict(row) for row in counts],
        "job": row_dict(latest_job) if latest_job else None,
        "library": row_dict(library),
        "unsupported": unsupported,
        "warning": config.password == "change-me",
    }


@app.get("/api/folders", dependencies=[Depends(require_login)])
def folders():
    result = [{"path": "", "name": "Весь архив"}]
    if not config.photos_root.exists():
        return result
    for path in sorted(config.photos_root.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            relative = path.relative_to(config.photos_root)
            if len(relative.parts) <= 3 and not any(
                part.startswith(".") for part in relative.parts
            ):
                result.append({"path": relative.as_posix(), "name": relative.as_posix()})
    return result


@app.post("/api/jobs", dependencies=[Depends(require_login)])
def create_job(payload: JobRequest):
    active = database.one(
        "SELECT id FROM jobs WHERE state IN ('queued','running','paused') LIMIT 1"
    )
    if active:
        raise HTTPException(409, "Сначала завершите или отмените текущее задание")
    try:
        job_id = jobs.create_job(payload.scope, payload.duplicate_scope)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": job_id}


@app.post("/api/jobs/{job_id}/{action}", dependencies=[Depends(require_login)])
def job_action(job_id: int, action: Literal["pause", "resume", "cancel"]):
    try:
        jobs.set_state(job_id, action)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@app.get("/api/review", dependencies=[Depends(require_login)])
def review(
    category: str = "exact",
    decision: str = "pending",
    page: int = 1,
    page_size: int = 48,
):
    if category not in CATEGORIES:
        raise HTTPException(400, "Неизвестная категория")
    page = max(1, page)
    page_size = min(max(page_size, 1), 100)
    total = database.one(
        """
        SELECT COUNT(*) AS count FROM findings f
        JOIN media m ON m.id=f.media_id
        WHERE f.category=? AND f.decision=? AND m.status='active'
        """,
        (category, decision),
    )["count"]
    rows = database.all(
        """
        SELECT f.*,m.relative_path,m.size,m.width,m.height,m.captured_at,
          m.sharpness,m.brightness
        FROM findings f JOIN media m ON m.id=f.media_id
        WHERE f.category=? AND f.decision=? AND m.status='active'
        ORDER BY COALESCE(f.group_key,''),f.suggested_best DESC,f.score DESC,f.id
        LIMIT ? OFFSET ?
        """,
        (category, decision, page_size, (page - 1) * page_size),
    )
    return {
        "items": [row_dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post("/api/review/action", dependencies=[Depends(require_login)])
def review_action(payload: ReviewAction):
    placeholders = ",".join("?" for _ in payload.finding_ids)
    rows = database.all(
        f"""
        SELECT DISTINCT f.media_id FROM findings f
        JOIN media m ON m.id=f.media_id
        WHERE f.id IN ({placeholders}) AND m.status='active'
        """,
        tuple(payload.finding_ids),
    )
    if not rows:
        raise HTTPException(404, "Выбранные фотографии не найдены")
    failures = []
    if payload.action == "quarantine":
        for row in rows:
            try:
                storage.quarantine_media(int(row["media_id"]))
            except (OSError, ValueError) as exc:
                failures.append({"media_id": row["media_id"], "error": str(exc)})
    else:
        with database.connect() as connection:
            connection.execute(
                f"UPDATE findings SET decision=? WHERE id IN ({placeholders})",
                (payload.action, *payload.finding_ids),
            )
            for row in rows:
                media = connection.execute(
                    "SELECT relative_path FROM media WHERE id=?", (row["media_id"],)
                ).fetchone()
                connection.execute(
                    "INSERT INTO audit_log(action,relative_path,details) VALUES(?,?,?)",
                    (payload.action, media["relative_path"], None),
                )
    return {"ok": not failures, "failures": failures}


@app.get("/thumbnail/{media_id}", dependencies=[Depends(require_login)])
def thumbnail(media_id: int):
    row = database.one("SELECT id FROM media WHERE id=?", (media_id,))
    path = config.thumbnail_root / f"{media_id}.jpg"
    if not row or not path.is_file():
        raise HTTPException(404, "Миниатюра не найдена")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/photo/{media_id}", dependencies=[Depends(require_login)])
def photo(media_id: int):
    row = database.one("SELECT * FROM media WHERE id=?", (media_id,))
    if not row:
        raise HTTPException(404, "Файл не найден")
    root = config.quarantine_root if row["status"] == "quarantine" else config.photos_root
    relative = (
        row["quarantine_path"]
        if row["status"] == "quarantine"
        else row["relative_path"]
    )
    path = safe_path(root, relative)
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404, "Файл не найден")
    media_type, _ = mimetypes.guess_type(path.name)
    return FileResponse(path, media_type=media_type or "application/octet-stream")


@app.get("/api/quarantine", dependencies=[Depends(require_login)])
def quarantine():
    rows = database.all(
        """
        SELECT id,relative_path,quarantine_path,size,captured_at,width,height
        FROM media WHERE status='quarantine' ORDER BY updated_at DESC
        """
    )
    total_size = sum(int(row["size"] or 0) for row in rows)
    return {"items": [row_dict(row) for row in rows], "total_size": total_size}


@app.post("/api/quarantine/restore", dependencies=[Depends(require_login)])
def restore(payload: MediaIds):
    failures = []
    restored = []
    for media_id in payload.media_ids:
        try:
            restored.append(
                storage.restore_media(media_id, payload.rename_on_conflict)
            )
        except (OSError, ValueError) as exc:
            failures.append({"media_id": media_id, "error": str(exc)})
    status = 200 if not failures else 409
    return JSONResponse(
        {"restored": restored, "failures": failures}, status_code=status
    )


@app.post("/api/quarantine/delete", dependencies=[Depends(require_login)])
def delete_quarantine(payload: DeleteRequest):
    if payload.confirmation != "УДАЛИТЬ":
        raise HTTPException(400, "Введите слово УДАЛИТЬ для подтверждения")
    failures = []
    deleted = 0
    for media_id in payload.media_ids:
        try:
            storage.delete_media(media_id)
            deleted += 1
        except (OSError, ValueError) as exc:
            failures.append({"media_id": media_id, "error": str(exc)})
    return {"deleted": deleted, "failures": failures}


@app.get("/api/settings", dependencies=[Depends(require_login)])
def get_settings():
    return database.settings()


@app.post("/api/settings", dependencies=[Depends(require_login)])
def save_settings(payload: SettingsRequest):
    return database.save_settings(payload.model_dump())


@app.get("/api/audit", dependencies=[Depends(require_login)])
def audit(limit: int = 100):
    rows = database.all(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 500),)
    )
    return {"items": [row_dict(row) for row in rows]}


@app.get("/health")
def health():
    return {"status": "ok"}
