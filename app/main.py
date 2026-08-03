from __future__ import annotations

import mimetypes
import uuid
from io import BytesIO

from PIL import Image, ImageOps
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from .analyzer import CATEGORIES, JobManager
from .bootstrap import build_application_dependencies
from .config import Config, load_config
from .library_api import install_library_api
from .infrastructure.diagnostics import bind_diagnostic_context, configure_json_logging, shutdown_json_logging
from .infrastructure.diagnostics import DiagnosticService
from .infrastructure.database.operations import SqliteOperationRepository
from .infrastructure.database.locks import SqliteResourceLockRepository
from .application.services.operation_manager import OperationManager
from .application.services.operation_recovery import OperationRecoveryService
from .infrastructure.background.huey_queue import HueyBackgroundQueue
from .web.operations_api import install_operations_api
from .security import password_matches, safe_path

BASE_DIR = Path(__file__).parent
config: Config = load_config()
dependencies = build_application_dependencies(config)
database = dependencies.database
storage = dependencies.storage
jobs = dependencies.jobs
library_indexer = dependencies.library_indexer
operation_manager = OperationManager(SqliteOperationRepository(database), SqliteResourceLockRepository(database))
diagnostics = DiagnosticService(database)
operation_recovery = OperationRecoveryService(operation_manager, database, HueyBackgroundQueue(), diagnostics, temp_roots=(config.photos_root, config.quarantine_root))
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@asynccontextmanager
async def lifespan(_: FastAPI):
    roots = [config.photos_root, config.quarantine_root, config.data_root, config.thumbnail_root, config.model_root, config.resolved_staging_root, config.log_root]
    if config.videos_root is not None:
        roots.append(config.videos_root)
    for path in roots:
        path.mkdir(parents=True, exist_ok=True)
    database.initialize()
    configure_json_logging(config.data_root / "logs")
    if config.startup_recovery:
        operation_recovery.recover()
    jobs.start_worker()
    try:
        yield
    finally:
        shutdown_json_logging()


app = FastAPI(title="Разбор фотографий", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=config.session_secret, same_site="strict", https_only=False, max_age=60 * 60 * 24 * 30)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.middleware("http")
async def diagnostic_request_context(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    with bind_diagnostic_context(correlation_id=correlation_id, request_id=request_id, component="web"):
        response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    response.headers["X-Request-ID"] = request_id
    return response


def require_login(request: Request) -> None:
    if config.auth_enabled and request.session.get("authenticated") is not True:
        raise HTTPException(status_code=401, detail="Требуется вход")


install_library_api(app, database, library_indexer, require_login, config)
install_operations_api(app, operation_manager, require_login, diagnostics)

def row_dict(row) -> dict:
    return {key: row[key] for key in row.keys()}


def latest_result_job():
    return database.one("SELECT * FROM jobs WHERE state='completed' ORDER BY id DESC LIMIT 1")


def safe_folder(relative: str) -> Path:
    raw = Path(relative)
    if raw.is_absolute() or any(part in {".", ".."} or part.startswith(".") for part in raw.parts):
        raise HTTPException(400, "Недопустимый путь")
    current = config.photos_root
    for part in raw.parts:
        current = current / part
        if current.is_symlink():
            raise HTTPException(400, "Символические ссылки недоступны")
    try:
        path = safe_path(config.photos_root, relative)
    except ValueError as exc:
        raise HTTPException(400, "Недопустимый путь") from exc
    if path.is_symlink() or not path.is_dir():
        raise HTTPException(404, "Папка не найдена")
    return path


def visible_directories(folder: Path) -> list[dict[str, str]]:
    result = []
    for child in folder.iterdir():
        if child.name.startswith(".") or child.is_symlink() or not child.is_dir():
            continue
        result.append({"path": child.relative_to(config.photos_root).as_posix(), "name": child.name})
    return sorted(result, key=lambda item: item["name"].casefold())


class JobRequest(BaseModel):
    scope: str = ""
    duplicate_scope: Literal["scope", "archive"] = "scope"


class ReviewAction(BaseModel):
    finding_ids: list[int] = Field(min_length=1, max_length=500)
    action: Literal["keep", "later", "quarantine", "quality"]


class MediaIds(BaseModel):
    media_ids: list[int] = Field(min_length=1, max_length=500)
    rename_on_conflict: bool = False


class TransferRequest(MediaIds):
    destination: str = ""
    operation: Literal["copy", "move"]


class FolderCreateRequest(BaseModel):
    parent: str = ""
    name: str = Field(min_length=1, max_length=120)


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
    if not config.auth_enabled:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html", context={"default_password": config.password == "change-me"})


@app.post("/login")
def login(request: Request, password: str = Form(...)):
    if not config.auth_enabled:
        return RedirectResponse("/", status_code=303)
    if not password_matches(password, config.password):
        return templates.TemplateResponse(request=request, name="login.html", context={"error": "Неверный пароль", "default_password": config.password == "change-me"}, status_code=401)
    request.session.clear()
    request.session["authenticated"] = True
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login" if config.auth_enabled else "/", status_code=303)


@app.exception_handler(401)
async def unauthorized(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={"categories": CATEGORIES, "auth_enabled": config.auth_enabled})


@app.get("/api/summary", dependencies=[Depends(require_login)])
def summary():
    result_job = latest_result_job()
    counts = []
    if result_job:
        job_id = int(result_job["id"])
        counts = database.all("""
            SELECT f.category, COUNT(*) AS count FROM findings f JOIN media m ON m.id=f.media_id
            WHERE m.status='active' AND m.last_scan_job_id=? AND f.decision='pending'
            GROUP BY f.category
        """, (job_id,))
        quality = database.one("""
            SELECT COUNT(*) AS count FROM media m
            WHERE m.status='active' AND m.last_scan_job_id=? AND m.error IS NULL
              AND (m.manual_quality=1 OR NOT EXISTS(SELECT 1 FROM findings f WHERE f.media_id=m.id))
        """, (job_id,))
        counts = list(counts) + [{"category": "quality", "count": quality["count"]}]
    latest_job = database.one("SELECT * FROM jobs ORDER BY id DESC LIMIT 1")
    library = database.one("""
        SELECT COUNT(*) AS total, SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active,
          SUM(CASE WHEN status='quarantine' THEN 1 ELSE 0 END) AS quarantine,
          SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS errors FROM media
    """)
    return {"categories": [row_dict(row) for row in counts], "job": row_dict(latest_job) if latest_job else None,
            "result_job": row_dict(result_job) if result_job else None, "library": row_dict(library),
            "unsupported": latest_job["unsupported"] if latest_job else 0,
            "warning": config.auth_enabled and config.password == "change-me"}


@app.get("/api/folders", dependencies=[Depends(require_login)])
def folders(path: str = ""):
    folder = safe_folder(path)
    relative = folder.relative_to(config.photos_root).as_posix()
    relative = "" if relative == "." else relative
    parts = relative.split("/") if relative else []
    breadcrumbs = [{"path": "", "name": "Архив"}]
    breadcrumbs += [{"path": "/".join(parts[:index]), "name": parts[index - 1]} for index in range(1, len(parts) + 1)]
    return {"path": relative, "breadcrumbs": breadcrumbs, "directories": visible_directories(folder)}


@app.post("/api/folders", dependencies=[Depends(require_login)])
def create_folder(payload: FolderCreateRequest):
    if payload.name in {".", ".."} or payload.name.startswith(".") or "/" in payload.name or "\\" in payload.name:
        raise HTTPException(400, "Введите имя одной новой папки")
    parent = safe_folder(payload.parent)
    target = parent / payload.name
    if target.exists() or target.is_symlink():
        raise HTTPException(409, "Такая папка уже существует")
    target.mkdir()
    return {"path": target.relative_to(config.photos_root).as_posix(), "name": target.name}


@app.post("/api/jobs", dependencies=[Depends(require_login)])
def create_job(payload: JobRequest):
    active = database.one("SELECT id FROM jobs WHERE state IN ('queued','running','paused') LIMIT 1")
    if active:
        raise HTTPException(409, "Сначала завершите или отмените текущий анализ")
    try:
        return {"id": jobs.create_job(payload.scope, payload.duplicate_scope)}
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/jobs/{job_id}/{action}", dependencies=[Depends(require_login)])
def job_action(job_id: int, action: Literal["pause", "resume", "cancel"]):
    try:
        jobs.set_state(job_id, action)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@app.get("/api/review", dependencies=[Depends(require_login)])
def review(category: str = "exact", page: int = 1, page_size: int = 48):
    if category not in CATEGORIES:
        raise HTTPException(400, "Неизвестная категория")
    result_job = latest_result_job()
    if not result_job:
        return {"items": [], "total": 0, "page": 1, "page_size": page_size}
    page, page_size = max(1, page), min(max(page_size, 1), 100)
    job_id = int(result_job["id"])
    base = "m.status='active' AND m.last_scan_job_id=?"
    if category == "quality":
        where = base + " AND m.error IS NULL AND (m.manual_quality=1 OR NOT EXISTS(SELECT 1 FROM findings f WHERE f.media_id=m.id))"
        params = (job_id,)
        select = "SELECT m.id AS media_id,m.id,m.relative_path,m.size,m.width,m.height,m.captured_at,m.sharpness,m.brightness,m.manual_quality, CASE WHEN m.manual_quality=1 THEN 'Подтверждено вручную' ELSE 'Проблем не обнаружено' END AS reason, 0 AS suggested_best"
        order = "m.relative_path"
    else:
        where = base + " AND f.category=? AND f.decision='pending'"
        params = (job_id, category)
        select = "SELECT f.*,m.relative_path,m.size,m.width,m.height,m.captured_at,m.sharpness,m.brightness,m.manual_quality"
        order = "COALESCE(f.group_key,''),f.suggested_best DESC,f.score DESC,f.id"
    total = database.one(f"SELECT COUNT(*) AS count FROM {'media m' if category == 'quality' else 'findings f JOIN media m ON m.id=f.media_id'} WHERE {where}", params)["count"]
    rows = database.all(f"{select} FROM {'media m' if category == 'quality' else 'findings f JOIN media m ON m.id=f.media_id'} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", (*params, page_size, (page - 1) * page_size))
    return {"items": [row_dict(row) for row in rows], "total": total, "page": page, "page_size": page_size}


@app.post("/api/review/action", dependencies=[Depends(require_login)])
def review_action(payload: ReviewAction):
    placeholders = ",".join("?" for _ in payload.finding_ids)
    rows = database.all(f"SELECT DISTINCT f.media_id,m.relative_path FROM findings f JOIN media m ON m.id=f.media_id WHERE f.id IN ({placeholders}) AND m.status='active'", tuple(payload.finding_ids))
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
            media_ids = [int(row["media_id"]) for row in rows]
            if payload.action == "quality":
                media_marks = ",".join("?" for _ in media_ids)
                connection.execute(f"UPDATE media SET manual_quality=1,updated_at=CURRENT_TIMESTAMP WHERE id IN ({media_marks})", tuple(media_ids))
                connection.execute(f"UPDATE findings SET decision='quality' WHERE media_id IN ({media_marks})", tuple(media_ids))
            else:
                connection.execute(f"UPDATE findings SET decision=? WHERE id IN ({placeholders})", (payload.action, *payload.finding_ids))
            for row in rows:
                connection.execute("INSERT INTO audit_log(action,relative_path,details) VALUES(?,?,?)", (payload.action, row["relative_path"], None))
    return {"ok": not failures, "failures": failures}


@app.post("/api/media/transfer", dependencies=[Depends(require_login)])
def transfer(payload: TransferRequest):
    safe_folder(payload.destination)
    completed, failures = [], []
    for media_id in payload.media_ids:
        try:
            action = storage.copy_media if payload.operation == "copy" else storage.move_media
            completed.append({"media_id": media_id, "path": action(media_id, payload.destination, payload.rename_on_conflict)})
        except (OSError, ValueError) as exc:
            failures.append({"media_id": media_id, "error": str(exc)})
    status = 200 if not failures else 409
    return JSONResponse({"completed": completed, "failures": failures}, status_code=status)


@app.post("/api/media/quarantine", dependencies=[Depends(require_login)])
def quarantine_media(payload: MediaIds):
    moved, failures = [], []
    for media_id in payload.media_ids:
        try:
            moved.append({"media_id": media_id, "path": storage.quarantine_media(media_id)})
        except (OSError, ValueError) as exc:
            failures.append({"media_id": media_id, "error": str(exc)})
    return JSONResponse({"moved": moved, "failures": failures}, status_code=200 if not failures else 409)


@app.get("/thumbnail/{media_id}", dependencies=[Depends(require_login)])
def thumbnail(media_id: int):
    row, path = database.one("SELECT id FROM media WHERE id=?", (media_id,)), config.thumbnail_root / f"{media_id}.jpg"
    if not row or not path.is_file():
        raise HTTPException(404, "Миниатюра не найдена")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/photo/{media_id}", dependencies=[Depends(require_login)])
def photo(media_id: int):
    row = database.one("SELECT * FROM media WHERE id=?", (media_id,))
    if not row:
        raise HTTPException(404, "Файл не найден")
    root, relative = (config.quarantine_root, row["quarantine_path"]) if row["status"] == "quarantine" else (config.photos_root, row["relative_path"])
    path = safe_path(root, relative)
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404, "Файл не найден")
    media_type, _ = mimetypes.guess_type(path.name)
    return FileResponse(path, media_type=media_type or "application/octet-stream")


@app.get("/library-preview/{media_id}", dependencies=[Depends(require_login)])
def library_preview(media_id: int):
    row = database.one("SELECT * FROM media WHERE id=?", (media_id,))
    if not row or row["status"] != "active":
        raise HTTPException(404, "Файл не найден")
    path = safe_path(config.photos_root, row["relative_path"])
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404, "Файл не найден")
    try:
        with Image.open(path) as image:
            preview = ImageOps.exif_transpose(image).convert("RGB")
            preview.thumbnail((520, 360), Image.Resampling.LANCZOS)
            output = BytesIO()
            preview.save(output, format="JPEG", quality=88, optimize=True)
    except OSError as exc:
        raise HTTPException(415, "Невозможно создать миниатюру") from exc
    return Response(output.getvalue(), media_type="image/jpeg")

@app.get("/api/quarantine", dependencies=[Depends(require_login)])
def quarantine():
    rows = database.all("SELECT id,relative_path,quarantine_path,size,captured_at,width,height FROM media WHERE status='quarantine' ORDER BY updated_at DESC")
    return {"items": [row_dict(row) for row in rows], "total_size": sum(int(row["size"] or 0) for row in rows)}


@app.post("/api/quarantine/restore", dependencies=[Depends(require_login)])
def restore(payload: MediaIds):
    restored, failures = [], []
    for media_id in payload.media_ids:
        try:
            restored.append(storage.restore_media(media_id, payload.rename_on_conflict))
        except (OSError, ValueError) as exc:
            failures.append({"media_id": media_id, "error": str(exc)})
    return JSONResponse({"restored": restored, "failures": failures}, status_code=200 if not failures else 409)


@app.post("/api/quarantine/delete", dependencies=[Depends(require_login)])
def delete_quarantine(payload: DeleteRequest):
    if payload.confirmation != "УДАЛИТЬ":
        raise HTTPException(400, "Введите слово УДАЛИТЬ для подтверждения")
    deleted, failures = 0, []
    for media_id in payload.media_ids:
        try:
            storage.delete_media(media_id); deleted += 1
        except (OSError, ValueError) as exc:
            failures.append({"media_id": media_id, "error": str(exc)})
    return {"deleted": deleted, "failures": failures}


@app.get("/api/settings", dependencies=[Depends(require_login)])
def get_settings(): return database.settings()


@app.post("/api/settings", dependencies=[Depends(require_login)])
def save_settings(payload: SettingsRequest): return database.save_settings(payload.model_dump())


@app.get("/api/audit", dependencies=[Depends(require_login)])
def audit(limit: int = 100):
    return {"items": [row_dict(row) for row in database.all("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 500),))]}


@app.get("/health")
def health():
    database.schema_version()
    return {"status": "ok", "database": "ready"}
