"""Dashboard router — summary stats and overview."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from job_hunter.db.models import ApplicationAttempt, Job, Score
from job_hunter.db.repo import count_applied_today, count_jobs_by_status, get_top_missing_skills
from job_hunter.web.deps import get_db, get_user_data_dir

router = APIRouter(tags=["dashboard"])


@router.get("/")
async def dashboard_page(request: Request, session: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    user_id = user.id if user else None
    data_dir = get_user_data_dir(request)
    # Redirect to onboarding if no profile exists yet
    if not (data_dir / "user_profile.yml").exists():
        return RedirectResponse(url="/onboarding", status_code=302)
    templates = request.app.state.templates
    stats = _build_stats(session, user_id=user_id)
    return templates.TemplateResponse(request, "dashboard.html", {**stats})


@router.get("/api/stats/dashboard")
async def dashboard_stats(request: Request, session: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    user_id = user.id if user else None
    return _build_stats(session, user_id=user_id)


def _build_stats(session: Session, *, user_id: uuid.UUID | None = None) -> dict:
    status_counts = count_jobs_by_status(session, user_id=user_id)
    total = sum(status_counts.values())
    applied_today = count_applied_today(session, user_id=user_id)
    top_skills = get_top_missing_skills(session, limit=10, user_id=user_id)

    # ── Fit-score histogram (buckets: 0-19, 20-39, 40-59, 60-79, 80-100) ──
    score_query = select(Score.llm_fit_score)
    if user_id is not None:
        score_query = score_query.where(Score.user_id == user_id)
    scores = session.execute(score_query).scalars().all()
    fit_buckets = [0, 0, 0, 0, 0]  # 0-19, 20-39, 40-59, 60-79, 80-100
    for s in scores:
        idx = min(s // 20, 4)
        fit_buckets[idx] += 1
    avg_fit = round(sum(scores) / len(scores)) if scores else 0

    # ── Easy-apply ratio ──
    easy_query = select(func.count()).select_from(Job).where(Job.easy_apply.is_(True))
    if user_id is not None:
        easy_query = easy_query.where(Job.user_id == user_id)
    easy_apply_count = session.execute(easy_query).scalar() or 0

    # ── Recent activity (last 8 jobs, newest first) ──
    recent_query = (
        select(Job.title, Job.company, Job.status, Job.collected_at, Job.hash)
        .order_by(Job.collected_at.desc())
        .limit(8)
    )
    if user_id is not None:
        recent_query = recent_query.where(Job.user_id == user_id)
    recent_jobs = session.execute(recent_query).all()

    # ── Application success rate ──
    attempt_query = select(func.count()).select_from(ApplicationAttempt)
    if user_id is not None:
        attempt_query = attempt_query.where(ApplicationAttempt.user_id == user_id)
    total_attempts = session.execute(attempt_query).scalar() or 0

    success_query = (
        select(func.count()).select_from(ApplicationAttempt)
        .where(ApplicationAttempt.result == "success")
    )
    if user_id is not None:
        success_query = success_query.where(ApplicationAttempt.user_id == user_id)
    success_attempts = session.execute(success_query).scalar() or 0

    # ── Market intelligence summary (safe if tables don't exist) ──
    market_summary: dict = {}
    try:
        from job_hunter.market.opportunity import score_opportunities
        from job_hunter.market.trends.queries import get_latest_snapshots
        from job_hunter.market.repo import count_entities, count_edges

        entity_count = count_entities(session)
        edge_count = count_edges(session)

        if entity_count > 0:
            snapshots = get_latest_snapshots(session, limit=10)
            rising = [
                {"name": s.entity.display_name if s.entity else "?", "momentum": round(s.momentum, 2)}
                for s in snapshots if s.momentum > 0
            ][:5]

            opps = score_opportunities(session, "default")
            top_opps = [
                {
                    "role": o["role_key"].replace("_", " ").title(),
                    "score": round(o["opportunity_score"] * 100),
                    "gaps": len(o.get("hard_gaps", [])),
                }
                for o in opps[:3]
            ]

            market_summary = {
                "entities": entity_count,
                "edges": edge_count,
                "rising": rising,
                "opportunities": top_opps,
            }
    except Exception:
        pass

    return {
        "total_jobs": total,
        "status_counts": status_counts,
        "applied_today": applied_today,
        "top_missing_skills": [{"skill": s, "count": c} for s, c in top_skills],
        "fit_buckets": fit_buckets,
        "fit_bucket_labels": ["0-19", "20-39", "40-59", "60-79", "80-100"],
        "avg_fit": avg_fit,
        "scored_count": len(scores),
        "easy_apply_count": easy_apply_count,
        "recent_jobs": [
            {
                "title": r.title,
                "company": r.company,
                "status": r.status.value if r.status else "new",
                "collected_at": r.collected_at.strftime("%b %d, %H:%M") if r.collected_at else "",
                "hash": r.hash,
            }
            for r in recent_jobs
        ],
        "total_attempts": total_attempts,
        "success_attempts": success_attempts,
        "market": market_summary,
    }


