from __future__ import annotations

import os

import pytest

from app.config import load_config


def clear_runtime(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("PHOTO_REVIEW_"):
            monkeypatch.delenv(key, raising=False)


def test_development_defaults_to_a_safe_project_local_root(monkeypatch, tmp_path):
    clear_runtime(monkeypatch)
    monkeypatch.chdir(tmp_path)
    config = load_config()
    assert config.data_root == (tmp_path / ".photo-review" / "data").resolve()
    assert config.photos_root.is_relative_to(config.data_root)
    assert config.huey_db_path != config.database_path


def test_production_requires_explicit_roots_and_non_default_secrets(monkeypatch, tmp_path):
    clear_runtime(monkeypatch)
    monkeypatch.setenv("PHOTO_REVIEW_ENV", "production")
    with pytest.raises(ValueError, match="explicit settings"):
        load_config()
    monkeypatch.setenv("PHOTO_REVIEW_PHOTOS", str(tmp_path / "photos"))
    monkeypatch.setenv("PHOTO_REVIEW_QUARANTINE", str(tmp_path / "quarantine"))
    monkeypatch.setenv("PHOTO_REVIEW_DATA", str(tmp_path / "data"))
    with pytest.raises(ValueError, match="non-default"):
        load_config()


def test_test_mode_refuses_roots_outside_its_temporary_data_root(monkeypatch, tmp_path):
    clear_runtime(monkeypatch)
    monkeypatch.setenv("PHOTO_REVIEW_TEST_MODE", "true")
    monkeypatch.setenv("PHOTO_REVIEW_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PHOTO_REVIEW_PHOTOS", str(tmp_path / "real-library"))
    monkeypatch.setenv("PHOTO_REVIEW_QUARANTINE", str(tmp_path / "data" / "quarantine"))
    with pytest.raises(ValueError, match="Test mode"):
        load_config()
