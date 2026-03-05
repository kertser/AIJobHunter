"""FastAPI dependency injection — DB session, settings, task manager."""

from __future__ import annotations

from typing import Generator

from fastapi import Request
from sqlalchemy.orm import Session

from job_hunter.config.models import AppSettings
from job_hunter.db.repo import make_session
from job_hunter.web.task_manager import TaskManager


def get_db(request: Request) -> Generator[Session, None, None]:
    """Yield a DB session, commit on success, rollback on error."""
    session = make_session(request.app.state.engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_settings(request: Request) -> AppSettings:
    return request.app.state.settings


def get_task_manager(request: Request) -> TaskManager:
    return request.app.state.task_manager

