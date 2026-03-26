"""Dialogue evidence ingestion — sessions, turns, assessments.

Provides CRUD for the dialogue tables and a pipeline to convert
dialogue-derived signals into market evidence records.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_hunter.market.db_models import (
    AssessmentType,
    DialogueAssessment,
    DialogueSession,
    DialogueTurn,
    EntityType,
    EvidenceType,
    MarketEvidence,
    Polarity,
    SessionType,
    SubjectType,
)
from job_hunter.market.normalize import resolve_or_create_entity

logger = logging.getLogger("job_hunter.market.dialogue")


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------

def create_session(
    session: Session,
    *,
    subject_type: SubjectType,
    subject_key: str,
    session_type: SessionType,
    source: str = "",
) -> DialogueSession:
    """Create a new dialogue session."""
    ds = DialogueSession(
        subject_type=subject_type,
        subject_key=subject_key,
        session_type=session_type,
        source=source,
    )
    session.add(ds)
    session.flush()
    return ds


def end_session(
    session: Session,
    session_id: uuid.UUID,
) -> None:
    """Mark a dialogue session as ended."""
    ds = session.get(DialogueSession, session_id)
    if ds is not None:
        ds.ended_at = datetime.now(timezone.utc)
        session.flush()


def get_session(
    session: Session,
    session_id: uuid.UUID,
) -> DialogueSession | None:
    return session.get(DialogueSession, session_id)


def get_all_sessions(session: Session) -> Sequence[DialogueSession]:
    """Return all dialogue sessions, newest first."""
    return session.execute(
        select(DialogueSession).order_by(DialogueSession.started_at.desc())
    ).scalars().all()


def get_sessions_for_subject(
    session: Session,
    subject_type: SubjectType,
    subject_key: str,
) -> Sequence[DialogueSession]:
    return session.execute(
        select(DialogueSession).where(
            DialogueSession.subject_type == subject_type,
            DialogueSession.subject_key == subject_key,
        )
    ).scalars().all()


# ---------------------------------------------------------------------------
# Turn CRUD
# ---------------------------------------------------------------------------

def add_turn(
    session: Session,
    *,
    session_id: uuid.UUID,
    speaker: str,
    turn_index: int,
    prompt_text: str = "",
    response_text: str = "",
) -> DialogueTurn:
    """Append a turn to a dialogue session."""
    turn = DialogueTurn(
        session_id=session_id,
        speaker=speaker,
        turn_index=turn_index,
        prompt_text=prompt_text,
        response_text=response_text,
    )
    session.add(turn)
    session.flush()
    return turn


def get_turns(
    session: Session,
    session_id: uuid.UUID,
) -> Sequence[DialogueTurn]:
    return session.execute(
        select(DialogueTurn)
        .where(DialogueTurn.session_id == session_id)
        .order_by(DialogueTurn.turn_index)
    ).scalars().all()


# ---------------------------------------------------------------------------
# Assessment CRUD
# ---------------------------------------------------------------------------

def add_assessment(
    session: Session,
    *,
    session_id: uuid.UUID,
    assessment_type: AssessmentType,
    score: float,
    confidence: float = 0.5,
    evidence_span: str = "",
    assessor_version: str = "",
) -> DialogueAssessment:
    da = DialogueAssessment(
        session_id=session_id,
        assessment_type=assessment_type,
        score=score,
        confidence=confidence,
        evidence_span=evidence_span,
        assessor_version=assessor_version,
    )
    session.add(da)
    session.flush()
    return da


def get_assessments(
    session: Session,
    session_id: uuid.UUID,
) -> Sequence[DialogueAssessment]:
    return session.execute(
        select(DialogueAssessment)
        .where(DialogueAssessment.session_id == session_id)
    ).scalars().all()


# ---------------------------------------------------------------------------
# Dialogue → Evidence pipeline
# ---------------------------------------------------------------------------

def ingest_dialogue_evidence(
    session: Session,
    dialogue_session_id: uuid.UUID,
) -> int:
    """Convert dialogue turns and assessments into market evidence records.

    Scans the response text of each turn for entity mentions (skills,
    tools) and creates evidence records of type ``DIALOGUE``.

    Returns the number of evidence records created.
    """
    ds = session.get(DialogueSession, dialogue_session_id)
    if ds is None:
        return 0

    turns = get_turns(session, dialogue_session_id)
    assessments = get_assessments(session, dialogue_session_id)
    created = 0

    # Extract entity mentions from turn response text
    for turn in turns:
        text = (turn.response_text or "").lower()
        if not text.strip():
            continue

        # Simple keyword extraction from response text
        from job_hunter.market.extract import _SKILLS, _TOOLS

        for skill in _SKILLS:
            if skill in text:
                entity = resolve_or_create_entity(
                    session, EntityType.SKILL, skill,
                )
                session.add(MarketEvidence(
                    subject_type=ds.subject_type,
                    subject_key=ds.subject_key,
                    entity_id=entity.id,
                    evidence_type=EvidenceType.DIALOGUE,
                    polarity=Polarity.POSITIVE,
                    confidence=0.7,
                    source_excerpt=turn.response_text[:200],
                ))
                created += 1

        for tool in _TOOLS:
            if tool in text:
                entity = resolve_or_create_entity(
                    session, EntityType.TOOL, tool,
                )
                session.add(MarketEvidence(
                    subject_type=ds.subject_type,
                    subject_key=ds.subject_key,
                    entity_id=entity.id,
                    evidence_type=EvidenceType.DIALOGUE,
                    polarity=Polarity.POSITIVE,
                    confidence=0.7,
                    source_excerpt=turn.response_text[:200],
                ))
                created += 1

    # Convert assessments into evidence for meta-skills
    for asmt in assessments:
        entity = resolve_or_create_entity(
            session, EntityType.SKILL, asmt.assessment_type.value.replace("_", " "),
        )
        polarity = Polarity.POSITIVE if asmt.score >= 0.5 else Polarity.NEGATIVE
        session.add(MarketEvidence(
            subject_type=ds.subject_type,
            subject_key=ds.subject_key,
            entity_id=entity.id,
            evidence_type=EvidenceType.DIALOGUE,
            polarity=polarity,
            confidence=asmt.confidence,
            source_excerpt=asmt.evidence_span[:200] if asmt.evidence_span else "",
        ))
        created += 1

    if created:
        session.flush()
    logger.info(
        "Ingested %d evidence record(s) from dialogue session %s",
        created, dialogue_session_id,
    )
    return created

