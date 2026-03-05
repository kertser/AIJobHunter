"""Database repository — session factory, init, and CRUD helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from job_hunter.db.models import (
    ApplicationAttempt,
    Base,
    Job,
    JobStatus,
    Score,
)


def get_engine(data_dir: Path) -> Engine:
    """Create a SQLAlchemy engine pointing at the SQLite DB inside *data_dir*."""
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "job_hunter.db"
    return create_engine(f"sqlite:///{db_path}", echo=False)


def get_memory_engine() -> Engine:
    """Return an in-memory SQLite engine (useful for tests)."""
    return create_engine("sqlite:///:memory:", echo=False)


def init_db(engine: Engine) -> None:
    """Create all tables defined in the ORM metadata."""
    Base.metadata.create_all(engine)


def make_session(engine: Engine) -> Session:
    """Return a new session bound to *engine*."""
    return sessionmaker(bind=engine)()


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

def upsert_job(session: Session, job: Job) -> Job:
    """Insert a job or update it if a job with the same hash already exists."""
    existing = session.execute(
        select(Job).where(Job.hash == job.hash)
    ).scalar_one_or_none()

    if existing is not None:
        # Only update fields that are explicitly set on the incoming object
        updatable = (
            "title", "company", "location", "description_text",
            "easy_apply", "status", "notes",
        )
        for attr in updatable:
            value = getattr(job, attr, None)
            if value is not None:
                setattr(existing, attr, value)
        session.flush()
        return existing

    session.add(job)
    session.flush()
    return job


def get_jobs_by_status(session: Session, status: JobStatus) -> Sequence[Job]:
    """Return all jobs matching the given status."""
    return session.execute(
        select(Job).where(Job.status == status)
    ).scalars().all()


def save_score(session: Session, score: Score) -> Score:
    """Persist a Score row."""
    session.add(score)
    session.flush()
    return score


def save_attempt(session: Session, attempt: ApplicationAttempt) -> ApplicationAttempt:
    """Persist an ApplicationAttempt row."""
    session.add(attempt)
    session.flush()
    return attempt


