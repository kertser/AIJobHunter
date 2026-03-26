"""Database repository — session factory, init, and CRUD helpers."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from job_hunter.db.models import (
    ApplicationAttempt,
    ApplicationResult,
    Base,
    Job,
    JobStatus,
    Score,
)


def _set_sqlite_wal(dbapi_conn, connection_record):
    """Enable WAL journal mode for concurrent read/write access."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def get_engine(data_dir: Path) -> Engine:
    """Create a SQLAlchemy engine pointing at the SQLite DB inside *data_dir*."""
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "job_hunter.db"
    eng = create_engine(f"sqlite:///{db_path}", echo=False)
    event.listen(eng, "connect", _set_sqlite_wal)
    return eng


def get_memory_engine() -> Engine:
    """Return an in-memory SQLite engine (useful for tests).

    Uses StaticPool + check_same_thread=False so the same in-memory DB
    can be shared across threads (needed by FastAPI TestClient).
    """
    return create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def init_db(engine: Engine) -> None:
    """Create all tables defined in the ORM metadata.

    Imports market models so their tables are registered on ``Base.metadata``
    before ``create_all`` runs.
    """
    import job_hunter.market.db_models  # noqa: F401  — register market tables
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


def get_all_jobs(session: Session) -> Sequence[Job]:
    """Return all jobs."""
    return session.execute(select(Job)).scalars().all()


def count_jobs_by_status(session: Session) -> dict[str, int]:
    """Return a dict mapping status name → count."""
    rows = session.execute(
        select(Job.status, func.count()).group_by(Job.status)
    ).all()
    return {status.value: count for status, count in rows}


def get_scores_for_jobs(session: Session, job_hashes: list[str]) -> dict[str, Score]:
    """Return a dict mapping job_hash → Score for the given hashes."""
    if not job_hashes:
        return {}
    scores = session.execute(
        select(Score).where(Score.job_hash.in_(job_hashes))
    ).scalars().all()
    return {s.job_hash: s for s in scores}


def get_attempts_today(session: Session) -> Sequence[ApplicationAttempt]:
    """Return all application attempts started today (UTC)."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return session.execute(
        select(ApplicationAttempt).where(ApplicationAttempt.started_at >= today_start)
    ).scalars().all()


def count_applied_today(session: Session) -> int:
    """Count successful applications (SUCCESS or DRY_RUN) started today."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    result = session.execute(
        select(func.count()).select_from(ApplicationAttempt).where(
            ApplicationAttempt.started_at >= today_start,
            ApplicationAttempt.result.in_([ApplicationResult.SUCCESS, ApplicationResult.DRY_RUN]),
        )
    ).scalar()
    return result or 0


def get_top_missing_skills(session: Session, limit: int = 10) -> list[tuple[str, int]]:
    """Return the most common missing skills across all scores."""
    scores = session.execute(select(Score.missing_skills)).scalars().all()
    counter: Counter[str] = Counter()
    for skills_list in scores:
        if isinstance(skills_list, list):
            for skill in skills_list:
                counter[skill] += 1
    return counter.most_common(limit)


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


def delete_job(session: Session, job_hash: str) -> bool:
    """Delete a job and its related scores and application attempts.

    Returns True if the job existed and was deleted.
    """
    job = session.execute(select(Job).where(Job.hash == job_hash)).scalar_one_or_none()
    if not job:
        return False

    # Delete related scores
    scores = session.execute(select(Score).where(Score.job_hash == job_hash)).scalars().all()
    for s in scores:
        session.delete(s)

    # Delete related attempts
    attempts = session.execute(
        select(ApplicationAttempt).where(ApplicationAttempt.job_hash == job_hash)
    ).scalars().all()
    for a in attempts:
        session.delete(a)

    session.delete(job)
    session.flush()
    return True


