"""FastAPI application factory during the legacy-router migration."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Return the compatibility application while routes move in later tasks."""
    from ..main import app as legacy_app

    return legacy_app
