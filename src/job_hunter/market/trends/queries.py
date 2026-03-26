"""SQL helper queries for trend analysis.

Provides time-bucketed entity frequency counts and related aggregations
used by :mod:`market.trends.compute`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from job_hunter.market.db_models import (
    EntityType,
    MarketEntity,
    MarketEvidence,
    MarketSnapshot,
)

logger = logging.getLogger("job_hunter.market.trends.queries")


def recent_entity_counts(
    session: Session,
    days: int = 30,
    entity_types: list[EntityType] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Top entities by evidence count in the last *days* days.

    Returns dicts with keys: entity_id, canonical_name, display_name,
    entity_type, count.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    q = (
        select(
            MarketEntity.id,
            MarketEntity.canonical_name,
            MarketEntity.display_name,
            MarketEntity.entity_type,
            func.count(MarketEvidence.id).label("cnt"),
        )
        .join(MarketEvidence, MarketEvidence.entity_id == MarketEntity.id)
        .where(MarketEvidence.observed_at >= cutoff)
    )
    if entity_types:
        q = q.where(MarketEntity.entity_type.in_(entity_types))
    q = (
        q.group_by(MarketEntity.id)
        .order_by(func.count(MarketEvidence.id).desc())
        .limit(limit)
    )

    rows = session.execute(q).all()
    return [
        {
            "entity_id": r[0],
            "canonical_name": r[1],
            "display_name": r[2],
            "entity_type": r[3].value if hasattr(r[3], "value") else r[3],
            "count": r[4],
        }
        for r in rows
    ]


def entity_frequency_by_bucket(
    session: Session,
    entity_id: int,
    bucket_days: int = 7,
    num_buckets: int = 12,
) -> list[dict[str, Any]]:
    """Evidence counts per time bucket for a single entity (newest-first)."""
    now = datetime.now(timezone.utc)
    buckets: list[dict[str, Any]] = []

    for i in range(num_buckets):
        end = now - timedelta(days=i * bucket_days)
        start = end - timedelta(days=bucket_days)
        count = session.execute(
            select(func.count(MarketEvidence.id)).where(
                MarketEvidence.entity_id == entity_id,
                MarketEvidence.observed_at >= start,
                MarketEvidence.observed_at < end,
            )
        ).scalar() or 0
        buckets.append({
            "bucket_start": start.isoformat(),
            "bucket_end": end.isoformat(),
            "count": count,
        })

    return buckets


def all_entity_bucket_counts(
    session: Session,
    bucket_days: int = 7,
    num_buckets: int = 4,
) -> dict[int, list[int]]:
    """Return ``{entity_id: [newest_count, ..., oldest_count]}`` for every entity.

    Used by :func:`compute.compute_trends` to batch-compute trend metrics.
    """
    now = datetime.now(timezone.utc)
    entity_ids = [
        r[0] for r in session.execute(select(MarketEntity.id)).all()
    ]
    result: dict[int, list[int]] = {eid: [] for eid in entity_ids}

    for i in range(num_buckets):
        end = now - timedelta(days=i * bucket_days)
        start = end - timedelta(days=bucket_days)

        rows = session.execute(
            select(
                MarketEvidence.entity_id,
                func.count(MarketEvidence.id),
            )
            .where(
                MarketEvidence.observed_at >= start,
                MarketEvidence.observed_at < end,
            )
            .group_by(MarketEvidence.entity_id)
        ).all()

        counts = {r[0]: r[1] for r in rows}
        for eid in entity_ids:
            result[eid].append(counts.get(eid, 0))

    return result


def get_latest_snapshots(
    session: Session,
    limit: int = 50,
) -> Sequence[MarketSnapshot]:
    """Most recent snapshot rows ordered by frequency desc."""
    latest = session.execute(
        select(func.max(MarketSnapshot.bucket_start))
    ).scalar()
    if latest is None:
        return []
    return session.execute(
        select(MarketSnapshot)
        .where(MarketSnapshot.bucket_start == latest)
        .where(MarketSnapshot.entity_id.isnot(None))
        .order_by(MarketSnapshot.frequency.desc())
        .limit(limit)
    ).scalars().all()
