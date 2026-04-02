"""Database repository — session factory, init, and CRUD helpers."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import create_engine, event, func, select, update
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
    before ``create_all`` runs.  Then runs lightweight migrations to add any
    columns that were introduced after the table was originally created.
    """
    import job_hunter.market.db_models  # noqa: F401  — register market tables
    import job_hunter.auth.models  # noqa: F401  — register auth tables
    Base.metadata.create_all(engine)

    from job_hunter.db.migrations import run_migrations
    run_migrations(engine)


def make_session(engine: Engine) -> Session:
    """Return a new session bound to *engine*."""
    return sessionmaker(bind=engine)()


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

def _apply_user_filter(query, model, user_id: uuid.UUID | None):
    """Append a ``WHERE model.user_id == user_id`` clause when *user_id* is set."""
    if user_id is not None:
        return query.where(model.user_id == user_id)
    return query


def upsert_job(session: Session, job: Job, *, user_id: uuid.UUID | None = None) -> Job:
    """Insert a job or update it if a duplicate is detected.

    Three-tier dedup lookup (first match wins):

    1. **By external_id** — same LinkedIn job ID, possibly reformatted title/company.
    2. **By hash** — exact match on ``SHA-256(external_id|title|company)``.
    3. **By normalised title + company** — catches jobs reposted with a new
       ``external_id`` (same position, different posting).

    When an existing row is updated and its ``hash`` changes, related
    ``Score`` and ``ApplicationAttempt`` rows are cascade-updated so that
    joins on ``job_hash`` keep working.
    """
    from job_hunter.utils.hashing import normalize_for_dedup

    # --- Tier 1: same external_id ---
    existing = session.execute(
        select(Job).where(Job.external_id == job.external_id)
    ).scalar_one_or_none()

    # --- Tier 2: same hash ---
    if existing is None:
        existing = session.execute(
            select(Job).where(Job.hash == job.hash)
        ).scalar_one_or_none()

    # --- Tier 3: content-based (normalised title + company) ---
    if existing is None:
        norm_title, norm_company = normalize_for_dedup(job.title, job.company)
        if norm_title and norm_company:
            existing = session.execute(
                select(Job).where(
                    func.lower(func.trim(Job.title)) == norm_title,
                    func.lower(func.trim(Job.company)) == norm_company,
                )
            ).scalar_one_or_none()

    if existing is not None:
        old_hash = existing.hash
        updatable = (
            "title", "company", "location", "description_text",
            "easy_apply", "status", "notes", "external_id",
            "description_formatted",
        )
        for attr in updatable:
            value = getattr(job, attr, None)
            if value is not None:
                setattr(existing, attr, value)
        # Recalculate hash after updating fields
        from job_hunter.utils.hashing import job_hash as _calc_hash
        new_hash = _calc_hash(
            external_id=existing.external_id,
            title=existing.title,
            company=existing.company,
        )
        if new_hash != old_hash:
            existing.hash = new_hash
            _cascade_hash_update(session, old_hash, new_hash)
        session.flush()
        return existing

    if user_id is not None and job.user_id is None:
        job.user_id = user_id
    session.add(job)
    try:
        session.flush()
    except Exception:
        # Safety net: IntegrityError on external_id UNIQUE → fall back to update
        session.rollback()
        existing = session.execute(
            select(Job).where(Job.external_id == job.external_id)
        ).scalar_one_or_none()
        if existing is not None:
            return upsert_job(session, job, user_id=user_id)
        raise
    return job


def _cascade_hash_update(session: Session, old_hash: str, new_hash: str) -> None:
    """Update ``job_hash`` in related tables when a Job's hash changes."""
    session.execute(
        update(Score).where(Score.job_hash == old_hash).values(job_hash=new_hash)
    )
    session.execute(
        update(ApplicationAttempt)
        .where(ApplicationAttempt.job_hash == old_hash)
        .values(job_hash=new_hash)
    )
    # Also update market_events if the table exists
    try:
        from job_hunter.market.db_models import MarketEvent
        session.execute(
            update(MarketEvent)
            .where(MarketEvent.job_hash == old_hash)
            .values(job_hash=new_hash)
        )
    except Exception:
        pass


def get_jobs_by_status(
    session: Session, status: JobStatus, *, user_id: uuid.UUID | None = None,
) -> Sequence[Job]:
    """Return all jobs matching the given status."""
    query = select(Job).where(Job.status == status)
    query = _apply_user_filter(query, Job, user_id)
    return session.execute(query).scalars().all()


def get_all_jobs(session: Session, *, user_id: uuid.UUID | None = None) -> Sequence[Job]:
    """Return all jobs."""
    query = select(Job)
    query = _apply_user_filter(query, Job, user_id)
    return session.execute(query).scalars().all()


def count_jobs_by_status(
    session: Session, *, user_id: uuid.UUID | None = None,
) -> dict[str, int]:
    """Return a dict mapping status name → count."""
    query = select(Job.status, func.count()).group_by(Job.status)
    query = _apply_user_filter(query, Job, user_id)
    rows = session.execute(query).all()
    return {status.value: count for status, count in rows}


def get_scores_for_jobs(
    session: Session, job_hashes: list[str], *, user_id: uuid.UUID | None = None,
) -> dict[str, Score]:
    """Return a dict mapping job_hash → Score for the given hashes."""
    if not job_hashes:
        return {}
    query = select(Score).where(Score.job_hash.in_(job_hashes))
    query = _apply_user_filter(query, Score, user_id)
    scores = session.execute(query).scalars().all()
    return {s.job_hash: s for s in scores}


def get_attempts_today(
    session: Session, *, user_id: uuid.UUID | None = None,
) -> Sequence[ApplicationAttempt]:
    """Return all application attempts started today (UTC)."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    query = select(ApplicationAttempt).where(ApplicationAttempt.started_at >= today_start)
    query = _apply_user_filter(query, ApplicationAttempt, user_id)
    return session.execute(query).scalars().all()


def count_applied_today(
    session: Session, *, user_id: uuid.UUID | None = None,
) -> int:
    """Count successful applications (SUCCESS or DRY_RUN) started today."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    query = select(func.count()).select_from(ApplicationAttempt).where(
        ApplicationAttempt.started_at >= today_start,
        ApplicationAttempt.result.in_([ApplicationResult.SUCCESS, ApplicationResult.DRY_RUN]),
    )
    query = _apply_user_filter(query, ApplicationAttempt, user_id)
    result = session.execute(query).scalar()
    return result or 0


def get_top_missing_skills(
    session: Session, limit: int = 10, *, user_id: uuid.UUID | None = None,
) -> list[tuple[str, int]]:
    """Return the most common missing skills across all scores."""
    query = select(Score.missing_skills)
    query = _apply_user_filter(query, Score, user_id)
    scores = session.execute(query).scalars().all()
    counter: Counter[str] = Counter()
    for skills_list in scores:
        if isinstance(skills_list, list):
            for skill in skills_list:
                counter[skill] += 1
    return counter.most_common(limit)


def save_score(session: Session, score: Score, *, user_id: uuid.UUID | None = None) -> Score:
    """Persist a Score row."""
    if user_id is not None and score.user_id is None:
        score.user_id = user_id
    session.add(score)
    session.flush()
    return score


def save_attempt(
    session: Session, attempt: ApplicationAttempt, *, user_id: uuid.UUID | None = None,
) -> ApplicationAttempt:
    """Persist an ApplicationAttempt row."""
    if user_id is not None and attempt.user_id is None:
        attempt.user_id = user_id
    session.add(attempt)
    session.flush()
    return attempt


def delete_job(session: Session, job_hash: str, *, user_id: uuid.UUID | None = None) -> bool:
    """Delete a job and its related scores and application attempts.

    Returns True if the job existed and was deleted.
    """
    query = select(Job).where(Job.hash == job_hash)
    query = _apply_user_filter(query, Job, user_id)
    job = session.execute(query).scalar_one_or_none()
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


