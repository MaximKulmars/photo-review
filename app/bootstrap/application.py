"""Dependency composition for the current PhotoHome application."""

from dataclasses import dataclass

from ..analyzer import JobManager
from ..config import Config
from ..db import Database
from ..library import LibraryIndexer
from ..storage import Storage


@dataclass(frozen=True)
class ApplicationDependencies:
    """Concrete dependencies kept at the bootstrap boundary during migration."""

    database: Database
    storage: Storage
    jobs: JobManager
    library_indexer: LibraryIndexer


def build_application_dependencies(config: Config) -> ApplicationDependencies:
    database = Database(config.database_path)
    return ApplicationDependencies(
        database=database,
        storage=Storage(config.photos_root, config.quarantine_root, database),
        jobs=JobManager(database, config.photos_root, config.thumbnail_root),
        library_indexer=LibraryIndexer(database, config.library_roots),
    )
