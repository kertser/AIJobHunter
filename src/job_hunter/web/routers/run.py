"""Run router — trigger pipeline operations and stream progress via SSE."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from job_hunter.config.loader import load_profiles, load_user_profile
from job_hunter.web.task_manager import TaskManager

logger = logging.getLogger("job_hunter.web.run")

router = APIRouter(tags=["run"])


def _load_run_params(settings) -> dict:
    """Load common params from settings + profile files for pipeline runs."""
    params: dict = {
        "mock": settings.mock,
        "dry_run": settings.dry_run,
        "headless": settings.headless,
        "slowmo_ms": settings.slowmo_ms,
        "data_dir": settings.data_dir,
        "openai_api_key": settings.openai_api_key,
        "cookies_path": str(settings.data_dir / "cookies.json"),
    }

    # Load search profile defaults
    profiles_path = settings.data_dir / "profiles.yml"
    if profiles_path.exists():
        profs = load_profiles(profiles_path)
        if profs:
            p = profs[0]
            params.update(
                profile_name=p.name,
                keywords=p.keywords,
                location=p.location,
                remote=p.remote,
                seniority=p.seniority,
                min_fit_score=p.min_fit_score,
                min_similarity=p.min_similarity,
                max_applications_per_day=p.max_applications_per_day,
                blacklist_companies=p.blacklist_companies,
                blacklist_titles=p.blacklist_titles,
            )

    # Load resume text
    user_profile_path = settings.data_dir / "user_profile.yml"
    if user_profile_path.exists():
        up = load_user_profile(user_profile_path)
        params["resume_text"] = (
            f"{up.name}\n{up.title}\n{up.summary}\n"
            f"Skills: {', '.join(up.skills)}\n"
            f"Experience: {up.experience_years} years\n"
        )

    resume_path = settings.data_dir / "resume.pdf"
    params["resume_path"] = str(resume_path) if resume_path.exists() else "tests/fixtures/resume.txt"

    params.setdefault("profile_name", "default")
    params.setdefault("resume_text", "")
    return params


@router.get("/run")
async def run_page(request: Request):
    templates = request.app.state.templates
    tm = request.app.state.task_manager
    return templates.TemplateResponse(request, "run.html", {
        "task_status": tm.get_status(),
    })


@router.post("/api/run/discover")
async def run_discover(request: Request):
    tm: TaskManager = request.app.state.task_manager
    settings = request.app.state.settings
    engine = request.app.state.engine
    if tm.is_running:
        return JSONResponse({"error": "A task is already running"}, status_code=409)

    params = _load_run_params(settings)

    from job_hunter.linkedin.discover import discover_jobs
    from job_hunter.db.models import Job
    from job_hunter.db.repo import make_session, upsert_job

    async def _run():
        logger.info(
            "Discover params: mock=%s, headless=%s, keywords=%s, location=%s, "
            "remote=%s, seniority=%s, cookies=%s",
            params["mock"], params["headless"],
            params.get("keywords", []), params.get("location", ""),
            params.get("remote", False), params.get("seniority"),
            params["cookies_path"],
        )
        job_dicts = await discover_jobs(
            profile_name=params["profile_name"],
            mock=params["mock"],
            headless=params["headless"],
            slowmo_ms=params["slowmo_ms"],
            cookies_path=params["cookies_path"],
            keywords=params.get("keywords", []),
            location=params.get("location", ""),
            remote=params.get("remote", False),
            seniority=params.get("seniority"),
            openai_api_key=params.get("openai_api_key", ""),
        )
        logger.info("Discover returned %d jobs", len(job_dicts))
        if not job_dicts:
            logger.warning("No jobs discovered. Check the progress log above for details.")
        session = make_session(engine)
        for jd in job_dicts:
            upsert_job(session, Job(**jd))
        session.commit()
        session.close()
        return {"discovered": len(job_dicts)}

    tm.start_task("discover", _run())
    return JSONResponse({"started": "discover"}, status_code=202)


@router.post("/api/run/score")
async def run_score(request: Request):
    tm: TaskManager = request.app.state.task_manager
    settings = request.app.state.settings
    engine = request.app.state.engine
    if tm.is_running:
        return JSONResponse({"error": "A task is already running"}, status_code=409)

    params = _load_run_params(settings)

    from job_hunter.db.models import JobStatus, Score
    from job_hunter.db.repo import make_session, save_score
    from job_hunter.matching.embeddings import FakeEmbedder, OpenAIEmbedder
    from job_hunter.matching.llm_eval import FakeLLMEvaluator, OpenAILLMEvaluator
    from job_hunter.matching.scoring import compute_score, decide_job_status, decision_to_db

    async def _run():
        session = make_session(engine)

        if params["mock"]:
            embedder = FakeEmbedder(fixed_similarity=0.5)
            evaluator = FakeLLMEvaluator(fit_score=80, decision="apply")
        else:
            api_key = params["openai_api_key"]
            if not api_key:
                raise ValueError(
                    "OpenAI API key not set. Go to Settings and enter your key, "
                    "or set JOBHUNTER_OPENAI_API_KEY environment variable."
                )
            embedder = OpenAIEmbedder(api_key=api_key)
            evaluator = OpenAILLMEvaluator(api_key=api_key)

        # Find all jobs that don't have a Score record yet
        from sqlalchemy import select
        from job_hunter.db.models import Job
        all_jobs = session.execute(select(Job)).scalars().all()
        scored_hashes = set(
            row[0] for row in session.execute(select(Score.job_hash)).all()
        )
        unscored_jobs = [j for j in all_jobs if j.hash not in scored_hashes]

        if not unscored_jobs:
            session.close()
            return {"scored": 0, "message": "All jobs already scored"}

        logger.info("Found %d unscored jobs to process", len(unscored_jobs))
        scored = 0
        for job in unscored_jobs:
            if not job.description_text:
                logger.warning("Skipping %s (%s) — no description", job.hash, job.title)
                continue
            result = compute_score(
                resume_text=params.get("resume_text", ""),
                job_description=job.description_text or "",
                embedder=embedder, llm_evaluator=evaluator,
            )
            score_row = Score(
                job_hash=job.hash, embedding_similarity=result["embedding_similarity"],
                llm_fit_score=result["llm_fit_score"], missing_skills=result["missing_skills"],
                risk_flags=result["risk_flags"], decision=decision_to_db(result["decision"]),
            )
            save_score(session, score_row)
            job.status = decide_job_status(
                easy_apply=job.easy_apply, fit_score=result["llm_fit_score"],
                similarity=result["embedding_similarity"], decision_str=result["decision"],
                min_fit_score=params.get("min_fit_score", 75),
                min_similarity=params.get("min_similarity", 0.35),
            )
            scored += 1
        session.commit()
        session.close()
        return {"scored": scored}

    tm.start_task("score", _run())
    return JSONResponse({"started": "score"}, status_code=202)


@router.post("/api/run/apply")
async def run_apply(request: Request):
    tm: TaskManager = request.app.state.task_manager
    settings = request.app.state.settings
    engine = request.app.state.engine
    if tm.is_running:
        return JSONResponse({"error": "A task is already running"}, status_code=409)

    params = _load_run_params(settings)

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
        from job_hunter.db.models import ApplicationAttempt, ApplicationResult, JobStatus
        from job_hunter.db.repo import get_jobs_by_status, make_session, save_attempt
        from job_hunter.linkedin.apply import apply_to_job
        from job_hunter.linkedin.mock_site import MockLinkedInServer

        session = make_session(engine)
        queued = get_jobs_by_status(session, JobStatus.QUEUED)
        applied = 0
        mock_server = None
        if params["mock"] and queued:
            mock_server = MockLinkedInServer()
            base_url = mock_server.start()
        try:
            for job in queued:
                job_url = f"{base_url}{job.url}" if params["mock"] else job.url
                result = await apply_to_job(
                    job_url=job_url, resume_path=params["resume_path"],
                    dry_run=params["dry_run"], headless=params["headless"],
                    slowmo_ms=params["slowmo_ms"], mock=params["mock"],
                    cookies_path=params["cookies_path"],
                    form_answers=profile_form_answers,
                )
                result_map = {"success": ApplicationResult.SUCCESS, "dry_run": ApplicationResult.DRY_RUN,
                              "failed": ApplicationResult.FAILED, "blocked": ApplicationResult.BLOCKED,
                              "already_applied": ApplicationResult.ALREADY_APPLIED}
                attempt = ApplicationAttempt(
                    job_hash=job.hash, result=result_map.get(result["result"], ApplicationResult.FAILED),
                    failure_stage=result.get("failure_stage"),
                    form_answers_json=result.get("form_answers", {}),
                )
                save_attempt(session, attempt)
                status_map = {"success": JobStatus.APPLIED, "dry_run": JobStatus.APPLIED,
                              "failed": JobStatus.FAILED, "blocked": JobStatus.BLOCKED,
                              "already_applied": JobStatus.APPLIED}
                job.status = status_map.get(result["result"], JobStatus.FAILED)
                if result["result"] in ("success", "dry_run", "already_applied"):
                    applied += 1
                if result["result"] == "blocked":
                    break
            session.commit()
        finally:
            session.close()
            if mock_server:
                mock_server.stop()
        return {"applied": applied}

    tm.start_task("apply", _run())
    return JSONResponse({"started": "apply"}, status_code=202)


@router.post("/api/run/pipeline")
async def run_pipeline_endpoint(request: Request):
    tm: TaskManager = request.app.state.task_manager
    settings = request.app.state.settings
    if tm.is_running:
        return JSONResponse({"error": "A task is already running"}, status_code=409)

    params = _load_run_params(settings)

    from job_hunter.orchestration.pipeline import run_pipeline

    coro = run_pipeline(**params)
    tm.start_task("pipeline", coro)
    return JSONResponse({"started": "pipeline"}, status_code=202)


@router.get("/api/run/status")
async def run_status_sse(request: Request):
    tm: TaskManager = request.app.state.task_manager

    async def event_generator():
        async for event in tm.subscribe():
            yield {"event": event.type, "data": event.message}

    return EventSourceResponse(event_generator())


@router.get("/api/run/task-status")
async def run_task_status(request: Request):
    tm: TaskManager = request.app.state.task_manager
    return tm.get_status()

