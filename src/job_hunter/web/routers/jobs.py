"""Jobs router — list, detail, status update."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from job_hunter.db.models import ApplicationAttempt, Job, JobStatus, Score
from job_hunter.db.repo import delete_job, get_scores_for_jobs
from job_hunter.web.deps import get_db

router = APIRouter(tags=["jobs"])


class StatusUpdate(BaseModel):
    status: str


@router.get("/jobs")
async def jobs_page(request: Request, session: Session = Depends(get_db), status: str = ""):
    templates = request.app.state.templates
    query = select(Job).order_by(Job.collected_at.desc())
    if status:
        try:
            query = query.where(Job.status == JobStatus(status))
        except ValueError:
            pass
    jobs = session.execute(query).scalars().all()
    hashes = [j.hash for j in jobs]
    scores_map = get_scores_for_jobs(session, hashes)
    statuses = [s.value for s in JobStatus]
    return templates.TemplateResponse(request, "jobs.html", {
        "jobs": jobs, "scores_map": scores_map,
        "statuses": statuses, "current_status": status,
    })


@router.get("/api/jobs")
async def list_jobs(
    session: Session = Depends(get_db),
    status: str = "",
    company: str = "",
    title: str = "",
    page: int = 1,
    per_page: int = 50,
):
    query = select(Job)
    if status:
        query = query.where(Job.status == JobStatus(status))
    if company:
        query = query.where(Job.company.ilike(f"%{company}%"))
    if title:
        query = query.where(Job.title.ilike(f"%{title}%"))
    query = query.order_by(Job.collected_at.desc())
    query = query.offset((page - 1) * per_page).limit(per_page)
    jobs = session.execute(query).scalars().all()
    hashes = [j.hash for j in jobs]
    scores_map = get_scores_for_jobs(session, hashes)
    result = []
    for j in jobs:
        entry: dict[str, Any] = {
            "external_id": j.external_id, "title": j.title, "company": j.company,
            "location": j.location, "status": j.status.value, "easy_apply": j.easy_apply,
            "url": j.url, "hash": j.hash,
        }
        s = scores_map.get(j.hash)
        if s:
            entry["fit_score"] = s.llm_fit_score
            entry["similarity"] = round(s.embedding_similarity, 3)
            entry["decision"] = s.decision.value
        result.append(entry)
    return {"jobs": result, "page": page, "per_page": per_page}


@router.get("/api/jobs/{job_hash}")
async def get_job(job_hash: str, request: Request, session: Session = Depends(get_db)):
    job = session.execute(select(Job).where(Job.hash == job_hash)).scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")
    scores = session.execute(select(Score).where(Score.job_hash == job_hash)).scalars().all()
    attempts = session.execute(
        select(ApplicationAttempt).where(ApplicationAttempt.job_hash == job_hash)
    ).scalars().all()
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "job_detail.html", {
        "job": job, "scores": scores, "attempts": attempts,
    })


@router.patch("/api/jobs/{job_hash}/status")
async def update_job_status(job_hash: str, body: StatusUpdate, session: Session = Depends(get_db)):
    job = session.execute(select(Job).where(Job.hash == job_hash)).scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")
    try:
        new_status = JobStatus(body.status)
    except ValueError:
        raise HTTPException(400, f"Invalid status: {body.status}")
    job.status = new_status
    session.flush()
    return {"hash": job.hash, "status": job.status.value, "title": job.title}


@router.delete("/api/jobs/{job_hash}")
async def remove_job(job_hash: str, session: Session = Depends(get_db)):
    deleted = delete_job(session, job_hash)
    if not deleted:
        raise HTTPException(404, "Job not found")
    return {"deleted": True, "hash": job_hash}


