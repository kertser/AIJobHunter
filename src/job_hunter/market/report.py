"""Market intelligence report generator — Markdown + JSON artefacts.

Produces a snapshot report of current market state including:
- Overview stats (events, entities, edges)
- Top skills, tools, tasks, problems
- Rising trends (highest momentum entities)
- Role archetypes with requirements
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from job_hunter.market.db_models import (
    EntityType,
    MarketEntity,
    MarketSnapshot,
)
from job_hunter.market.repo import get_all_events, get_edge_count, get_entity_count
from job_hunter.market.role_model import get_role_archetypes
from job_hunter.market.trends.queries import get_latest_snapshots, recent_entity_counts

logger = logging.getLogger("job_hunter.market.report")


def generate_market_report(
    session: Session,
    out_dir: Path,
) -> tuple[Path, Path]:
    """Generate a market intelligence report and write to *out_dir*.

    Returns ``(markdown_path, json_path)``.
    """
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")

    data = _collect_report_data(session)

    # --- Markdown ---
    md = _render_markdown(data, date_str)
    md_path = out_dir / f"market_{date_str}.md"
    md_path.write_text(md, encoding="utf-8")

    # --- JSON ---
    json_path = out_dir / f"market_{date_str}.json"
    json_path.write_text(
        json.dumps(data, indent=2, default=str), encoding="utf-8",
    )

    logger.info("Market report written to %s", out_dir)
    return md_path, json_path


def _collect_report_data(session: Session) -> dict[str, Any]:
    events = get_all_events(session)
    entity_count = get_entity_count(session)
    edge_count = get_edge_count(session)
    snapshot_count = session.execute(
        select(func.count()).select_from(MarketSnapshot)
    ).scalar() or 0

    top_skills = recent_entity_counts(
        session, days=90, entity_types=[EntityType.SKILL], limit=15,
    )
    top_tools = recent_entity_counts(
        session, days=90, entity_types=[EntityType.TOOL], limit=15,
    )
    top_tasks = recent_entity_counts(
        session, days=90, entity_types=[EntityType.TASK], limit=10,
    )
    top_problems = recent_entity_counts(
        session, days=90, entity_types=[EntityType.PROBLEM], limit=10,
    )

    # Rising trends
    rising: list[dict[str, Any]] = []
    for snap in get_latest_snapshots(session, limit=20):
        if snap.entity_id:
            entity = session.get(MarketEntity, snap.entity_id)
            if entity:
                rising.append({
                    "display_name": entity.display_name,
                    "entity_type": entity.entity_type.value,
                    "frequency": snap.frequency,
                    "momentum": round(snap.momentum, 2),
                    "novelty": snap.novelty,
                    "burst": round(snap.burst, 2),
                })

    # Role archetypes
    archetypes = get_role_archetypes(session)
    for role in archetypes:
        for req in role["requirements"]:
            entity = session.get(MarketEntity, req["entity_id"])
            req["display_name"] = entity.display_name if entity else "?"
            req["entity_type"] = entity.entity_type.value if entity else "?"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "event_count": len(events),
        "entity_count": entity_count,
        "edge_count": edge_count,
        "snapshot_count": snapshot_count,
        "top_skills": top_skills,
        "top_tools": top_tools,
        "top_tasks": top_tasks,
        "top_problems": top_problems,
        "rising_trends": rising,
        "role_archetypes": archetypes,
    }


def _render_markdown(data: dict[str, Any], date_str: str) -> str:
    lines: list[str] = []
    lines.append(f"# Market Intelligence Report — {date_str}")
    lines.append("")
    lines.append(f"**Generated:** {data['generated_at']}")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Events | {data['event_count']} |")
    lines.append(f"| Entities | {data['entity_count']} |")
    lines.append(f"| Graph Edges | {data['edge_count']} |")
    lines.append(f"| Trend Snapshots | {data['snapshot_count']} |")
    lines.append("")

    # Top Skills
    if data["top_skills"]:
        lines.append("## Top Skills")
        lines.append("")
        lines.append("| Skill | Count |")
        lines.append("|-------|-------|")
        for s in data["top_skills"]:
            lines.append(f"| {s['display_name']} | {s['count']} |")
        lines.append("")

    # Top Tools
    if data["top_tools"]:
        lines.append("## Top Tools")
        lines.append("")
        lines.append("| Tool | Count |")
        lines.append("|------|-------|")
        for t in data["top_tools"]:
            lines.append(f"| {t['display_name']} | {t['count']} |")
        lines.append("")

    # Top Tasks
    if data["top_tasks"]:
        lines.append("## Top Tasks")
        lines.append("")
        lines.append("| Task | Count |")
        lines.append("|------|-------|")
        for t in data["top_tasks"]:
            lines.append(f"| {t['display_name']} | {t['count']} |")
        lines.append("")

    # Top Problems
    if data["top_problems"]:
        lines.append("## Top Problems")
        lines.append("")
        lines.append("| Problem | Count |")
        lines.append("|---------|-------|")
        for p in data["top_problems"]:
            lines.append(f"| {p['display_name']} | {p['count']} |")
        lines.append("")

    # Rising Trends
    if data["rising_trends"]:
        lines.append("## Rising Trends")
        lines.append("")
        lines.append("| Entity | Type | Frequency | Momentum | Burst |")
        lines.append("|--------|------|-----------|----------|-------|")
        for r in data["rising_trends"]:
            arrow = "↑" if r["momentum"] > 0 else ("↓" if r["momentum"] < 0 else "→")
            lines.append(
                f"| {r['display_name']} | {r['entity_type']} | "
                f"{r['frequency']:.0f} | {arrow}{abs(r['momentum'])} | "
                f"{r['burst']} |"
            )
        lines.append("")

    # Role Archetypes
    if data["role_archetypes"]:
        lines.append("## Role Archetypes")
        lines.append("")
        for role in data["role_archetypes"]:
            lines.append(f"### {role['role_key']}")
            lines.append("")
            if role["requirements"]:
                lines.append("| Requirement | Type | Importance | Confidence |")
                lines.append("|-------------|------|------------|------------|")
                for req in role["requirements"]:
                    lines.append(
                        f"| {req.get('display_name', '?')} | "
                        f"{req.get('entity_type', '?')} | "
                        f"{req['importance']:.1%} | "
                        f"{req['confidence']:.1%} |"
                    )
            lines.append("")

    return "\n".join(lines)

