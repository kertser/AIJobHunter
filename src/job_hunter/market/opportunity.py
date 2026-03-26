"""Opportunity scoring — graph proximity × trend boost.

Combines match scores with market trends and graph adjacency to produce
ranked opportunity recommendations and adjacent-role suggestions.
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
    get_capabilities_for_candidate,
    get_match_explanations,
)

logger = logging.getLogger("job_hunter.market.opportunity")


def score_opportunities(
    session: Session,
    candidate_key: str = "default",
) -> list[dict[str, Any]]:
    """Rank role opportunities by combining match score with trend momentum.

    Reads existing ``MatchExplanation`` rows (must be created by
    :func:`matching.match_candidate_to_roles` first) and augments each
    with a trend-weighted ``opportunity_score``.

    Returns a list sorted by ``opportunity_score`` desc.
    """
    explanations = get_match_explanations(session, candidate_key)
    if not explanations:
        return []

    momentum_map = _entity_momentum(session)

    results: list[dict[str, Any]] = []
    for expl in explanations:
        # Base score from matching
        base = expl.success_score

        # Trend boost: average momentum of role's required entities
        role_reqs = session.execute(
            select(RoleRequirement).where(
                RoleRequirement.role_key == expl.role_key,
            )
        ).scalars().all()

        momenta = [
            momentum_map.get(r.entity_id, 0.0) for r in role_reqs
        ]
        avg_momentum = sum(momenta) / len(momenta) if momenta else 0.0
        trend_boost = max(avg_momentum * 0.1, 0.0)  # only positive boost

        opportunity_score = min(base + trend_boost, 1.0)

        results.append({
            "role_key": expl.role_key,
            "success_score": expl.success_score,
            "confidence": expl.confidence,
            "learning_upside": expl.learning_upside,
            "mismatch_risk": expl.mismatch_risk,
            "hard_gaps": expl.hard_gaps,
            "soft_gaps": expl.soft_gaps,
            "learnable_gaps": expl.learnable_gaps,
            "trend_boost": round(trend_boost, 3),
            "opportunity_score": round(opportunity_score, 3),
        })

    results.sort(key=lambda r: r["opportunity_score"], reverse=True)
    return results


def find_adjacent_roles(
    session: Session,
    candidate_key: str = "default",
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """Find roles the candidate doesn't directly match but could reach.

    Uses the NetworkX graph to find ROLE entities within 2 hops of
    the candidate's capabilities.  Returns roles not already matched
    (or matched poorly) with a ``proximity`` score.
    """
    caps = get_capabilities_for_candidate(session, candidate_key)
    if not caps:
        return []

    cap_entity_ids = {c.entity_id for c in caps}

    try:
        import networkx as nx
        from job_hunter.market.graph.metrics import to_networkx

        G = to_networkx(session)
        if G.number_of_nodes() == 0:
            return []
    except Exception:
        return []

    # Find all ROLE entities in the graph
    from job_hunter.market.db_models import EntityType
    role_entities = session.execute(
        select(MarketEntity).where(MarketEntity.entity_type == EntityType.ROLE)
    ).scalars().all()

    # Already matched roles (via MatchExplanation)
    existing = get_match_explanations(session, candidate_key)
    well_matched = {e.role_key for e in existing if e.success_score >= 0.5}

    adjacent: list[dict[str, Any]] = []

    for role_ent in role_entities:
        if role_ent.canonical_name in well_matched:
            continue
        if role_ent.id not in G:
            continue

        # Find minimum distance from any candidate capability to this role
        min_dist = None
        for cap_id in cap_entity_ids:
            if cap_id not in G:
                continue
            try:
                dist = nx.shortest_path_length(G, cap_id, role_ent.id)
                if dist <= 3 and (min_dist is None or dist < min_dist):
                    min_dist = dist
            except nx.NetworkXNoPath:
                continue

        if min_dist is not None and min_dist > 0:
            proximity = round(1.0 / min_dist, 3)
            adjacent.append({
                "role_key": role_ent.canonical_name,
                "display_name": role_ent.display_name,
                "proximity": proximity,
                "hops": min_dist,
            })

    adjacent.sort(key=lambda r: r["proximity"], reverse=True)
    return adjacent[:top_n]


def gap_analysis(
    session: Session,
    candidate_key: str,
    role_key: str,
) -> dict[str, Any]:
    """Detailed gap breakdown for a specific candidate-role pair.

    Returns the match explanation enriched with per-requirement details.
    """
    expl = session.execute(
        select(MatchExplanation).where(
            MatchExplanation.candidate_key == candidate_key,
            MatchExplanation.role_key == role_key,
        )
    ).scalar_one_or_none()

    if expl is None:
        return {"error": "No match found. Run matching first."}

    # Load role requirements with entity info
    reqs = session.execute(
        select(RoleRequirement).where(
            RoleRequirement.role_key == role_key,
        ).order_by(RoleRequirement.importance.desc())
    ).scalars().all()

    caps = get_capabilities_for_candidate(session, candidate_key)
    cap_by_entity = {c.entity_id: c for c in caps}

    requirements: list[dict[str, Any]] = []
    for req in reqs:
        entity = session.get(MarketEntity, req.entity_id)
        cap = cap_by_entity.get(req.entity_id)
        requirements.append({
            "entity": entity.display_name if entity else "?",
            "entity_type": entity.entity_type.value if entity else "?",
            "importance": req.importance,
            "learnability": req.learnability,
            "candidate_proficiency": cap.proficiency_estimate if cap else 0.0,
            "candidate_confidence": cap.confidence if cap else 0.0,
            "status": (
                "covered" if cap and cap.proficiency_estimate >= 0.3
                else "learnable" if req.learnability >= 0.5
                else "gap"
            ),
        })

    return {
        "candidate_key": candidate_key,
        "role_key": role_key,
        "success_score": expl.success_score,
        "confidence": expl.confidence,
        "learning_upside": expl.learning_upside,
        "mismatch_risk": expl.mismatch_risk,
        "hard_gaps": expl.hard_gaps,
        "soft_gaps": expl.soft_gaps,
        "learnable_gaps": expl.learnable_gaps,
        "requirements": requirements,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entity_momentum(session: Session) -> dict[int, float]:
    from job_hunter.market.trends.queries import get_latest_snapshots

    return {
        s.entity_id: s.momentum
        for s in get_latest_snapshots(session, limit=200)
        if s.entity_id is not None
    }

