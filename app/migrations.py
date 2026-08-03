from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable


class MigrationError(RuntimeError):
    """Raised when a database cannot be migrated safely."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    up: tuple[str, ...]
    down: tuple[str, ...]


MIGRATIONS = (
    Migration(
        1,
        "initial_schema",
        (
            """
            CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE media (
                id INTEGER PRIMARY KEY,
                relative_path TEXT NOT NULL UNIQUE,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                width INTEGER,
                height INTEGER,
                captured_at TEXT,
                sha256 TEXT,
                phash TEXT,
                brightness REAL,
                sharpness REAL,
                edge_density REAL,
                text_length INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                quarantine_path TEXT,
                analysis_revision INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            "CREATE INDEX media_hash_idx ON media(sha256)",
            "CREATE INDEX media_status_idx ON media(status)",
            "CREATE INDEX media_phash_idx ON media(phash)",
            """
            CREATE TABLE findings (
                id INTEGER PRIMARY KEY,
                media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
                category TEXT NOT NULL,
                reason TEXT NOT NULL,
                score REAL NOT NULL DEFAULT 0,
                group_key TEXT,
                suggested_best INTEGER NOT NULL DEFAULT 0,
                decision TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(media_id, category, group_key)
            )
            """,
            """
            CREATE INDEX findings_queue_idx
                ON findings(category, decision)
            """,
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY,
                scope TEXT NOT NULL,
                duplicate_scope TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'queued',
                total INTEGER NOT NULL DEFAULT 0,
                processed INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                unsupported INTEGER NOT NULL DEFAULT 0,
                errors INTEGER NOT NULL DEFAULT 0,
                message TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TEXT,
                finished_at TEXT
            )
            """,
            """
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY,
                action TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
        ),
        (
            "DROP TABLE audit_log",
            "DROP TABLE jobs",
            "DROP TABLE findings",
            "DROP TABLE media",
            "DROP TABLE settings",
        ),
    ),
    Migration(
        2,
        "scan_job_reference",
        ("ALTER TABLE media ADD COLUMN last_scan_job_id INTEGER",),
        ("ALTER TABLE media DROP COLUMN last_scan_job_id",),
    ),
    Migration(
        3,
        "manual_quality",
        (
            "ALTER TABLE media ADD COLUMN "
            "manual_quality INTEGER NOT NULL DEFAULT 0",
        ),
        ("ALTER TABLE media DROP COLUMN manual_quality",),
    ),
    Migration(
        4,
        "general_job_type",
        (
            "ALTER TABLE jobs ADD COLUMN "
            "kind TEXT NOT NULL DEFAULT 'analysis'",
            "ALTER TABLE jobs ADD COLUMN payload TEXT",
        ),
        (
            "ALTER TABLE jobs DROP COLUMN payload",
            "ALTER TABLE jobs DROP COLUMN kind",
        ),
    ),
    Migration(
        5,
        "light_library_index",
        (
            "ALTER TABLE media ADD COLUMN media_type TEXT NOT NULL DEFAULT 'photo'",
            "ALTER TABLE media ADD COLUMN library_root TEXT NOT NULL DEFAULT 'photos'",
            "ALTER TABLE media ADD COLUMN file_name TEXT",
            "ALTER TABLE media ADD COLUMN parent_relative_path TEXT",
            "ALTER TABLE media ADD COLUMN mime_type TEXT",
            "ALTER TABLE media ADD COLUMN container_id INTEGER",
            "ALTER TABLE media ADD COLUMN index_state TEXT NOT NULL DEFAULT 'indexed'",
            "ALTER TABLE media ADD COLUMN missing_since TEXT",
            """
            CREATE TABLE containers (
                id INTEGER PRIMARY KEY,
                library_root TEXT NOT NULL,
                media_type TEXT NOT NULL,
                kind TEXT NOT NULL,
                year TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                name TEXT NOT NULL,
                cover_media_id INTEGER,
                cover_mode TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                missing_since TEXT,
                UNIQUE(library_root, media_type, kind, relative_path)
            )
            """,
            "CREATE INDEX media_library_idx ON media(library_root, media_type, index_state)",
            "CREATE INDEX containers_library_idx ON containers(library_root, media_type, year)",
        ),
        (
            "DROP TABLE containers",
            "ALTER TABLE media DROP COLUMN missing_since",
            "ALTER TABLE media DROP COLUMN index_state",
            "ALTER TABLE media DROP COLUMN container_id",
            "ALTER TABLE media DROP COLUMN mime_type",
            "ALTER TABLE media DROP COLUMN parent_relative_path",
            "ALTER TABLE media DROP COLUMN file_name",
            "ALTER TABLE media DROP COLUMN library_root",
            "ALTER TABLE media DROP COLUMN media_type",
        ),
    ),
    Migration(
        6,
        "unsorted_section",
        (
            "ALTER TABLE media ADD COLUMN collection_state TEXT NOT NULL DEFAULT 'album'",
            "ALTER TABLE media ADD COLUMN source_name TEXT",
            "ALTER TABLE media ADD COLUMN source_relative_path TEXT",
            "ALTER TABLE media ADD COLUMN imported_at TEXT NOT NULL DEFAULT '1970-01-01 00:00:00'",
            "ALTER TABLE media ADD COLUMN date_source TEXT NOT NULL DEFAULT 'import'",
            "UPDATE media SET imported_at=CURRENT_TIMESTAMP",
            """
            UPDATE media SET collection_state=CASE
                WHEN status='quarantine' THEN 'quarantine'
                WHEN library_root='photos' AND relative_path LIKE 'Unsorted/%'
                  AND container_id IS NULL THEN 'unsorted'
                ELSE 'album'
            END
            """,
            """
            UPDATE media SET source_name=CASE
                WHEN collection_state='unsorted'
                  AND parent_relative_path IS NOT NULL
                  AND parent_relative_path <> 'Unsorted'
                THEN substr(
                    substr(parent_relative_path, length('Unsorted/') + 1),
                    1,
                    instr(substr(parent_relative_path, length('Unsorted/') + 1) || '/', '/') - 1
                )
                ELSE NULL
            END
            """,
            """
            UPDATE media SET source_relative_path=CASE
                WHEN collection_state='unsorted'
                  AND parent_relative_path IS NOT NULL
                  AND parent_relative_path <> 'Unsorted'
                THEN substr(parent_relative_path, length('Unsorted/') + 1)
                ELSE NULL
            END
            """,
            "CREATE INDEX media_collection_idx ON media(library_root, media_type, collection_state, status, index_state)",
            "CREATE INDEX media_source_idx ON media(source_name)",
        ),
        (
            "DROP INDEX IF EXISTS media_source_idx",
            "DROP INDEX IF EXISTS media_collection_idx",
            "ALTER TABLE media DROP COLUMN date_source",
            "ALTER TABLE media DROP COLUMN imported_at",
            "ALTER TABLE media DROP COLUMN source_relative_path",
            "ALTER TABLE media DROP COLUMN source_name",
            "ALTER TABLE media DROP COLUMN collection_state",
        ),
    ),
    Migration(
        7,
        "operation_model",
        (
            """
            CREATE TABLE operations (
                id TEXT PRIMARY KEY,
                operation_type TEXT NOT NULL,
                scope_type TEXT,
                scope_id TEXT,
                initiator_type TEXT,
                initiator_id TEXT,
                status TEXT NOT NULL,
                title TEXT NOT NULL,
                stage TEXT,
                total_items INTEGER NOT NULL DEFAULT 0,
                processed_items INTEGER NOT NULL DEFAULT 0,
                succeeded_items INTEGER NOT NULL DEFAULT 0,
                failed_items INTEGER NOT NULL DEFAULT 0,
                skipped_items INTEGER NOT NULL DEFAULT 0,
                progress_percent INTEGER NOT NULL DEFAULT 0,
                can_pause INTEGER NOT NULL DEFAULT 0,
                can_cancel INTEGER NOT NULL DEFAULT 0,
                can_resume INTEGER NOT NULL DEFAULT 0,
                can_retry_failed INTEGER NOT NULL DEFAULT 0,
                can_continue INTEGER NOT NULL DEFAULT 0,
                requires_confirmation INTEGER NOT NULL DEFAULT 0,
                parameters_json TEXT NOT NULL DEFAULT '{}',
                result_json TEXT,
                error_code TEXT,
                user_message TEXT,
                parent_operation_id TEXT REFERENCES operations(id) ON DELETE RESTRICT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                queued_at TEXT,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                version INTEGER NOT NULL DEFAULT 1
            )
            """,
            "CREATE INDEX operations_status_idx ON operations(status)",
            "CREATE INDEX operations_type_idx ON operations(operation_type)",
            "CREATE INDEX operations_created_idx ON operations(created_at)",
            "CREATE INDEX operations_parent_idx ON operations(parent_operation_id)",
            """
            CREATE TABLE operation_items (
                id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL REFERENCES operations(id) ON DELETE RESTRICT,
                item_type TEXT NOT NULL,
                item_id TEXT NOT NULL,
                source_version TEXT,
                status TEXT NOT NULL,
                stage TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                error_code TEXT,
                user_message TEXT,
                result_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(operation_id, item_type, item_id)
            )
            """,
            "CREATE INDEX operation_items_operation_idx ON operation_items(operation_id)",
            "CREATE INDEX operation_items_object_idx ON operation_items(item_type, item_id)",
        ),
        (
            "DROP TABLE operation_items",
            "DROP TABLE operations",
        ),
    ),
    Migration(
        8,
        "operation_idempotency",
        (
            """
            CREATE TABLE operation_commands (
                idempotency_key TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL UNIQUE REFERENCES operations(id) ON DELETE RESTRICT,
                fingerprint TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
        ),
        ("DROP TABLE operation_commands",),
    ),
    Migration(
        9,
        "file_operation_execution_journal",
        (
            """
            CREATE TABLE file_execution_commands (
                command_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL,
                operation_item_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT,
                error_code TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            "CREATE INDEX file_execution_commands_operation_idx ON file_execution_commands(operation_id)",
        ),
        ("DROP TABLE file_execution_commands",),
    ),
    Migration(
        10,
        "transactional_outbox",
        (
            """
            CREATE TABLE outbox_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                aggregate_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                operation_id TEXT,
                payload TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                available_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                processed_at TEXT,
                last_error_code TEXT,
                last_error_message TEXT,
                locked_at TEXT,
                locked_by TEXT
            )
            """,
            "CREATE INDEX outbox_events_available_idx ON outbox_events(status, available_at, id)",
            "CREATE INDEX outbox_events_aggregate_idx ON outbox_events(aggregate_type, aggregate_id, id)",
        ),
        ("DROP TABLE outbox_events",),
    ),
    Migration(
        11,
        "diagnostic_events",
        (
            """
            CREATE TABLE diagnostic_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_code TEXT NOT NULL,
                severity TEXT NOT NULL,
                component TEXT NOT NULL,
                title TEXT NOT NULL,
                user_message TEXT NOT NULL,
                suggested_action TEXT NOT NULL,
                technical_reference TEXT,
                object_type TEXT,
                object_id TEXT,
                operation_id TEXT,
                first_occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                occurrence_count INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL,
                resolved_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            "CREATE INDEX diagnostic_events_operation_idx ON diagnostic_events(operation_id)",
            "CREATE INDEX diagnostic_events_active_idx ON diagnostic_events(status, severity, last_occurred_at)",
        ),
        ("DROP TABLE diagnostic_events",),
    ),
)

LATEST_SCHEMA_VERSION = MIGRATIONS[-1].version
APPLICATION_TABLES = {"settings", "media", "findings", "jobs", "audit_log"}


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            """
        )
    }


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def detect_schema_version(connection: sqlite3.Connection) -> int:
    """Return the version, including for databases created before migrations."""
    tables = _tables(connection)
    application_tables = tables & APPLICATION_TABLES
    if not application_tables:
        unrelated = tables - {"schema_migrations"}
        if unrelated:
            raise MigrationError(
                "База содержит неизвестные таблицы и не похожа на Photo Review"
            )
        inferred = 0
    elif application_tables != APPLICATION_TABLES:
        missing = ", ".join(sorted(APPLICATION_TABLES - application_tables))
        raise MigrationError(f"Неполная схема Photo Review; отсутствуют: {missing}")
    else:
        media_columns = _columns(connection, "media")
        job_columns = _columns(connection, "jobs")
        has_scan_job = "last_scan_job_id" in media_columns
        has_manual_quality = "manual_quality" in media_columns
        library_columns = {"media_type", "library_root", "file_name", "parent_relative_path", "mime_type", "container_id", "index_state", "missing_since"}
        unsorted_columns = {"collection_state", "source_name", "source_relative_path", "imported_at", "date_source"}
        library_count = len(library_columns & media_columns)
        has_library_index = library_count == len(library_columns) and "containers" in tables
        unsorted_count = len(unsorted_columns & media_columns)
        has_unsorted_section = unsorted_count == len(unsorted_columns)
        if unsorted_count and not has_unsorted_section:
            raise MigrationError("Структура раздела «Неразобранное» применена частично")
        if library_count and not has_library_index:
            raise MigrationError("Структура медиатеки содержит частично применённую миграцию")
        job_type_columns = {"kind", "payload"} & job_columns
        has_job_type = job_type_columns == {"kind", "payload"}
        if job_type_columns and not has_job_type:
            raise MigrationError(
                "Структура таблицы jobs содержит частично применённую миграцию"
            )
        if has_job_type and not (has_scan_job and has_manual_quality):
            raise MigrationError("Структура таблиц не соответствует известной миграции")
        if has_manual_quality and not has_scan_job:
            raise MigrationError("Структура таблицы media непоследовательна")
        operation_tables = {"operations", "operation_items"}
        operation_count = len(operation_tables & tables)
        if operation_count and operation_count != len(operation_tables):
            raise MigrationError("Модель операций применена частично")
        has_operation_model = operation_count == len(operation_tables)
        if has_operation_model:
            operation_columns = _columns(connection, "operations")
            item_columns = _columns(connection, "operation_items")
            if not {"id", "status", "version", "parent_operation_id"} <= operation_columns or not {"id", "operation_id", "item_type", "item_id", "status"} <= item_columns:
                raise MigrationError("Структура модели операций неполная")
        has_idempotency = "operation_commands" in tables
        if has_idempotency and not has_operation_model:
            raise MigrationError("Ключи идемпотентности существуют без модели операций")
        if has_idempotency:
            command_columns = _columns(connection, "operation_commands")
            if not {"idempotency_key", "operation_id", "fingerprint"} <= command_columns:
                raise MigrationError("Структура ключей идемпотентности неполная")
        has_file_executor = "file_execution_commands" in tables
        if has_file_executor and not has_idempotency:
            raise MigrationError("Журнал файловых операций существует без модели операций")
        if has_file_executor:
            execution_columns = _columns(connection, "file_execution_commands")
            if not {"command_id", "operation_id", "operation_item_id", "fingerprint", "status"} <= execution_columns:
                raise MigrationError("Журнал файловых операций неполный")
        has_outbox = "outbox_events" in tables
        if has_outbox and not has_file_executor:
            raise MigrationError("Transactional outbox существует без журнала файловых операций")
        if has_outbox:
            outbox_columns = _columns(connection, "outbox_events")
            if not {"id", "event_id", "event_type", "aggregate_type", "aggregate_id", "payload", "status", "attempt_count", "available_at"} <= outbox_columns:
                raise MigrationError("Transactional outbox неполный")
        has_diagnostics = "diagnostic_events" in tables
        if has_diagnostics and not has_outbox:
            raise MigrationError("Диагностика существует без transactional outbox")
        if has_diagnostics:
            diagnostic_columns = _columns(connection, "diagnostic_events")
            if not {"id", "event_code", "severity", "component", "operation_id", "occurrence_count", "status"} <= diagnostic_columns:
                raise MigrationError("Таблица диагностических событий неполная")
        inferred = (
            11
            if has_diagnostics
            else 10
            if has_outbox
            else 9
            if has_file_executor
            else 8
            if has_idempotency
            else 7
            if has_operation_model
            else 6
            if has_unsorted_section
            else 5
            if has_library_index
            else 4
            if has_job_type
            else 3
            if has_manual_quality
            else 2
            if has_scan_job
            else 1
        )

    declared = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if declared > LATEST_SCHEMA_VERSION:
        raise MigrationError(
            f"Версия базы {declared} новее поддерживаемой {LATEST_SCHEMA_VERSION}"
        )
    if declared and declared != inferred:
        raise MigrationError(
            f"Версия схемы {declared} не соответствует структуре версии {inferred}"
        )
    return inferred


def _execute_all(
    connection: sqlite3.Connection, statements: Iterable[str]
) -> None:
    for statement in statements:
        connection.execute(statement)


def migrate(connection: sqlite3.Connection, current_version: int) -> None:
    if current_version > LATEST_SCHEMA_VERSION:
        raise MigrationError("Откат более новой базы автоматически запрещён")
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for migration in MIGRATIONS[:current_version]:
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, name)
                VALUES (?, ?)
                """,
                (migration.version, migration.name),
            )
        for migration in MIGRATIONS[current_version:]:
            _execute_all(connection, migration.up)
            connection.execute(
                "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                (migration.version, migration.name),
            )
            connection.execute(f"PRAGMA user_version={migration.version}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def rollback(
    connection: sqlite3.Connection, current_version: int, target_version: int
) -> None:
    if target_version < 0 or target_version > current_version:
        raise ValueError("Недопустимая целевая версия схемы")
    connection.execute("BEGIN IMMEDIATE")
    try:
        for migration in reversed(MIGRATIONS[target_version:current_version]):
            if migration.version == 5:
                connection.execute("DROP INDEX IF EXISTS containers_library_idx")
                connection.execute("DROP INDEX IF EXISTS media_library_idx")
            connection.execute(
                "DELETE FROM schema_migrations WHERE version=?",
                (migration.version,),
            )
            _execute_all(connection, migration.down)
            connection.execute(f"PRAGMA user_version={migration.version - 1}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
