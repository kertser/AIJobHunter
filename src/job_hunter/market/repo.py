"""Market repo — CRUD helpers for all market tables."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_hunter.market.db_models import (
    CandidateCapability,
    EdgeType,
    ExtractionStatus,
    MarketEdge,
    MarketEntity,
    MarketEvent,
    MarketEvidence,
    MarketExtraction,
    MarketSnapshot,
    MatchExplanation,
    SubjectType,
)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def create_event(session: Session, event: MarketEvent) -> MarketEvent:
    """Insert a market event; caller must commit."""
    session.add(event)
    session.flush()
    return event


def get_event_by_job_hash(session: Session, job_hash: str) -> MarketEvent | None:
    return session.execute(
        select(MarketEvent).where(MarketEvent.job_hash == job_hash)
    ).scalar_one_or_none()


def get_all_events(session: Session) -> Sequence[MarketEvent]:
    return session.execute(select(MarketEvent)).scalars().all()


def get_events_without_extraction(
    session: Session, extractor_version: str,
) -> Sequence[MarketEvent]:
    """Return events that have no extraction for *extractor_version*."""
    extracted_ids = (
        select(MarketExtraction.event_id)
        .where(MarketExtraction.extractor_version == extractor_version)
        .scalar_subquery()
    )
    return session.execute(
        select(MarketEvent).where(MarketEvent.id.notin_(extracted_ids))
    ).scalars().all()


# ---------------------------------------------------------------------------
# Extractions
# ---------------------------------------------------------------------------

def create_extraction(
    session: Session, extraction: MarketExtraction,
) -> MarketExtraction:
    session.add(extraction)
    session.flush()
    return extraction


def get_ungraphed_extractions(session: Session) -> Sequence[MarketExtraction]:
    """Return complete extractions that haven't been materialised into the graph."""
    return session.execute(
        select(MarketExtraction).where(
            MarketExtraction.status == ExtractionStatus.COMPLETE,
            MarketExtraction.graphed_at.is_(None),
        )
    ).scalars().all()


def mark_extraction_graphed(session: Session, extraction_id: int) -> None:
    ext = session.get(MarketExtraction, extraction_id)
    if ext is not None:
        ext.graphed_at = datetime.now(timezone.utc)
        session.flush()


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

def get_all_entities(session: Session) -> Sequence[MarketEntity]:
    return session.execute(select(MarketEntity)).scalars().all()


def get_entity_count(session: Session) -> int:
    from sqlalchemy import func
    return session.execute(
        select(func.count()).select_from(MarketEntity)
    ).scalar() or 0


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

def create_evidence(
    session: Session, evidence: MarketEvidence,
) -> MarketEvidence:
    session.add(evidence)
    session.flush()
    return evidence


def get_evidence_for_entity(
    session: Session, entity_id: int,
) -> Sequence[MarketEvidence]:
    return session.execute(
        select(MarketEvidence).where(MarketEvidence.entity_id == entity_id)
    ).scalars().all()


def get_evidence_for_extraction(
    session: Session, extraction_id: int,
) -> Sequence[MarketEvidence]:
    return session.execute(
        select(MarketEvidence).where(
            MarketEvidence.extraction_id == extraction_id,
        )
    ).scalars().all()


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------

def upsert_edge(
    session: Session,
    src_entity_id: int,
    dst_entity_id: int,
    edge_type: EdgeType,
) -> MarketEdge:
    """Create or increment a graph edge."""
    # Canonical ordering for symmetric edges
    if edge_type == EdgeType.CO_OCCURS_WITH and src_entity_id > dst_entity_id:
        src_entity_id, dst_entity_id = dst_entity_id, src_entity_id

    edge = session.execute(
        select(MarketEdge).where(
            MarketEdge.src_entity_id == src_entity_id,
            MarketEdge.dst_entity_id == dst_entity_id,
            MarketEdge.edge_type == edge_type,
        )
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if edge is not None:
        edge.count += 1
        edge.weight = float(edge.count)
        edge.last_seen = now
        session.flush()
        return edge

    edge = MarketEdge(
        src_entity_id=src_entity_id,
        dst_entity_id=dst_entity_id,
        edge_type=edge_type,
        weight=1.0,
        count=1,
        first_seen=now,
        last_seen=now,
    )
    session.add(edge)
    session.flush()
    return edge


def get_all_edges(session: Session) -> Sequence[MarketEdge]:
    return session.execute(select(MarketEdge)).scalars().all()


def get_edge_count(session: Session) -> int:
    from sqlalchemy import func
    return session.execute(
        select(func.count()).select_from(MarketEdge)
    ).scalar() or 0


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

def create_snapshot(
    session: Session, snapshot: MarketSnapshot,
) -> MarketSnapshot:
    session.add(snapshot)
    session.flush()
    return snapshot


# ---------------------------------------------------------------------------
# Candidate capabilities
# ---------------------------------------------------------------------------

def get_capabilities_for_candidate(
    session: Session, candidate_key: str,
) -> Sequence[CandidateCapability]:
    return session.execute(
        select(CandidateCapability).where(
            CandidateCapability.candidate_key == candidate_key,
        )
    ).scalars().all()


def delete_capabilities_for_candidate(
    session: Session, candidate_key: str,
) -> int:
    """Delete all capability rows for a candidate. Returns count deleted."""
    rows = get_capabilities_for_candidate(session, candidate_key)
    for r in rows:
        session.delete(r)
    session.flush()
    return len(rows)


# ---------------------------------------------------------------------------
# Match explanations
# ---------------------------------------------------------------------------

def get_match_explanations(
    session: Session, candidate_key: str,
) -> Sequence[MatchExplanation]:
    return session.execute(
        select(MatchExplanation).where(
            MatchExplanation.candidate_key == candidate_key,
        ).order_by(MatchExplanation.success_score.desc())
    ).scalars().all()


def delete_match_explanations(
    session: Session, candidate_key: str,
) -> int:
    rows = list(get_match_explanations(session, candidate_key))
    for r in rows:
        session.delete(r)
    session.flush()
    return len(rows)


# ---------------------------------------------------------------------------
# Evidence queries
# ---------------------------------------------------------------------------

def get_evidence_for_subject(
    session: Session,
    subject_type: SubjectType,
    subject_key: str,
) -> Sequence[MarketEvidence]:
    return session.execute(
        select(MarketEvidence).where(
            MarketEvidence.subject_type == subject_type,
            MarketEvidence.subject_key == subject_key,
        )
    ).scalars().all()


