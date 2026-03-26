"""Build and refresh the heterogeneous evidence graph.

The graph is materialised from completed extractions:
  extraction → entities + evidence records + co-occurrence edges.
"""

from __future__ import annotations

import itertools
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from job_hunter.market.db_models import (
    EdgeType,
    EntityType,
    EvidenceType,
    MarketEvidence,
    MarketExtraction,
    Polarity,
    SubjectType,
)
from job_hunter.market.normalize import resolve_or_create_entity
from job_hunter.market.repo import (
    get_ungraphed_extractions,
    mark_extraction_graphed,
    upsert_edge,
)

logger = logging.getLogger("job_hunter.market.graph.builder")

# Confidence defaults per extraction category.
_CONFIDENCE: dict[str, float] = {
    "explicit_skills": 0.9,
    "inferred_skills": 0.6,
    "tasks": 0.8,
    "problems": 0.7,
    "tools": 0.9,
}

# Mapping from extraction field → (EntityType, EvidenceType).
_FIELD_MAP: dict[str, tuple[EntityType, EvidenceType]] = {
    "explicit_skills": (EntityType.SKILL, EvidenceType.EXPLICIT_MENTION),
    "inferred_skills": (EntityType.SKILL, EvidenceType.INFERRED),
    "tasks": (EntityType.TASK, EvidenceType.EXPLICIT_MENTION),
    "problems": (EntityType.PROBLEM, EvidenceType.EXPLICIT_MENTION),
    "tools": (EntityType.TOOL, EvidenceType.EXPLICIT_MENTION),
}


def build_graph(session: Session) -> dict[str, int]:
    """Materialise entities, evidence, and edges from ungraphed extractions.

    Returns a summary dict with counts of created objects.
    """
    extractions = get_ungraphed_extractions(session)
    if not extractions:
        logger.info("No ungraphed extractions to process")
        return {"extractions": 0, "entities": 0, "evidence": 0, "edges": 0}

    total_entities = 0
    total_evidence = 0
    total_edges = 0

    for ext in extractions:
        entities, evidence, edges = _process_extraction(session, ext)
        total_entities += entities
        total_evidence += evidence
        total_edges += edges
        mark_extraction_graphed(session, ext.id)

    logger.info(
        "Built graph from %d extraction(s): %d entities, %d evidence, %d edges",
        len(extractions), total_entities, total_evidence, total_edges,
    )
    return {
        "extractions": len(extractions),
        "entities": total_entities,
        "evidence": total_evidence,
        "edges": total_edges,
    }


def _process_extraction(
    session: Session, ext: MarketExtraction,
) -> tuple[int, int, int]:
    """Materialise one extraction into entities + evidence + edges.

    Returns (entity_count, evidence_count, edge_count).
    """
    # Resolve the event to get the job_hash for the subject key
    from job_hunter.market.db_models import MarketEvent
    event = session.get(MarketEvent, ext.event_id)
    subject_key = event.job_hash or str(event.id) if event else str(ext.event_id)

    now = datetime.now(timezone.utc)
    entity_ids: list[int] = []
    entity_count = 0
    evidence_count = 0

    for field_name, (etype, evtype) in _FIELD_MAP.items():
        items: list[str] = getattr(ext, field_name, []) or []
        confidence = _CONFIDENCE[field_name]

        for raw_name in items:
            if not raw_name or not raw_name.strip():
                continue

            entity = resolve_or_create_entity(session, etype, raw_name)
            entity_ids.append(entity.id)
            entity_count += 1

            # Create evidence linking subject (job) → entity
            ev = MarketEvidence(
                extraction_id=ext.id,
                subject_type=SubjectType.JOB,
                subject_key=subject_key,
                entity_id=entity.id,
                evidence_type=evtype,
                polarity=Polarity.POSITIVE,
                confidence=confidence,
                source_excerpt=ext.tools.__class__.__name__,  # placeholder
                observed_at=now,
            )
            # Use the raw text excerpt instead of the placeholder
            ev.source_excerpt = (event.raw_text or "")[:200] if event else ""
            session.add(ev)
            evidence_count += 1

    session.flush()

    # Build co-occurrence edges between all entity pairs in this extraction
    unique_ids = sorted(set(entity_ids))
    edge_count = 0
    for a, b in itertools.combinations(unique_ids, 2):
        upsert_edge(session, a, b, EdgeType.CO_OCCURS_WITH)
        edge_count += 1

    return entity_count, evidence_count, edge_count

