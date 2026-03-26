"""Daily report generation — Markdown + JSON summaries."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from job_hunter.db.models import JobStatus, Score
from job_hunter.db.repo import (
    count_jobs_by_status,
    get_all_jobs,
    get_scores_for_jobs,
    get_top_missing_skills,
)

logger = logging.getLogger("job_hunter.reporting")


def generate_report(
    *,
    session: Session,
    data_dir: Path,
    date: str | None = None,
) -> dict[str, Any]:
    """Generate the daily report for *date* (defaults to today).

    Writes ``reports/YYYY-MM-DD.md`` and ``reports/YYYY-MM-DD.json``
    inside *data_dir*.

    Returns a summary dict.
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    reports_dir = data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    # --- Gather data ---
    status_counts = count_jobs_by_status(session)
    total_jobs = sum(status_counts.values())

    all_jobs = get_all_jobs(session)
    job_hashes = [j.hash for j in all_jobs]
    scores_map = get_scores_for_jobs(session, job_hashes)
    top_missing = get_top_missing_skills(session, limit=10)

    # Build per-job details for the report
    job_details: list[dict[str, Any]] = []
    for job in all_jobs:
        entry: dict[str, Any] = {
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "status": job.status.value,
            "easy_apply": job.easy_apply,
            "url": job.url,
        }
        score: Score | None = scores_map.get(job.hash)
        if score is not None:
            entry["fit_score"] = score.llm_fit_score
            entry["similarity"] = round(score.embedding_similarity, 3)
            entry["decision"] = score.decision.value
            entry["missing_skills"] = score.missing_skills
            entry["risk_flags"] = score.risk_flags
        job_details.append(entry)

    summary: dict[str, Any] = {
        "date": date,
        "total_jobs": total_jobs,
        "status_counts": status_counts,
        "top_missing_skills": [{"skill": s, "count": c} for s, c in top_missing],
        "jobs": job_details,
    }

    # --- Optional market intelligence section ---
    market_section = _collect_market_section(session)
    if market_section is not None:
        summary["market"] = market_section

    # --- Write JSON ---
    json_path = reports_dir / f"{date}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info("JSON report written to %s", json_path)

    # --- Write Markdown ---
    md_path = reports_dir / f"{date}.md"
    md = _render_markdown(summary)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    logger.info("Markdown report written to %s", md_path)

    summary["json_path"] = str(json_path)
    summary["md_path"] = str(md_path)
    return summary


def _render_markdown(summary: dict[str, Any]) -> str:
    """Render the summary dict as a Markdown report."""
    lines: list[str] = []
    lines.append(f"# AI Job Hunter — Daily Report ({summary['date']})")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total jobs tracked:** {summary['total_jobs']}")

    status_counts = summary.get("status_counts", {})
    for status in ["new", "scored", "queued", "applied", "skipped", "review", "blocked", "failed"]:
        count = status_counts.get(status, 0)
        if count > 0:
            lines.append(f"- **{status.capitalize()}:** {count}")

    lines.append("")

    # Top missing skills
    top_skills = summary.get("top_missing_skills", [])
    if top_skills:
        lines.append("## Top Missing Skills")
        lines.append("")
        for item in top_skills:
            lines.append(f"- {item['skill']} ({item['count']})")
        lines.append("")

    # Job table
    jobs = summary.get("jobs", [])
    if jobs:
        lines.append("## Jobs")
        lines.append("")
        lines.append("| Title | Company | Location | Status | Fit | Sim | Easy Apply |")
        lines.append("|---|---|---|---|---|---|---|")
        for j in jobs:
            fit = j.get("fit_score", "—")
            sim = j.get("similarity", "—")
            ea = "✅" if j.get("easy_apply") else "❌"
            lines.append(
                f"| {j['title']} | {j['company']} | {j['location']} "
                f"| {j['status']} | {fit} | {sim} | {ea} |"
            )
        lines.append("")

    # --- Market Intelligence section ---
    market = summary.get("market")
    if market:
        lines.append("## Market Intelligence")
        lines.append("")
        lines.append(f"- **Entities tracked:** {market.get('entity_count', 0)}")
        lines.append(f"- **Graph edges:** {market.get('edge_count', 0)}")
        lines.append(f"- **Events:** {market.get('event_count', 0)}")
        lines.append("")

        top_skills = market.get("top_skills", [])
        if top_skills:
            lines.append("### Top Skills (90 days)")
            lines.append("")
            lines.append("| Skill | Count |")
            lines.append("|-------|-------|")
            for s in top_skills:
                lines.append(f"| {s['display_name']} | {s['count']} |")
            lines.append("")

        rising = market.get("rising_trends", [])
        if rising:
            lines.append("### Rising Trends")
            lines.append("")
            lines.append("| Entity | Type | Frequency | Momentum |")
            lines.append("|--------|------|-----------|----------|")
            for r in rising:
                arrow = "↑" if r["momentum"] > 0 else ("↓" if r["momentum"] < 0 else "→")
                lines.append(
                    f"| {r['display_name']} | {r['entity_type']} | "
                    f"{r['frequency']:.0f} | {arrow}{abs(r['momentum'])} |"
                )
            lines.append("")

        matches = market.get("matches", [])
        if matches:
            lines.append("### Opportunity Matches")
            lines.append("")
            lines.append("| Role | Score | Confidence | Hard Gaps |")
            lines.append("|------|-------|------------|-----------|")
            for m in matches:
                score = f"{m['success_score']:.0%}"
                conf = f"{m['confidence']:.0%}"
                gaps = len(m.get("hard_gaps", []))
                lines.append(f"| {m['role_key']} | {score} | {conf} | {gaps} |")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*Generated by AI Job Hunter*")
    lines.append("")
    return "\n".join(lines)


def _collect_market_section(session: Session) -> dict[str, Any] | None:
    """Collect market intelligence data for the report, or None if empty.

    All market imports are guarded so the function degrades gracefully
    when the market subsystem hasn't been run.
    """
    try:
        from job_hunter.market.repo import (
            get_all_events,
            get_edge_count,
            get_entity_count,
        )
        entity_count = get_entity_count(session)
        if entity_count == 0:
            return None

        from job_hunter.market.db_models import EntityType, MarketEntity
        from job_hunter.market.trends.queries import (
            get_latest_snapshots,
            recent_entity_counts,
        )

        events = get_all_events(session)
        edge_count = get_edge_count(session)

        top_skills = recent_entity_counts(
            session, days=90, entity_types=[EntityType.SKILL], limit=10,
        )

        # Rising trends
        rising: list[dict[str, Any]] = []
        for snap in get_latest_snapshots(session, limit=10):
            if snap.entity_id:
                entity = session.get(MarketEntity, snap.entity_id)
                if entity:
                    rising.append({
                        "display_name": entity.display_name,
                        "entity_type": entity.entity_type.value,
                        "frequency": snap.frequency,
                        "momentum": round(snap.momentum, 2),
                    })

        result: dict[str, Any] = {
            "entity_count": entity_count,
            "edge_count": edge_count,
            "event_count": len(events),
            "top_skills": top_skills,
            "rising_trends": rising,
        }

        # Opportunity matches (if candidate model has been run)
        try:
            from job_hunter.market.repo import get_match_explanations
            expls = get_match_explanations(session, "default")
            if expls:
                result["matches"] = [
                    {
                        "role_key": e.role_key,
                        "success_score": e.success_score,
                        "confidence": e.confidence,
                        "hard_gaps": e.hard_gaps,
                    }
                    for e in expls
                ]
        except Exception:
            pass

        return result
    except Exception:
        return None


