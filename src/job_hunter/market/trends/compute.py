"""Trend snapshot computation — frequency, momentum, novelty, burst.

Computes temporal demand metrics for each entity and persists them
into ``market_snapshots``.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from job_hunter.market.db_models import MarketSnapshot
from job_hunter.market.trends.queries import all_entity_bucket_counts

logger = logging.getLogger("job_hunter.market.trends.compute")


def compute_trends(
    session: Session,
    bucket_days: int = 7,
    num_buckets: int = 4,
) -> dict[str, Any]:
    """Compute and persist trend snapshots for all entities.

    Metrics per entity:

    * **frequency** — evidence count in the most recent bucket.
    * **momentum** — ``(current - previous) / max(previous, 1)``.
    * **novelty** — ``1.0`` if evidence exists *only* in the current
      bucket (entity just appeared); ``0.0`` otherwise.
    * **burst** — z-score of current frequency vs historical mean/std.

    Returns ``{entities, snapshots_created}``.
    """
    counts_map = all_entity_bucket_counts(
        session, bucket_days=bucket_days, num_buckets=num_buckets,
    )
    if not counts_map:
        logger.info("No entities to compute trends for")
        return {"entities": 0, "snapshots_created": 0}

    now = datetime.now(timezone.utc)
    bucket_start = now - timedelta(days=bucket_days)
    created = 0

    for entity_id, buckets in counts_map.items():
        current = buckets[0] if buckets else 0
        previous = buckets[1] if len(buckets) > 1 else 0

        frequency = float(current)
        momentum = (current - previous) / max(previous, 1)

        has_history = any(b > 0 for b in buckets[1:]) if len(buckets) > 1 else False
        novelty = 1.0 if current > 0 and not has_history else 0.0

        if len(buckets) >= 2:
            hist = buckets[1:]
            mean = sum(hist) / len(hist)
            variance = sum((x - mean) ** 2 for x in hist) / len(hist)
            std = math.sqrt(variance) if variance > 0 else 0.0
            burst = (current - mean) / max(std, 1.0)
        else:
            burst = 0.0

        session.add(MarketSnapshot(
            bucket_start=bucket_start,
            entity_id=entity_id,
            edge_id=None,
            frequency=frequency,
            momentum=momentum,
            novelty=novelty,
            burst=burst,
        ))
        created += 1

    session.flush()
    logger.info(
        "Computed trends for %d entities (%d snapshots)",
        len(counts_map), created,
    )
    return {"entities": len(counts_map), "snapshots_created": created}
