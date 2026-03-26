"""Full market intelligence pipeline — shared orchestration logic.

Chains: ingest → extract → graph → trends → role-model → (candidate-model → match).
Used by both ``hunt market run-all`` CLI and the web UI run button.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from job_hunter.config.models import UserProfile
from job_hunter.market.extract import MarketExtractor
from job_hunter.market.title_normalizer import TitleNormalizer

logger = logging.getLogger("job_hunter.market.pipeline")


def run_market_pipeline(
    session: Session,
    *,
    extractor: MarketExtractor,
    profile: UserProfile | None = None,
    candidate_key: str = "default",
    title_normalizer: TitleNormalizer | None = None,
) -> dict[str, Any]:
    """Execute the full market intelligence pipeline sequentially.

    Steps:
      1. Ingest — convert jobs → market events
      2. Extract — run signal extraction on un-extracted events
      3. Graph — normalise entities + build evidence graph
      4. Trends — compute frequency, momentum, novelty, burst
      5. Role model — derive role archetypes from extraction data
      6. Candidate model — project user profile into capabilities (if *profile* given)
      7. Match — match candidate against role archetypes (if *profile* given)

    Each step commits after completion so partial progress is preserved.
    Returns a summary dict with counts from each step.
    """
    summary: dict[str, Any] = {}
    total_steps = 7 if profile is not None else 5

    # --- Step 1: Ingest ---
    logger.info("━━━ Step 1/%d: 📥 Ingest — converting jobs to market events", total_steps)
    from job_hunter.market.events import ingest_jobs

    events_created = ingest_jobs(session)
    session.commit()
    summary["events_created"] = events_created
    logger.info("  ✓ Ingested %d new market event(s)", events_created)

    # --- Step 2: Extract ---
    logger.info("━━━ Step 2/%d: 🔍 Extract — running signal extraction (%s)", total_steps, extractor.version)
    from job_hunter.market.extract import run_extraction

    extractions = run_extraction(session, extractor)
    session.commit()
    summary["extractions"] = extractions
    logger.info("  ✓ Extracted signals from %d event(s)", extractions)

    # --- Step 3: Graph ---
    logger.info("━━━ Step 3/%d: 🕸️ Graph — building entity + evidence graph", total_steps)
    from job_hunter.market.graph.builder import build_graph

    graph_summary = build_graph(session)
    session.commit()
    summary["graph"] = graph_summary
    logger.info(
        "  ✓ Graph: %d entities, %d evidence records, %d edges",
        graph_summary["entities"],
        graph_summary["evidence"],
        graph_summary["edges"],
    )

    # --- Step 4: Trends ---
    logger.info("━━━ Step 4/%d: 📈 Trends — computing frequency, momentum, novelty", total_steps)
    from job_hunter.market.trends.compute import compute_trends

    trends_result = compute_trends(session)
    session.commit()
    summary["trends"] = trends_result
    logger.info(
        "  ✓ Trends for %d entities (%d snapshots created)",
        trends_result["entities"],
        trends_result["snapshots_created"],
    )

    # --- Step 5: Role model ---
    logger.info("━━━ Step 5/%d: 🎭 Role model — deriving role archetypes", total_steps)
    from job_hunter.market.role_model import build_role_archetypes

    role_result = build_role_archetypes(session, title_normalizer=title_normalizer)
    session.commit()
    summary["roles"] = role_result
    logger.info(
        "  ✓ Built %d role archetype(s) with %d requirement(s)",
        role_result["roles_created"],
        role_result["requirements_created"],
    )

    # --- Step 6 & 7: Candidate model + Match (optional) ---
    if profile is not None:
        logger.info("━━━ Step 6/%d: 👤 Candidate model — projecting profile for '%s'", total_steps, candidate_key)
        from job_hunter.market.candidate_model import build_candidate_capabilities

        cap_result = build_candidate_capabilities(
            session, profile, candidate_key=candidate_key,
        )
        session.commit()
        summary["capabilities"] = cap_result
        logger.info(
            "  ✓ Built %d capabilities (%d entities resolved)",
            cap_result["capabilities_created"],
            cap_result["entities_resolved"],
        )

        logger.info("━━━ Step 7/%d: 🎯 Match — comparing candidate vs role archetypes", total_steps)
        from job_hunter.market.matching import match_candidate_to_roles

        matches = match_candidate_to_roles(session, candidate_key=candidate_key)
        session.commit()
        summary["matches"] = len(matches)
        logger.info("  ✓ Matched against %d role(s)", len(matches))
    else:
        logger.info("━━━ Steps 6-7: ⏭️ Skipped (no user profile provided)")
        summary["capabilities"] = None
        summary["matches"] = 0

    logger.info("✅ Market pipeline complete — %d steps finished", total_steps)
    return summary

