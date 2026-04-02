"""Jobs router — list, detail, status update."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from job_hunter.db.models import ApplicationAttempt, ApplicationResult, Job, JobStatus, Score
from job_hunter.db.repo import delete_job, get_scores_for_jobs
from job_hunter.web.deps import get_db, get_user_data_dir

router = APIRouter(tags=["jobs"])


class StatusUpdate(BaseModel):
    status: str


def _get_user_id(request: Request) -> uuid.UUID | None:
    user = getattr(request.state, "user", None)
    return user.id if user else None


def _get_applied_map(session: Session, hashes: list[str], *, user_id: uuid.UUID | None = None) -> dict:
    """Build a map of job_hash → earliest applied_at datetime."""
    if not hashes:
        return {}
    query = (
        select(ApplicationAttempt)
        .where(ApplicationAttempt.job_hash.in_(hashes))
        .where(ApplicationAttempt.result.in_([
            ApplicationResult.SUCCESS, ApplicationResult.DRY_RUN,
        ]))
    )
    if user_id is not None:
        query = query.where(ApplicationAttempt.user_id == user_id)
    attempts = session.execute(query).scalars().all()
    result = {}
    for a in attempts:
        if a.job_hash not in result or (a.started_at and a.started_at < result[a.job_hash]):
            result[a.job_hash] = a.started_at
    return result


@router.get("/jobs")
async def jobs_page(
    request: Request,
    session: Session = Depends(get_db),
    status: str = "",
    page: int = 1,
    per_page: int = 50,
):
    user_id = _get_user_id(request)
    templates = request.app.state.templates

    # Clamp page
    if page < 1:
        page = 1

    # Base filter
    base_query = select(Job)
    if user_id is not None:
        base_query = base_query.where(Job.user_id == user_id)
    if status:
        try:
            base_query = base_query.where(Job.status == JobStatus(status))
        except ValueError:
            pass

    # Total count
    count_query = select(func.count()).select_from(base_query.subquery())
    total_count = session.execute(count_query).scalar() or 0
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    # Paginated rows
    query = base_query.order_by(Job.collected_at.desc())
    query = query.offset((page - 1) * per_page).limit(per_page)
    jobs = session.execute(query).scalars().all()

    hashes = [j.hash for j in jobs]
    scores_map = get_scores_for_jobs(session, hashes, user_id=user_id)
    applied_map = _get_applied_map(session, hashes, user_id=user_id)
    # Show only statuses that are actually used in the pipeline
    statuses = [s.value for s in JobStatus if s != JobStatus.SCORED]
    return templates.TemplateResponse(request, "jobs.html", {
        "jobs": jobs, "scores_map": scores_map, "applied_map": applied_map,
        "statuses": statuses, "current_status": status,
        "page": page, "per_page": per_page,
        "total_pages": total_pages, "total_count": total_count,
    })


@router.get("/api/jobs")
async def list_jobs(
    request: Request,
    session: Session = Depends(get_db),
    status: str = "",
    company: str = "",
    title: str = "",
    page: int = 1,
    per_page: int = 50,
):
    user_id = _get_user_id(request)
    query = select(Job)
    if user_id is not None:
        query = query.where(Job.user_id == user_id)
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
    scores_map = get_scores_for_jobs(session, hashes, user_id=user_id)
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
    user_id = _get_user_id(request)
    query = select(Job).where(Job.hash == job_hash)
    if user_id is not None:
        query = query.where(Job.user_id == user_id)
    job = session.execute(query).scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")
    scores = session.execute(select(Score).where(Score.job_hash == job_hash)).scalars().all()
    attempts = session.execute(
        select(ApplicationAttempt).where(ApplicationAttempt.job_hash == job_hash)
    ).scalars().all()
    # Get applied_at date from successful attempts
    applied_map = _get_applied_map(session, [job_hash], user_id=user_id)
    applied_at = applied_map.get(job_hash)

    # Prev / next neighbours scoped to user
    prev_q = (
        select(Job.hash)
        .where(
            or_(
                Job.collected_at > job.collected_at,
                and_(Job.collected_at == job.collected_at, Job.hash > job.hash),
            )
        )
        .order_by(Job.collected_at.asc(), Job.hash.asc())
        .limit(1)
    )
    next_q = (
        select(Job.hash)
        .where(
            or_(
                Job.collected_at < job.collected_at,
                and_(Job.collected_at == job.collected_at, Job.hash < job.hash),
            )
        )
        .order_by(Job.collected_at.desc(), Job.hash.desc())
        .limit(1)
    )
    if user_id is not None:
        prev_q = prev_q.where(Job.user_id == user_id)
        next_q = next_q.where(Job.user_id == user_id)
    prev_hash = session.execute(prev_q).scalar_one_or_none()
    next_hash = session.execute(next_q).scalar_one_or_none()

    # Market intelligence boost (safe to call even when tables are empty)
    market_boost: dict[str, Any] = {}
    try:
        from job_hunter.matching.scoring import compute_market_boost
        market_boost = compute_market_boost(session, job_title=job.title, candidate_key="default")
    except Exception:
        pass

    templates = request.app.state.templates
    return templates.TemplateResponse(request, "job_detail.html", {
        "job": job, "scores": scores, "attempts": attempts,
        "applied_at": applied_at, "market": market_boost,
        "prev_hash": prev_hash, "next_hash": next_hash,
    })


# --- Bulk endpoints (must be before /{job_hash} routes) ---

class BulkStatusUpdate(BaseModel):
    hashes: list[str]
    status: str


@router.patch("/api/jobs/bulk/status")
async def bulk_update_status(body: BulkStatusUpdate, request: Request, session: Session = Depends(get_db)):
    """Update status for multiple jobs in a single transaction."""
    user_id = _get_user_id(request)
    try:
        new_status = JobStatus(body.status)
    except ValueError:
        raise HTTPException(400, f"Invalid status: {body.status}")
    updated = 0
    for h in body.hashes:
        query = select(Job).where(Job.hash == h)
        if user_id is not None:
            query = query.where(Job.user_id == user_id)
        job = session.execute(query).scalar_one_or_none()
        if job:
            job.status = new_status
            updated += 1
    session.flush()
    return {"updated": updated, "status": body.status}


class BulkDelete(BaseModel):
    hashes: list[str]


@router.post("/api/jobs/bulk/delete")
async def bulk_delete_jobs(body: BulkDelete, request: Request, session: Session = Depends(get_db)):
    """Delete multiple jobs in a single transaction."""
    user_id = _get_user_id(request)
    deleted = 0
    for h in body.hashes:
        if delete_job(session, h, user_id=user_id):
            deleted += 1
    return {"deleted": deleted}


@router.post("/api/jobs/bulk/skip-all")
async def bulk_skip_all(request: Request, session: Session = Depends(get_db), status: str = ""):
    """Set ALL jobs matching the filter to 'skipped' in one go."""
    user_id = _get_user_id(request)
    query = select(Job)
    if user_id is not None:
        query = query.where(Job.user_id == user_id)
    if status:
        try:
            query = query.where(Job.status == JobStatus(status))
        except ValueError:
            pass
    # Exclude already-skipped jobs
    query = query.where(Job.status != JobStatus.SKIPPED)
    jobs = session.execute(query).scalars().all()
    for j in jobs:
        j.status = JobStatus.SKIPPED
    session.flush()
    return {"updated": len(jobs)}


@router.post("/api/jobs/bulk/delete-all")
async def bulk_delete_all(request: Request, session: Session = Depends(get_db), status: str = ""):
    """Delete ALL jobs matching the filter in one go."""
    user_id = _get_user_id(request)
    query = select(Job)
    if user_id is not None:
        query = query.where(Job.user_id == user_id)
    if status:
        try:
            query = query.where(Job.status == JobStatus(status))
        except ValueError:
            pass
    jobs = session.execute(query).scalars().all()
    deleted = 0
    for j in jobs:
        if delete_job(session, j.hash, user_id=user_id):
            deleted += 1
    return {"deleted": deleted}


# --- Single job endpoints ---

@router.patch("/api/jobs/{job_hash}/status")
async def update_job_status(job_hash: str, body: StatusUpdate, request: Request, session: Session = Depends(get_db)):
    user_id = _get_user_id(request)
    query = select(Job).where(Job.hash == job_hash)
    if user_id is not None:
        query = query.where(Job.user_id == user_id)
    job = session.execute(query).scalar_one_or_none()
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
async def remove_job(job_hash: str, request: Request, session: Session = Depends(get_db)):
    user_id = _get_user_id(request)
    deleted = delete_job(session, job_hash, user_id=user_id)
    if not deleted:
        raise HTTPException(404, "Job not found")
    return {"deleted": True, "hash": job_hash}


@router.post("/api/jobs/{job_hash}/reformat")
async def reformat_description(job_hash: str, request: Request, session: Session = Depends(get_db)):
    """Re-format a job description using LLM for clean Markdown output."""
    user_id = _get_user_id(request)
    query = select(Job).where(Job.hash == job_hash)
    if user_id is not None:
        query = query.where(Job.user_id == user_id)
    job = session.execute(query).scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")
    if not job.description_text:
        raise HTTPException(400, "No description to reformat")

    from job_hunter.web.deps import get_effective_settings
    eff = get_effective_settings(request)
    api_key = eff.openai_api_key

    from job_hunter.matching.description_cleaner import clean_description_llm
    from job_hunter.llm_client import get_task_params, is_local_provider

    if not api_key and not is_local_provider(eff):
        raise HTTPException(400, "No LLM available — set an OpenAI API key or switch to a local LLM in Settings")

    tp = get_task_params(eff, "description_clean")
    cleaned = clean_description_llm(
        job.description_text, api_key or "",
        temperature=tp.temperature,
        max_tokens=tp.max_tokens,
        settings=eff,
    )
    job.description_text = cleaned
    job.description_formatted = True
    session.flush()
    return {"hash": job.hash, "description_length": len(cleaned)}


@router.post("/api/jobs/{job_hash}/apply")
async def apply_single_job(job_hash: str, request: Request, session: Session = Depends(get_db)):
    """Trigger Easy Apply for a single job."""
    from fastapi.responses import JSONResponse

    user_id = _get_user_id(request)
    query = select(Job).where(Job.hash == job_hash)
    if user_id is not None:
        query = query.where(Job.user_id == user_id)
    job = session.execute(query).scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")
    if not job.url or not job.url.startswith("http"):
        raise HTTPException(400, "Job has no valid LinkedIn URL")

    from job_hunter.web.deps import get_effective_settings
    eff = get_effective_settings(request)
    tm = request.app.state.task_manager

    if tm.is_running:
        return JSONResponse({"error": "A task is already running"}, status_code=409)

    from job_hunter.db.models import ApplicationAttempt, ApplicationResult
    from job_hunter.db.repo import make_session, save_attempt
    from job_hunter.linkedin.apply import apply_to_job

    engine = request.app.state.engine
    data_dir = get_user_data_dir(request)
    job_url = job.url
    job_hash_val = job.hash
    mock = eff.mock
    dry_run = eff.dry_run
    headless = eff.headless
    slowmo_ms = eff.slowmo_ms
    captured_user_id = user_id

    resume_path = data_dir / "resume.pdf"
    resume_str = str(resume_path) if resume_path.exists() else "tests/fixtures/resume.txt"
    cookies_path = str(eff.data_dir / "cookies.json")

    # Build form answers from user profile
    profile_form_answers: dict[str, str] = {}
    user_profile_dict: dict | None = None
    try:
        from job_hunter.config.loader import load_user_profile
        user_profile_path = data_dir / "user_profile.yml"
        if user_profile_path.exists():
            user_profile = load_user_profile(user_profile_path)
            profile_form_answers = user_profile.build_form_answers()
            user_profile_dict = user_profile.model_dump()
    except Exception:
        pass

    api_key = eff.openai_api_key

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
                openai_api_key=api_key,
                user_profile=user_profile_dict,
                settings=eff,
            )
            sess = make_session(engine)
            result_map = {"success": ApplicationResult.SUCCESS, "dry_run": ApplicationResult.DRY_RUN,
                          "failed": ApplicationResult.FAILED, "blocked": ApplicationResult.BLOCKED,
                          "already_applied": ApplicationResult.ALREADY_APPLIED}
            attempt = ApplicationAttempt(
                job_hash=job_hash_val, result=result_map.get(result["result"], ApplicationResult.FAILED),
                failure_stage=result.get("failure_stage"),
                form_answers_json=result.get("form_answers", {}),
                user_id=captured_user_id,
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

