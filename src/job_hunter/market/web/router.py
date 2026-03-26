"""FastAPI router for Market Intelligence pages and API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from job_hunter.web.deps import get_db

router = APIRouter(tags=["market"])


# ---------------------------------------------------------------------------
# Market overview page
# ---------------------------------------------------------------------------

@router.get("/market")
async def market_page(request: Request, session: Session = Depends(get_db)):
    templates = request.app.state.templates
    data = _build_overview(session)

    # Enrich with role archetypes, candidate capabilities, and matches
    data.update(_build_role_archetypes_data(session))
    data.update(_build_candidate_data(session, request))

    return templates.TemplateResponse(request, "market.html", data)


# ---------------------------------------------------------------------------
# JSON API endpoints
# ---------------------------------------------------------------------------

@router.get("/api/market/overview")
async def market_overview(session: Session = Depends(get_db)):
    return _build_overview(session)


@router.get("/api/market/trends")
async def market_trends(session: Session = Depends(get_db)):
    from job_hunter.market.trends.queries import get_latest_snapshots, recent_entity_counts

    snapshots = get_latest_snapshots(session, limit=30)

    # Enrich snapshots with entity info
    from job_hunter.market.db_models import MarketEntity
    trend_rows = []
    for snap in snapshots:
        entity = session.get(MarketEntity, snap.entity_id) if snap.entity_id else None
        trend_rows.append({
            "entity_id": snap.entity_id,
            "display_name": entity.display_name if entity else "—",
            "entity_type": entity.entity_type.value if entity else "—",
            "frequency": snap.frequency,
            "momentum": round(snap.momentum, 2),
            "novelty": snap.novelty,
            "burst": round(snap.burst, 2),
        })

    top_skills = recent_entity_counts(session, days=30, limit=20)

    return {"trends": trend_rows, "top_entities": top_skills}


@router.get("/api/market/entities")
async def market_entities(session: Session = Depends(get_db)):
    from job_hunter.market.trends.queries import recent_entity_counts
    from job_hunter.market.db_models import EntityType

    result = {}
    for etype in EntityType:
        result[etype.value] = recent_entity_counts(
            session, days=365, entity_types=[etype], limit=30,
        )
    return result


@router.get("/api/market/roles")
async def market_roles(session: Session = Depends(get_db)):
    from job_hunter.market.role_model import get_role_archetypes
    from job_hunter.market.db_models import MarketEntity

    archetypes = get_role_archetypes(session)
    # Enrich with entity display names
    for role in archetypes:
        for req in role["requirements"]:
            entity = session.get(MarketEntity, req["entity_id"])
            req["display_name"] = entity.display_name if entity else "?"
            req["entity_type"] = entity.entity_type.value if entity else "?"
    return {"roles": archetypes}


@router.get("/api/market/companies/{company}")
async def market_company(company: str, session: Session = Depends(get_db)):
    """Demand patterns for a specific company."""
    from sqlalchemy import func, select
    from job_hunter.market.db_models import (
        EntityType, MarketEntity, MarketEvent, MarketExtraction,
        MarketEvidence, ExtractionStatus,
    )

    # Find events for this company
    events = session.execute(
        select(MarketEvent).where(
            func.lower(MarketEvent.company) == company.lower(),
        )
    ).scalars().all()

    if not events:
        return {"company": company, "event_count": 0, "top_entities": [], "roles": []}

    event_ids = [e.id for e in events]

    # Get extractions for these events
    extractions = session.execute(
        select(MarketExtraction).where(
            MarketExtraction.event_id.in_(event_ids),
            MarketExtraction.status == ExtractionStatus.COMPLETE,
        )
    ).scalars().all()

    extraction_ids = [ext.id for ext in extractions]

    # Top entities from evidence linked to these extractions
    top_entities: list[dict] = []
    if extraction_ids:
        rows = session.execute(
            select(
                MarketEntity.id,
                MarketEntity.canonical_name,
                MarketEntity.display_name,
                MarketEntity.entity_type,
                func.count(MarketEvidence.id).label("cnt"),
            )
            .join(MarketEvidence, MarketEvidence.entity_id == MarketEntity.id)
            .where(MarketEvidence.extraction_id.in_(extraction_ids))
            .group_by(MarketEntity.id)
            .order_by(func.count(MarketEvidence.id).desc())
            .limit(20)
        ).all()
        top_entities = [
            {
                "entity_id": r[0],
                "canonical_name": r[1],
                "display_name": r[2],
                "entity_type": r[3].value if hasattr(r[3], "value") else r[3],
                "count": r[4],
            }
            for r in rows
        ]

    # Roles (titles) this company hires for
    roles = list({e.title for e in events if e.title})

    return {
        "company": company,
        "event_count": len(events),
        "top_entities": top_entities,
        "roles": sorted(roles),
    }


@router.get("/api/market/candidate/{profile}")
async def market_candidate(
    profile: str,
    request: Request,
    session: Session = Depends(get_db),
):
    """Build and return candidate capability model for a profile."""
    from job_hunter.config.loader import load_user_profile
    from job_hunter.market.candidate_model import (
        build_candidate_capabilities,
        get_candidate_capabilities,
    )

    settings = request.app.state.settings
    profile_path = settings.data_dir / "user_profile.yml"
    if not profile_path.exists():
        return JSONResponse(
            {"error": "No user_profile.yml found. Run profile generation first."},
            status_code=404,
        )

    user_profile = load_user_profile(profile_path)
    build_candidate_capabilities(session, user_profile, candidate_key=profile)

    capabilities = get_candidate_capabilities(session, candidate_key=profile)
    return {"candidate_key": profile, "capabilities": capabilities}


@router.get("/api/market/match/{profile}")
async def market_match(
    profile: str,
    request: Request,
    session: Session = Depends(get_db),
):
    """Match candidate against role archetypes and return opportunities."""
    from job_hunter.config.loader import load_user_profile
    from job_hunter.market.candidate_model import build_candidate_capabilities
    from job_hunter.market.matching import match_candidate_to_roles
    from job_hunter.market.opportunity import (
        find_adjacent_roles,
        score_opportunities,
    )

    settings = request.app.state.settings
    profile_path = settings.data_dir / "user_profile.yml"
    if not profile_path.exists():
        return JSONResponse(
            {"error": "No user_profile.yml found."},
            status_code=404,
        )

    user_profile = load_user_profile(profile_path)
    build_candidate_capabilities(session, user_profile, candidate_key=profile)

    matches = match_candidate_to_roles(session, candidate_key=profile)
    opportunities = score_opportunities(session, candidate_key=profile)
    adjacent = find_adjacent_roles(session, candidate_key=profile, top_n=5)

    return {
        "candidate_key": profile,
        "matches": matches,
        "opportunities": opportunities,
        "adjacent_roles": adjacent,
    }


@router.get("/api/market/gap/{profile}/{role}")
async def market_gap_analysis(
    profile: str,
    role: str,
    request: Request,
    session: Session = Depends(get_db),
):
    """Detailed gap analysis for a candidate-role pair."""
    from job_hunter.market.opportunity import gap_analysis

    result = gap_analysis(session, candidate_key=profile, role_key=role)
    if "error" in result:
        return JSONResponse(result, status_code=404)
    return result


@router.get("/api/market/dialogue/sessions")
async def dialogue_sessions_list(session: Session = Depends(get_db)):
    """List all dialogue sessions."""
    from job_hunter.market.dialogue import get_all_sessions

    sessions = get_all_sessions(session)
    return {
        "sessions": [
            {
                "id": str(ds.id),
                "subject_type": ds.subject_type.value,
                "subject_key": ds.subject_key,
                "session_type": ds.session_type.value,
                "source": ds.source,
                "started_at": ds.started_at.isoformat() if ds.started_at else None,
                "ended_at": ds.ended_at.isoformat() if ds.ended_at else None,
            }
            for ds in sessions
        ],
    }


@router.get("/api/market/dialogue/sessions/{session_id}")
async def dialogue_session_detail(
    session_id: str,
    session: Session = Depends(get_db),
):
    """Retrieve a single dialogue session with turns and assessments."""
    import uuid as _uuid
    from job_hunter.market.dialogue import get_session, get_turns, get_assessments

    try:
        sid = _uuid.UUID(session_id)
    except ValueError:
        return JSONResponse({"error": "Invalid session ID"}, status_code=400)

    ds = get_session(session, sid)
    if ds is None:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    turns = get_turns(session, sid)
    assessments = get_assessments(session, sid)

    return {
        "id": str(ds.id),
        "subject_type": ds.subject_type.value,
        "subject_key": ds.subject_key,
        "session_type": ds.session_type.value,
        "source": ds.source,
        "started_at": ds.started_at.isoformat() if ds.started_at else None,
        "ended_at": ds.ended_at.isoformat() if ds.ended_at else None,
        "turns": [
            {
                "id": str(t.id),
                "speaker": t.speaker,
                "turn_index": t.turn_index,
                "prompt_text": t.prompt_text,
                "response_text": t.response_text,
                "timestamp": t.timestamp.isoformat() if t.timestamp else None,
            }
            for t in turns
        ],
        "assessments": [
            {
                "id": str(a.id),
                "assessment_type": a.assessment_type.value,
                "score": a.score,
                "confidence": a.confidence,
                "evidence_span": a.evidence_span,
                "assessor_version": a.assessor_version,
            }
            for a in assessments
        ],
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_overview(session: Session) -> dict:
    from job_hunter.market.repo import get_entity_count, get_edge_count, get_all_events
    from job_hunter.market.trends.queries import recent_entity_counts
    from job_hunter.market.db_models import EntityType, MarketEvent, MarketSnapshot
    from sqlalchemy import func, select

    events = get_all_events(session)
    entity_count = get_entity_count(session)
    edge_count = get_edge_count(session)
    snapshot_count = session.execute(
        select(func.count()).select_from(MarketSnapshot)
    ).scalar() or 0

    top_skills = recent_entity_counts(
        session, days=90, entity_types=[EntityType.SKILL], limit=10,
    )
    top_tools = recent_entity_counts(
        session, days=90, entity_types=[EntityType.TOOL], limit=10,
    )

    # Rising trends (highest momentum from latest snapshot)
    from job_hunter.market.trends.queries import get_latest_snapshots
    from job_hunter.market.db_models import MarketEntity
    rising = []
    for snap in get_latest_snapshots(session, limit=10):
        if snap.momentum > 0 and snap.entity_id:
            entity = session.get(MarketEntity, snap.entity_id)
            if entity:
                rising.append({
                    "display_name": entity.display_name,
                    "entity_type": entity.entity_type.value,
                    "frequency": snap.frequency,
                    "momentum": round(snap.momentum, 2),
                })

    # Top companies by event count
    company_rows = session.execute(
        select(
            MarketEvent.company,
            func.count(MarketEvent.id).label("cnt"),
        )
        .where(MarketEvent.company != "")
        .group_by(MarketEvent.company)
        .order_by(func.count(MarketEvent.id).desc())
        .limit(10)
    ).all()
    top_companies = [
        {"name": r[0], "event_count": r[1]}
        for r in company_rows
    ]

    return {
        "event_count": len(events),
        "entity_count": entity_count,
        "edge_count": edge_count,
        "snapshot_count": snapshot_count,
        "top_skills": top_skills,
        "top_tools": top_tools,
        "rising": rising,
        "top_companies": top_companies,
    }


def _build_role_archetypes_data(session: Session) -> dict:
    """Load role archetypes enriched with entity display names."""
    try:
        from job_hunter.market.role_model import get_role_archetypes
        from job_hunter.market.db_models import MarketEntity

        archetypes = get_role_archetypes(session)
        for role in archetypes:
            for req in role["requirements"]:
                entity = session.get(MarketEntity, req["entity_id"])
                req["display_name"] = entity.display_name if entity else "?"
                req["entity_type"] = entity.entity_type.value if entity else "?"
        return {"role_archetypes": archetypes}
    except Exception:
        return {"role_archetypes": []}


def _build_candidate_data(session: Session, request) -> dict:
    """Load candidate capabilities, opportunity matches, and adjacent roles.

    Gracefully returns empty data if no user profile exists or if the
    candidate model hasn't been built yet.
    """
    result: dict = {
        "candidate_capabilities": [],
        "opportunity_matches": [],
        "adjacent_roles": [],
    }
    try:
        from job_hunter.config.loader import load_user_profile
        from job_hunter.market.candidate_model import (
            build_candidate_capabilities,
            get_candidate_capabilities,
        )
        from job_hunter.market.matching import match_candidate_to_roles
        from job_hunter.market.opportunity import (
            find_adjacent_roles,
            score_opportunities,
        )

        settings = request.app.state.settings
        profile_path = settings.data_dir / "user_profile.yml"
        if not profile_path.exists():
            return result

        user_profile = load_user_profile(profile_path)
        candidate_key = "default"

        build_candidate_capabilities(session, user_profile, candidate_key=candidate_key)
        result["candidate_capabilities"] = get_candidate_capabilities(
            session, candidate_key=candidate_key,
        )

        match_candidate_to_roles(session, candidate_key=candidate_key)
        result["opportunity_matches"] = score_opportunities(
            session, candidate_key=candidate_key,
        )
        result["adjacent_roles"] = find_adjacent_roles(
            session, candidate_key=candidate_key, top_n=5,
        )
    except Exception:
        pass  # Degrade gracefully — panels just stay empty

    return result


