"""Candidate capability model — projects user profile into market entities.

Reads skills, programming languages, desired roles, and experience from
``UserProfile`` and creates ``CandidateCapability`` rows linked to
``MarketEntity`` records.  Existing dialogue-derived evidence (with
``subject_type=CANDIDATE``) is incorporated to adjust confidence and
detect contradictions.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from job_hunter.config.models import UserProfile
from job_hunter.market.db_models import (
    CandidateCapability,
    EntityType,
    MarketEntity,
    Polarity,
    SubjectType,
)
from job_hunter.market.normalize import resolve_or_create_entity
from job_hunter.market.repo import (
    delete_capabilities_for_candidate,
    get_capabilities_for_candidate,
    get_evidence_for_subject,
)

logger = logging.getLogger("job_hunter.market.candidate_model")


def build_candidate_capabilities(
    session: Session,
    profile: UserProfile,
    candidate_key: str = "default",
) -> dict[str, Any]:
    """Project a ``UserProfile`` into ``CandidateCapability`` rows.

    The function is **idempotent** — it deletes existing rows for
    *candidate_key* before inserting fresh ones.

    Returns ``{capabilities_created, entities_resolved}``.
    """
    # Delete old rows
    delete_capabilities_for_candidate(session, candidate_key)

    # Gather dialogue evidence for this candidate
    evidence_map = _dialogue_evidence_counts(session, candidate_key)

    # Collect raw signals from the profile
    signals: list[tuple[EntityType, str, float, float]] = []
    # (entity_type, raw_name, base_proficiency, base_recency)

    for skill in profile.skills:
        signals.append((EntityType.SKILL, skill, 0.8, 0.8))

    for lang in profile.programming_languages:
        signals.append((EntityType.SKILL, lang, 0.75, 0.7))

    for role in profile.desired_roles:
        signals.append((EntityType.ROLE, role, 0.5, 0.9))

    # Education can imply skills at lower confidence
    for edu in profile.education:
        signals.append((EntityType.SKILL, edu, 0.4, 0.4))

    entities_resolved = 0
    capabilities_created = 0

    # De-dup by (entity_type, entity_id) keeping highest proficiency
    seen: dict[tuple[str, int], CandidateCapability] = {}

    for etype, raw_name, base_prof, base_rec in signals:
        if not raw_name or not raw_name.strip():
            continue

        entity = resolve_or_create_entity(session, etype, raw_name)
        entities_resolved += 1

        key = (etype.value, entity.id)
        if key in seen:
            # Keep the higher proficiency
            if base_prof > seen[key].proficiency_estimate:
                seen[key].proficiency_estimate = base_prof
            continue

        # Dialogue evidence adjustments
        supporting, contradicting = evidence_map.get(entity.id, (0, 0))
        confidence = _compute_confidence(
            base=0.6,
            experience_years=profile.experience_years,
            supporting=supporting,
            contradicting=contradicting,
        )

        # Transferability — boosted by experience breadth
        transferability = min(0.3 + 0.05 * profile.experience_years, 0.8)

        cap = CandidateCapability(
            candidate_key=candidate_key,
            entity_id=entity.id,
            proficiency_estimate=round(base_prof, 3),
            confidence=round(confidence, 3),
            recency=round(base_rec, 3),
            transferability=round(transferability, 3),
            supporting_evidence_count=supporting,
            contradicting_evidence_count=contradicting,
        )
        session.add(cap)
        seen[key] = cap
        capabilities_created += 1

    session.flush()
    logger.info(
        "Built %d candidate capabilities for '%s' (%d entities resolved)",
        capabilities_created, candidate_key, entities_resolved,
    )
    return {
        "capabilities_created": capabilities_created,
        "entities_resolved": entities_resolved,
    }


def get_candidate_capabilities(
    session: Session,
    candidate_key: str = "default",
) -> list[dict[str, Any]]:
    """Return candidate capabilities enriched with entity display info."""
    caps = get_capabilities_for_candidate(session, candidate_key)
    result: list[dict[str, Any]] = []
    for cap in caps:
        entity = session.get(MarketEntity, cap.entity_id)
        result.append({
            "entity_id": cap.entity_id,
            "display_name": entity.display_name if entity else "?",
            "entity_type": entity.entity_type.value if entity else "?",
            "proficiency_estimate": cap.proficiency_estimate,
            "confidence": cap.confidence,
            "recency": cap.recency,
            "transferability": cap.transferability,
            "supporting_evidence_count": cap.supporting_evidence_count,
            "contradicting_evidence_count": cap.contradicting_evidence_count,
        })
    return sorted(result, key=lambda x: x["proficiency_estimate"], reverse=True)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _dialogue_evidence_counts(
    session: Session,
    candidate_key: str,
) -> dict[int, tuple[int, int]]:
    """Count (supporting, contradicting) evidence per entity for a candidate.

    Returns ``{entity_id: (positive_count, negative_count)}``.
    """
    evidence = get_evidence_for_subject(
        session, SubjectType.CANDIDATE, candidate_key,
    )
    counts: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for ev in evidence:
        if ev.polarity == Polarity.POSITIVE or ev.polarity == Polarity.NEUTRAL:
            counts[ev.entity_id][0] += 1
        elif ev.polarity == Polarity.NEGATIVE:
            counts[ev.entity_id][1] += 1
    return {eid: (c[0], c[1]) for eid, c in counts.items()}


def _compute_confidence(
    base: float,
    experience_years: int,
    supporting: int,
    contradicting: int,
) -> float:
    """Contradiction-aware confidence scoring.

    Formula: ``base × experience_boost × (1 - contradiction_ratio)``

    Floors at 0.1 to avoid zero-confidence capabilities.
    """
    # Experience boost: gentle log curve
    experience_boost = min(1.0 + 0.03 * experience_years, 1.3)

    # Contradiction reduction
    total = supporting + contradicting
    if total > 0:
        contradiction_ratio = contradicting / (total + 1)
    else:
        contradiction_ratio = 0.0

    confidence = base * experience_boost * (1.0 - contradiction_ratio)

    # Evidence count boost (more evidence → more confident)
    if supporting > 0:
        confidence = min(confidence + 0.05 * supporting, 0.95)

    return max(round(confidence, 3), 0.1)

