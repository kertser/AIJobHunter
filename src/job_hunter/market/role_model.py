"""Role archetype reconstruction from task / problem / skill bundles.

Groups job extractions by normalised title families and computes
recurring requirement patterns.  Results are stored in
``role_requirements``.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_hunter.market.db_models import (
    EntityType,
    MarketEntity,
    MarketEvent,
    MarketExtraction,
    ExtractionStatus,
    RoleRequirement,
)
from job_hunter.market.normalize import canonicalize, resolve_or_create_entity
from job_hunter.market.title_normalizer import TitleNormalizer

logger = logging.getLogger("job_hunter.market.role_model")

# Words stripped when normalising titles to role families (legacy fallback).
_STRIP_WORDS = {
    "senior", "junior", "mid", "lead", "principal", "staff",
    "director", "vp", "head", "chief", "intern", "i", "ii", "iii",
    "iv", "v", "remote", "hybrid", "contract", "freelance",
    "full-time", "part-time",
}


def _normalise_title(title: str) -> str:
    """Collapse a job title into a role-family key (legacy fallback).

    ``"Senior Python Developer (Remote)"`` → ``"python developer"``
    """
    t = title.lower().strip()
    t = re.sub(r"\(.*?\)", "", t)          # remove parentheticals
    t = re.sub(r"[^a-z0-9 /]+", " ", t)   # keep only alphanum + space + slash
    tokens = [w for w in t.split() if w and w not in _STRIP_WORDS]
    return " ".join(tokens).strip() or title.lower().strip()


def build_role_archetypes(
    session: Session,
    min_group_size: int = 2,
    importance_threshold: float = 0.3,
    title_normalizer: TitleNormalizer | None = None,
) -> dict[str, Any]:
    """Derive role archetypes from completed extractions.

    Algorithm:

    1. Group extractions by normalised job title (via ``market_events``).
       When *title_normalizer* is provided it batch-normalises all titles
       first (stripping company names, prefixes, junk suffixes and
       fuzzy-clustering similar titles).  Otherwise falls back to the
       legacy ``_normalise_title()`` heuristic.
    2. For each group with ≥ *min_group_size* members, aggregate entity
       frequencies across skills, tasks, tools, and problems.
    3. An entity's **importance** is the fraction of jobs in the group
       that mention it.
    4. Only entities above *importance_threshold* are stored as
       :class:`RoleRequirement` rows.

    Returns ``{roles_created, requirements_created}``.
    """
    # Fetch all complete extractions joined with their event title + company
    rows = session.execute(
        select(MarketExtraction, MarketEvent.title, MarketEvent.company)
        .join(MarketEvent, MarketEvent.id == MarketExtraction.event_id)
        .where(MarketExtraction.status == ExtractionStatus.COMPLETE)
    ).all()

    # Build title → family mapping
    if title_normalizer is not None:
        raw_titles = [title for _, title, _ in rows]
        raw_companies = [company or "" for _, _, company in rows]
        title_map = title_normalizer.normalize_batch(
            raw_titles, companies=raw_companies,
        )
    else:
        title_map = None

    # Group by normalised title
    groups: dict[str, list[MarketExtraction]] = defaultdict(list)
    for ext, title, _company in rows:
        if title_map is not None:
            family = title_map.get(title, _normalise_title(title))
        else:
            family = _normalise_title(title)
        groups[family].append(ext)

    roles_created = 0
    reqs_created = 0

    for family, extractions in groups.items():
        if len(extractions) < min_group_size:
            continue

        # Create (or retrieve) a ROLE entity for this family
        role_entity = resolve_or_create_entity(
            session, EntityType.ROLE, family,
        )
        role_key = role_entity.canonical_name

        # Clear old requirements for this role (allow re-computation)
        old = session.execute(
            select(RoleRequirement).where(
                RoleRequirement.role_key == role_key,
            )
        ).scalars().all()
        for r in old:
            session.delete(r)
        session.flush()

        # Aggregate entity mentions across the group
        entity_counter: Counter[tuple[str, str]] = Counter()
        # (entity_type_value, canonical_name) → count
        n_jobs = len(extractions)

        for ext in extractions:
            seen: set[tuple[str, str]] = set()
            for field, etype in (
                ("explicit_skills", EntityType.SKILL),
                ("inferred_skills", EntityType.SKILL),
                ("tasks", EntityType.TASK),
                ("problems", EntityType.PROBLEM),
                ("tools", EntityType.TOOL),
            ):
                items: list[str] = getattr(ext, field, []) or []
                for raw in items:
                    if not raw or not raw.strip():
                        continue
                    ent = resolve_or_create_entity(session, etype, raw)
                    key = (etype.value, ent.canonical_name)
                    if key not in seen:
                        entity_counter[key] += 1
                        seen.add(key)

        # Store requirements above the threshold
        for (etype_val, cname), count in entity_counter.items():
            importance = count / n_jobs
            if importance < importance_threshold:
                continue
            etype = EntityType(etype_val)
            ent = resolve_or_create_entity(session, etype, cname)
            session.add(RoleRequirement(
                role_key=role_key,
                entity_id=ent.id,
                importance=round(importance, 3),
                confidence=min(0.5 + 0.1 * n_jobs, 0.95),
                learnability=0.5,  # default — could be refined later
                supporting_evidence_count=count,
            ))
            reqs_created += 1

        roles_created += 1

    session.flush()
    logger.info(
        "Built %d role archetype(s) with %d requirement(s)",
        roles_created, reqs_created,
    )
    return {"roles_created": roles_created, "requirements_created": reqs_created}


def get_role_archetypes(session: Session) -> list[dict[str, Any]]:
    """Return role archetypes with their top requirements.

    Returns a list of ``{role_key, requirements: [{entity, importance, ...}]}``.
    """
    role_keys: list[str] = [
        r[0] for r in session.execute(
            select(RoleRequirement.role_key).distinct()
        ).all()
    ]
    result: list[dict[str, Any]] = []
    for rk in sorted(role_keys):
        reqs = session.execute(
            select(RoleRequirement)
            .where(RoleRequirement.role_key == rk)
            .order_by(RoleRequirement.importance.desc())
        ).scalars().all()
        result.append({
            "role_key": rk,
            "job_count": max((r.supporting_evidence_count for r in reqs), default=0),
            "requirements": [
                {
                    "entity_id": r.entity_id,
                    "importance": r.importance,
                    "confidence": r.confidence,
                    "learnability": r.learnability,
                    "evidence_count": r.supporting_evidence_count,
                }
                for r in reqs
            ],
        })
    return result

