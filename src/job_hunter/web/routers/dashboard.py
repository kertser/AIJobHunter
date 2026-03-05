"""Dashboard router — summary stats and overview."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from job_hunter.db.repo import count_applied_today, count_jobs_by_status, get_top_missing_skills
from job_hunter.web.deps import get_db

router = APIRouter(tags=["dashboard"])


@router.get("/")
async def dashboard_page(request: Request, session: Session = Depends(get_db)):
    templates = request.app.state.templates
    stats = _build_stats(session)
    return templates.TemplateResponse(request, "dashboard.html", {**stats})


@router.get("/api/stats/dashboard")
async def dashboard_stats(session: Session = Depends(get_db)):
    return _build_stats(session)


def _build_stats(session: Session) -> dict:
    status_counts = count_jobs_by_status(session)
    total = sum(status_counts.values())
    applied_today = count_applied_today(session)
    top_skills = get_top_missing_skills(session, limit=10)
    return {
        "total_jobs": total,
        "status_counts": status_counts,
        "applied_today": applied_today,
        "top_missing_skills": [{"skill": s, "count": c} for s, c in top_skills],
    }

