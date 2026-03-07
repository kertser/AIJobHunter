"""Jobs router — list, detail, status update."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from job_hunter.db.models import ApplicationAttempt, ApplicationResult, Job, JobStatus, Score
from job_hunter.db.repo import delete_job, get_scores_for_jobs
from job_hunter.web.deps import get_db

router = APIRouter(tags=["jobs"])


class StatusUpdate(BaseModel):
    status: str


def _get_applied_map(session: Session, hashes: list[str]) -> dict:
    """Build a map of job_hash → earliest applied_at datetime."""
    if not hashes:
        return {}
    attempts = session.execute(
        select(ApplicationAttempt)
        .where(ApplicationAttempt.job_hash.in_(hashes))
        .where(ApplicationAttempt.result.in_([
            ApplicationResult.SUCCESS, ApplicationResult.DRY_RUN,
        ]))
    ).scalars().all()
    result = {}
    for a in attempts:
        if a.job_hash not in result or (a.started_at and a.started_at < result[a.job_hash]):
            result[a.job_hash] = a.started_at
    return result


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
    applied_map = _get_applied_map(session, hashes)
    # Show only statuses that are actually used in the pipeline
    statuses = [s.value for s in JobStatus if s != JobStatus.SCORED]
    return templates.TemplateResponse(request, "jobs.html", {
        "jobs": jobs, "scores_map": scores_map, "applied_map": applied_map,
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
    # Get applied_at date from successful attempts
    applied_map = _get_applied_map(session, [job_hash])
    applied_at = applied_map.get(job_hash)
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "job_detail.html", {
        "job": job, "scores": scores, "attempts": attempts,
        "applied_at": applied_at,
    })


# --- Bulk endpoints (must be before /{job_hash} routes) ---

class BulkStatusUpdate(BaseModel):
    hashes: list[str]
    status: str


@router.patch("/api/jobs/bulk/status")
async def bulk_update_status(body: BulkStatusUpdate, session: Session = Depends(get_db)):
    """Update status for multiple jobs in a single transaction."""
    try:
        new_status = JobStatus(body.status)
    except ValueError:
        raise HTTPException(400, f"Invalid status: {body.status}")
    updated = 0
    for h in body.hashes:
        job = session.execute(select(Job).where(Job.hash == h)).scalar_one_or_none()
        if job:
            job.status = new_status
            updated += 1
    session.flush()
    return {"updated": updated, "status": body.status}


class BulkDelete(BaseModel):
    hashes: list[str]


@router.post("/api/jobs/bulk/delete")
async def bulk_delete_jobs(body: BulkDelete, session: Session = Depends(get_db)):
    """Delete multiple jobs in a single transaction."""
    deleted = 0
    for h in body.hashes:
        if delete_job(session, h):
            deleted += 1
    return {"deleted": deleted}


# --- Single job endpoints ---

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


@router.post("/api/jobs/{job_hash}/reformat")
async def reformat_description(job_hash: str, request: Request, session: Session = Depends(get_db)):
    """Re-format a job description using LLM for clean Markdown output."""
    job = session.execute(select(Job).where(Job.hash == job_hash)).scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")
    if not job.description_text:
        raise HTTPException(400, "No description to reformat")

    settings = request.app.state.settings
    api_key = settings.openai_api_key
    if not api_key:
        raise HTTPException(400, "OpenAI API key not set — go to Settings")

    from job_hunter.matching.description_cleaner import clean_description_llm
    cleaned = clean_description_llm(job.description_text, api_key)
    job.description_text = cleaned
    session.flush()
    return {"hash": job.hash, "description_length": len(cleaned)}


@router.post("/api/jobs/{job_hash}/apply")
async def apply_single_job(job_hash: str, request: Request, session: Session = Depends(get_db)):
    """Trigger Easy Apply for a single job."""
    from fastapi.responses import JSONResponse

    job = session.execute(select(Job).where(Job.hash == job_hash)).scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")
    if not job.url or not job.url.startswith("http"):
        raise HTTPException(400, "Job has no valid LinkedIn URL")

    settings = request.app.state.settings
    tm = request.app.state.task_manager

    if tm.is_running:
        return JSONResponse({"error": "A task is already running"}, status_code=409)

    from job_hunter.db.models import ApplicationAttempt, ApplicationResult
    from job_hunter.db.repo import make_session, save_attempt
    from job_hunter.linkedin.apply import apply_to_job

    engine = request.app.state.engine
    job_url = job.url
    job_hash_val = job.hash
    mock = settings.mock
    dry_run = settings.dry_run
    headless = settings.headless
    slowmo_ms = settings.slowmo_ms

    resume_path = settings.data_dir / "resume.pdf"
    resume_str = str(resume_path) if resume_path.exists() else "tests/fixtures/resume.txt"
    cookies_path = str(settings.data_dir / "cookies.json")

    # Build form answers from user profile
    profile_form_answers: dict[str, str] = {}
    try:
        from job_hunter.config.loader import load_user_profile
        user_profile_path = settings.data_dir / "user_profile.yml"
        if user_profile_path.exists():
            user_profile = load_user_profile(user_profile_path)
            profile_form_answers = user_profile.build_form_answers()
    except Exception:
        pass

    async def _run():
        from job_hunter.linkedin.mock_site import MockLinkedInServer
        actual_url = job_url
        mock_server = None
        if mock:
            mock_server = MockLinkedInServer()
            base_url = mock_server.start()
            actual_url = f"{base_url}{job_url}"
        try:
            result = await apply_to_job(
                job_url=actual_url, resume_path=resume_str,
                dry_run=dry_run, headless=headless,
                slowmo_ms=slowmo_ms, mock=mock,
                cookies_path=cookies_path,
                form_answers=profile_form_answers,
            )
            sess = make_session(engine)
            result_map = {"success": ApplicationResult.SUCCESS, "dry_run": ApplicationResult.DRY_RUN,
                          "failed": ApplicationResult.FAILED, "blocked": ApplicationResult.BLOCKED,
                          "already_applied": ApplicationResult.ALREADY_APPLIED}
            attempt = ApplicationAttempt(
                job_hash=job_hash_val, result=result_map.get(result["result"], ApplicationResult.FAILED),
                failure_stage=result.get("failure_stage"),
                form_answers_json=result.get("form_answers", {}),
            )
            save_attempt(sess, attempt)
            status_map = {"success": JobStatus.APPLIED, "dry_run": JobStatus.APPLIED,
                          "failed": JobStatus.FAILED, "blocked": JobStatus.BLOCKED,
                          "already_applied": JobStatus.APPLIED}
            from job_hunter.db.models import Job as JobModel
            db_job = sess.execute(select(JobModel).where(JobModel.hash == job_hash_val)).scalar_one_or_none()
            if db_job:
                db_job.status = status_map.get(result["result"], JobStatus.FAILED)
            sess.commit()
            sess.close()
            return {"result": result["result"], "job_hash": job_hash_val}
        finally:
            if mock_server:
                mock_server.stop()

    tm.start_task("apply", _run())
    return JSONResponse({"started": "apply", "job_hash": job_hash_val}, status_code=202)


