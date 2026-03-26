"""Convert existing jobs into normalised market events (idempotent)."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_hunter.db.models import Job
from job_hunter.market.db_models import MarketEvent, MarketEventType

logger = logging.getLogger("job_hunter.market.events")


def ingest_jobs(session: Session) -> int:
    """Create a :class:`MarketEvent` for every job that doesn't have one yet.

    Returns the number of new events created.
    """
    # Existing event hashes (only JOB_POSTING type)
    existing_hashes: set[str] = set(
        session.execute(
            select(MarketEvent.job_hash).where(
                MarketEvent.event_type == MarketEventType.JOB_POSTING,
            )
        ).scalars().all()
    )

    jobs: list[Job] = list(session.execute(select(Job)).scalars().all())
    created = 0

    for job in jobs:
        if job.hash in existing_hashes:
            continue

        event = MarketEvent(
            event_type=MarketEventType.JOB_POSTING,
            source_type=job.source,
            job_hash=job.hash,
            company=job.company,
            title=job.title,
            raw_text=job.description_text or "",
            published_at=job.posted_at,
            collected_at=job.collected_at,
        )
        session.add(event)
        created += 1

    if created:
        session.flush()
    logger.info("Ingested %d new market event(s) from %d job(s)", created, len(jobs))
    return created

