# Testing the new core

All tests use `tmp_path` and temporary SQLite files. They do not use the configured user media roots.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_operations.py tests\test_operation_manager.py tests\test_resource_locks.py -q
.\.venv\Scripts\python.exe -m pytest tests\test_file_operation_executor.py tests\test_outbox.py tests\test_operation_recovery.py tests\test_core_integration.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

The known Windows symlink checks are skipped only when the process lacks the required symbolic-link privilege. All other tests are required to pass.
