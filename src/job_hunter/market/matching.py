"""Probabilistic matching — candidate capabilities vs role requirements.

Compares a candidate's capability profile against role archetypes,
classifies gaps, computes confidence-aware match scores, and persists
``MatchExplanation`` rows.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_hunter.market.db_models import (
    CandidateCapability,
    MarketEntity,
    MatchExplanation,
    RoleRequirement,
)
from job_hunter.market.repo import (
    delete_match_explanations,
    get_capabilities_for_candidate,
    get_match_explanations,
)

logger = logging.getLogger("job_hunter.market.matching")

# Thresholds for gap classification
_HARD_GAP_IMPORTANCE = 0.7   # requirement importance above this → hard gap
_SOFT_GAP_IMPORTANCE = 0.3   # importance between this and hard → soft gap
_COVERAGE_THRESHOLD = 0.3    # proficiency below this → gap


def match_candidate_to_roles(
    session: Session,
    candidate_key: str = "default",
    role_keys: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Match a candidate against role archetypes.

    If *role_keys* is ``None``, matches against all known roles.
    Deletes existing ``MatchExplanation`` rows for the candidate before
    inserting (idempotent).

    Returns a list of match dicts sorted by ``success_score`` desc.
    """
    # Load candidate capabilities
    caps = get_capabilities_for_candidate(session, candidate_key)
    if not caps:
        logger.warning("No capabilities found for candidate '%s'", candidate_key)
        return []

    cap_by_entity: dict[int, CandidateCapability] = {c.entity_id: c for c in caps}

    # Load graph for proximity boost
    proximity_map = _build_proximity_map(session, cap_by_entity)

    # Load trend momentum for boost
    momentum_map = _build_momentum_map(session)

    # Determine roles to match against
    if role_keys is None:
        role_keys = [
            r[0] for r in session.execute(
                select(RoleRequirement.role_key).distinct()
            ).all()
        ]

    # Delete old explanations
    delete_match_explanations(session, candidate_key)

    results: list[dict[str, Any]] = []

    for role_key in role_keys:
        reqs = session.execute(
            select(RoleRequirement).where(
                RoleRequirement.role_key == role_key,
            ).order_by(RoleRequirement.importance.desc())
        ).scalars().all()

        if not reqs:
            continue

        match = _compute_match(
            session, candidate_key, role_key, caps, cap_by_entity,
            reqs, proximity_map, momentum_map,
        )
        results.append(match)

    # Sort by success_score desc
    results.sort(key=lambda m: m["success_score"], reverse=True)

    session.flush()
    logger.info(
        "Matched candidate '%s' against %d role(s)",
        candidate_key, len(results),
    )
    return results


def _compute_match(
    session: Session,
    candidate_key: str,
    role_key: str,
    caps: list[CandidateCapability],
    cap_by_entity: dict[int, CandidateCapability],
    reqs: list[RoleRequirement],
    proximity_map: dict[int, float],
    momentum_map: dict[int, float],
) -> dict[str, Any]:
    """Score one candidate-role pair."""
    hard_gaps: list[str] = []
    soft_gaps: list[str] = []
    learnable_gaps: list[str] = []
    covered_weight = 0.0
    total_weight = 0.0
    min_confidence = 1.0
    learning_upside = 0.0
    mismatch_risk = 0.0

    for req in reqs:
        entity = session.get(MarketEntity, req.entity_id)
        entity_name = entity.display_name if entity else f"entity_{req.entity_id}"
        importance = req.importance
        total_weight += importance

        cap = cap_by_entity.get(req.entity_id)

        if cap is not None:
            # Candidate has this capability
            effective = cap.proficiency_estimate * cap.transferability
            coverage = min(effective / max(importance, 0.01), 1.0)
            covered_weight += importance * coverage
            min_confidence = min(min_confidence, cap.confidence)

            # Contradiction penalty
            if cap.contradicting_evidence_count > 0:
                mismatch_risk += importance * 0.1 * cap.contradicting_evidence_count

            if coverage < _COVERAGE_THRESHOLD and importance >= _SOFT_GAP_IMPORTANCE:
                soft_gaps.append(entity_name)
                if req.learnability >= 0.5:
                    learning_upside += importance * req.learnability * (1 - coverage)
        else:
            # No direct capability — check graph proximity
            prox = proximity_map.get(req.entity_id, 0.0)
            if prox > 0:
                covered_weight += importance * prox * 0.5  # partial credit
                if req.learnability >= 0.5:
                    learnable_gaps.append(entity_name)
                    learning_upside += importance * req.learnability
                else:
                    soft_gaps.append(entity_name)
            elif importance >= _HARD_GAP_IMPORTANCE:
                hard_gaps.append(entity_name)
                mismatch_risk += importance * 0.3
            elif importance >= _SOFT_GAP_IMPORTANCE:
                soft_gaps.append(entity_name)
                if req.learnability >= 0.5:
                    learnable_gaps.append(entity_name)
                    learning_upside += importance * req.learnability
            else:
                learnable_gaps.append(entity_name)
                learning_upside += importance * max(req.learnability, 0.5)

        # Trend momentum boost
        momentum = momentum_map.get(req.entity_id, 0.0)
        if momentum > 0 and cap is not None:
            covered_weight += importance * momentum * 0.1

    # Compute final scores
    if total_weight > 0:
        success_score = covered_weight / total_weight
    else:
        success_score = 0.0

    # Clamp
    success_score = max(0.0, min(success_score, 1.0))

    # Confidence: minimum of candidate confidence and role confidence
    role_confidence = min((r.confidence for r in reqs), default=0.5)
    confidence = min(min_confidence, role_confidence)
    if hard_gaps:
        confidence *= max(0.5, 1.0 - 0.1 * len(hard_gaps))

    # Normalise mismatch risk
    if total_weight > 0:
        mismatch_risk = min(mismatch_risk / total_weight, 1.0)

    # Normalise learning upside
    if total_weight > 0:
        learning_upside = min(learning_upside / total_weight, 1.0)

    # Persist
    expl = MatchExplanation(
        candidate_key=candidate_key,
        role_key=role_key,
        success_score=round(success_score, 3),
        confidence=round(confidence, 3),
        learning_upside=round(learning_upside, 3),
        mismatch_risk=round(mismatch_risk, 3),
        hard_gaps=hard_gaps,
        soft_gaps=soft_gaps,
        learnable_gaps=learnable_gaps,
        explanation_payload={
            "covered_weight": round(covered_weight, 3),
            "total_weight": round(total_weight, 3),
            "requirements_count": len(reqs),
        },
    )
    session.add(expl)

    return {
        "role_key": role_key,
        "success_score": round(success_score, 3),
        "confidence": round(confidence, 3),
        "learning_upside": round(learning_upside, 3),
        "mismatch_risk": round(mismatch_risk, 3),
        "hard_gaps": hard_gaps,
        "soft_gaps": soft_gaps,
        "learnable_gaps": learnable_gaps,
    }


def _build_proximity_map(
    session: Session,
    cap_by_entity: dict[int, CandidateCapability],
) -> dict[int, float]:
    """Build entity_id → proximity_boost for entities near candidate skills.

    Uses NetworkX shortest path with max cutoff of 3 hops.
    Proximity boost = 0.3 / distance.
    """
    try:
        import networkx as nx
        from job_hunter.market.graph.metrics import to_networkx

        G = to_networkx(session)
        if G.number_of_nodes() == 0:
            return {}
    except Exception:
        return {}

    candidate_nodes = set(cap_by_entity.keys())
    proximity: dict[int, float] = {}

    for node in G.nodes():
        if node in candidate_nodes:
            continue
        min_dist = None
        for cap_node in candidate_nodes:
            if cap_node not in G:
                continue
            try:
                dist = nx.shortest_path_length(G, cap_node, node)
                if dist <= 3:
                    if min_dist is None or dist < min_dist:
                        min_dist = dist
            except nx.NetworkXNoPath:
                continue

        if min_dist is not None and min_dist > 0:
            proximity[node] = 0.3 / min_dist

    return proximity


def _build_momentum_map(session: Session) -> dict[int, float]:
    """Return entity_id → momentum from latest trend snapshots."""
    from job_hunter.market.trends.queries import get_latest_snapshots

    snapshots = get_latest_snapshots(session, limit=200)
    return {
        s.entity_id: s.momentum
        for s in snapshots
        if s.entity_id is not None
    }

