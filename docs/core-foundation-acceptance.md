# Core Foundation Acceptance

Status: accepted with documented limitations.

## Verified contracts

| Area | Evidence |
| --- | --- |
| Layer boundaries | `tests/test_zz_architecture.py` |
| Operations and transitions | `tests/test_operations.py`, `tests/test_operation_manager.py` |
| Worker persistence | `tests/test_huey.py` |
| Filesystem safety | `tests/test_file_operation_executor.py` |
| Outbox and diagnostics | `tests/test_outbox.py`, `tests/test_diagnostics.py` |
| API safety | `tests/test_operations_api.py`, `tests/test_api.py` |
| Locks and recovery | `tests/test_resource_locks.py`, `tests/test_operation_recovery.py` |
| Cross-component path | `tests/test_core_integration.py` |
| Runtime configuration | `tests/test_config_runtime.py` |

## Acceptance decision

The core is ready for planning the first safe vertical slice, such as album
rename, provided that slice registers its own outbox handlers and moves only
its own legacy path through the new executor.

## Limitations retained by design

- Existing user workflows still use legacy `Storage`.
- No real business outbox subscribers are registered yet.
- The current UI does not consume the operation API.
- Multi-user authorization does not exist yet.
- The repository consistently uses `sqlite3` and local migrations instead of
  SQLAlchemy/Alembic referenced by some historical design notes.
- Windows symbolic-link success tests need Developer Mode or the appropriate
  privilege; failure handling remains covered.

## Non-goals

This acceptance does not deploy the service, migrate user media, or move a
real user workflow to the new core.
