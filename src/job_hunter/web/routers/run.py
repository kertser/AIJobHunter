"""Run router — trigger pipeline operations and stream progress via SSE."""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from job_hunter.config.loader import load_profiles, load_user_profile
from job_hunter.web.deps import get_user_data_dir
from job_hunter.web.task_manager import TaskManager

logger = logging.getLogger("job_hunter.web.run")

router = APIRouter(tags=["run"])


def _load_run_params(settings, *, data_dir: Path | None = None) -> dict:
    """Load common params from settings + profile files for pipeline runs.

    *data_dir* overrides ``settings.data_dir`` when per-user directories are used.
    """
    dd = data_dir or settings.data_dir
    params: dict = {
        "mock": settings.mock,
        "dry_run": settings.dry_run,
        "headless": settings.headless,
        "slowmo_ms": settings.slowmo_ms,
        "data_dir": dd,
        "openai_api_key": settings.openai_api_key,
        "llm_provider": settings.llm_provider,
        "local_llm_url": settings.local_llm_url,
        "local_llm_model": settings.local_llm_model,
        "cookies_path": str(settings.data_dir / "cookies.json"),
    }

    # Load search profile defaults
    profiles_path = dd / "profiles.yml"
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

    # Load resume text and user preferences
    user_profile_path = dd / "user_profile.yml"
    if user_profile_path.exists():
        try:
            up = load_user_profile(user_profile_path)
            params["resume_text"] = (
                f"{up.name}\n{up.title}\n{up.summary}\n"
                f"Skills: {', '.join(up.skills)}\n"
                f"Experience: {up.experience_years} years\n"
            )
            params["user_preferences"] = {
                "preferred_industries": up.preferred_industries,
                "disliked_industries": up.disliked_industries,
            }
        except Exception:
            pass

    resume_path = dd / "resume.pdf"
    params["resume_path"] = str(resume_path) if resume_path.exists() else "tests/fixtures/resume.txt"

    params.setdefault("profile_name", "default")
    params.setdefault("resume_text", "")
    params.setdefault("user_preferences", None)

    return params


def _get_user_id(request: Request) -> uuid.UUID | None:
    user = getattr(request.state, "user", None)
    return user.id if user else None


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
    engine = request.app.state.engine
    if tm.is_running:
        return JSONResponse({"error": "A task is already running"}, status_code=409)

    from job_hunter.web.deps import get_effective_settings
    settings = get_effective_settings(request)
    user_id = _get_user_id(request)
    data_dir = get_user_data_dir(request)
    params = _load_run_params(settings, data_dir=data_dir)

    from job_hunter.linkedin.discover import discover_jobs
    from job_hunter.db.models import Job
    from job_hunter.db.repo import make_session, upsert_job

    captured_user_id = user_id

    # Create a Queue for remote click coordinates (interactive CAPTCHA solving).
    click_queue: asyncio.Queue[dict | None] = asyncio.Queue()
    request.app.state._discovery_click_queue = click_queue

    async def _run():
        try:
            # Build the interactive captcha handler closure.
            # It uses solve_captcha_interactively from session.py with the click queue.
            async def _captcha_handler(page, screenshot_dir):
                from job_hunter.linkedin.session import solve_captcha_interactively

                async def _get_click():
                    return await click_queue.get()

                return await solve_captcha_interactively(
                    page,
                    get_remote_click=_get_click,
                    screenshot_dir=screenshot_dir,
                    progress=logger.info,
                    timeout_s=300,
                )

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
                captcha_handler=_captcha_handler,
                settings=settings,
            )
            logger.info("Discover returned %d jobs", len(job_dicts))
            if not job_dicts:
                logger.warning("No jobs discovered. Check the progress log above for details.")
            session = make_session(engine)
            for jd in job_dicts:
                job = Job(**jd)
                if captured_user_id is not None:
                    job.user_id = captured_user_id
                upsert_job(session, job, user_id=captured_user_id)
            session.commit()
            session.close()
            return {"discovered": len(job_dicts)}
        finally:
            request.app.state._discovery_click_queue = None

    tm.start_task("discover", _run())
    return JSONResponse({"started": "discover"}, status_code=202)


@router.post("/api/run/score")
async def run_score(request: Request):
    tm: TaskManager = request.app.state.task_manager
    engine = request.app.state.engine
    if tm.is_running:
        return JSONResponse({"error": "A task is already running"}, status_code=409)

    from job_hunter.web.deps import get_effective_settings
    settings = get_effective_settings(request)
    user_id = _get_user_id(request)
    data_dir = get_user_data_dir(request)
    params = _load_run_params(settings, data_dir=data_dir)

    from job_hunter.db.models import JobStatus, Score
    from job_hunter.db.repo import make_session, save_score
    from job_hunter.matching.embeddings import FakeEmbedder, OpenAIEmbedder
    from job_hunter.matching.llm_eval import FakeLLMEvaluator, OpenAILLMEvaluator
    from job_hunter.matching.scoring import compute_score, decide_job_status, decision_to_db

    captured_user_id = user_id

    async def _run():
        def _score_sync():
            session = make_session(engine)

            user_prefs = params.get("user_preferences")

            if params["mock"]:
                embedder = FakeEmbedder(fixed_similarity=0.5)
                evaluator = FakeLLMEvaluator(fit_score=80, decision="apply")
            elif params.get("llm_provider") == "local":
                # Local LLM mode: no OpenAI calls at all.
                # Use FakeEmbedder (local models produce poor embeddings)
                # and point the LLM evaluator at the local sidecar.
                embedder = FakeEmbedder(fixed_similarity=0.5)
                local_url = params.get("local_llm_url", "http://localhost:8080/v1")
                local_model = params.get("local_llm_model", "") or "local"
                from job_hunter.llm_client import get_task_params
                tp = get_task_params(settings, "scoring")
                evaluator = OpenAILLMEvaluator(
                    api_key="local-no-key-needed",
                    model=local_model,
                    base_url=local_url,
                    temperature=tp.temperature,
                    max_tokens=tp.max_tokens,
                )
            else:
                api_key = params["openai_api_key"]
                if not api_key:
                    raise ValueError(
                        "OpenAI API key not set. Go to Settings and enter your key, "
                        "set JOBHUNTER_OPENAI_API_KEY environment variable, "
                        "or switch LLM Provider to 'local'."
                    )
                embedder = OpenAIEmbedder(api_key=api_key)
                from job_hunter.llm_client import get_task_params
                tp = get_task_params(settings, "scoring")
                evaluator = OpenAILLMEvaluator(
                    api_key=api_key,
                    temperature=tp.temperature,
                    max_tokens=tp.max_tokens,
                )

            # ── Auto-reformat unformatted descriptions with AI ──
            from sqlalchemy import select
            from job_hunter.db.models import Job
            from job_hunter.matching.description_cleaner import clean_description_llm, looks_llm_formatted

            reformat_q = select(Job).where(
                Job.description_formatted.is_(False) | Job.description_formatted.is_(None),
                Job.description_text != "",
                Job.description_text.isnot(None),
            )
            if captured_user_id is not None:
                reformat_q = reformat_q.where(Job.user_id == captured_user_id)
            unformatted = session.execute(reformat_q).scalars().all()

            if unformatted:
                logger.info("Reformatting %d job description(s) with AI before scoring…", len(unformatted))
                reformatted = 0
                for uj in unformatted:
                    try:
                        cleaned = clean_description_llm(
                            uj.description_text, settings=settings,
                        )
                        if looks_llm_formatted(cleaned):
                            uj.description_text = cleaned
                            uj.description_formatted = True
                            reformatted += 1
                        else:
                            # LLM unavailable or failed — mark as attempted
                            # so we don't retry rule-based results every run
                            uj.description_formatted = True
                    except Exception as exc:
                        logger.warning("Reformat failed for %s: %s", uj.title, exc)
                session.commit()
                logger.info("Reformatted %d/%d descriptions", reformatted, len(unformatted))

            # Find all jobs that don't have a Score record yet (scoped to user)
            job_q = select(Job)
            if captured_user_id is not None:
                job_q = job_q.where(Job.user_id == captured_user_id)
            all_jobs = session.execute(job_q).scalars().all()

            score_q = select(Score.job_hash)
            if captured_user_id is not None:
                score_q = score_q.where(Score.user_id == captured_user_id)
            scored_hashes = set(row[0] for row in session.execute(score_q).all())
            unscored_jobs = [j for j in all_jobs if j.hash not in scored_hashes]

            if not unscored_jobs:
                session.close()
                return {"scored": 0, "message": "All jobs already scored"}

            logger.info("Found %d unscored jobs to process", len(unscored_jobs))
            scored = 0
            consecutive_errors = 0
            for i, job in enumerate(unscored_jobs, 1):
                if not job.description_text:
                    logger.warning("Skipping %s (%s) — no description", job.hash, job.title)
                    continue
                logger.info("Scoring job %d/%d: %s at %s", i, len(unscored_jobs), job.title, job.company)
                try:
                    result = compute_score(
                        resume_text=params.get("resume_text", ""),
                        job_description=job.description_text or "",
                        embedder=embedder, llm_evaluator=evaluator,
                        user_preferences=user_prefs,
                    )
                except Exception as exc:
                    exc_str = str(exc)
                    # Detect quota / rate-limit errors and abort early
                    if "429" in exc_str or "quota" in exc_str.lower() or "rate" in exc_str.lower():
                        logger.error(
                            "❌ API quota/rate-limit error — aborting scoring. "
                            "Switch to a local LLM in Settings or check your API plan. Error: %s",
                            exc,
                        )
                        break
                    consecutive_errors += 1
                    logger.error("❌ score failed for %s: %s", job.title, exc)
                    if consecutive_errors >= 3:
                        logger.error("Too many consecutive errors (%d) — aborting scoring", consecutive_errors)
                        break
                    continue
                consecutive_errors = 0
                score_row = Score(
                    job_hash=job.hash, embedding_similarity=result["embedding_similarity"],
                    llm_fit_score=result["llm_fit_score"], missing_skills=result["missing_skills"],
                    risk_flags=result["risk_flags"], decision=decision_to_db(result["decision"]),
                    user_id=captured_user_id,
                )
                save_score(session, score_row, user_id=captured_user_id)
                job.status = decide_job_status(
                    easy_apply=job.easy_apply, fit_score=result["llm_fit_score"],
                    similarity=result["embedding_similarity"], decision_str=result["decision"],
                    min_fit_score=params.get("min_fit_score", 75),
                    min_similarity=params.get("min_similarity", 0.35),
                )
                logger.info(
                    "  → fit=%d, sim=%.3f, decision=%s",
                    result["llm_fit_score"], result["embedding_similarity"], result["decision"],
                )
                scored += 1
            session.commit()
            session.close()
            return {"scored": scored}

        # Run sync scoring in a thread so SSE progress events stream in real time
        return await asyncio.to_thread(_score_sync)

    tm.start_task("score", _run())
    return JSONResponse({"started": "score"}, status_code=202)


@router.post("/api/run/apply")
async def run_apply(request: Request):
    tm: TaskManager = request.app.state.task_manager
    engine = request.app.state.engine
    if tm.is_running:
        return JSONResponse({"error": "A task is already running"}, status_code=409)

    from job_hunter.web.deps import get_effective_settings
    settings = get_effective_settings(request)
    user_id = _get_user_id(request)
    data_dir = get_user_data_dir(request)
    params = _load_run_params(settings, data_dir=data_dir)
    captured_user_id = user_id
    captured_settings = settings

    # Build form answers from user profile
    profile_form_answers: dict[str, str] = {}
    user_profile = None
    try:
        user_profile_path = data_dir / "user_profile.yml"
        if user_profile_path.exists():
            user_profile = load_user_profile(user_profile_path)
            profile_form_answers = user_profile.build_form_answers()
    except Exception:
        pass

    user_profile_dict = None
    try:
        if user_profile:
            user_profile_dict = user_profile.model_dump()
    except Exception:
        pass

    async def _run():
        from job_hunter.db.models import ApplicationAttempt, ApplicationResult, JobStatus
        from job_hunter.db.repo import get_jobs_by_status, make_session, save_attempt
        from job_hunter.linkedin.apply import apply_to_job
        from job_hunter.linkedin.mock_site import MockLinkedInServer

        session = make_session(engine)
        queued = get_jobs_by_status(session, JobStatus.QUEUED, user_id=captured_user_id)
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
                    openai_api_key=params.get("openai_api_key", ""),
                    user_profile=user_profile_dict,
                    settings=captured_settings,
                )
                result_map = {"success": ApplicationResult.SUCCESS, "dry_run": ApplicationResult.DRY_RUN,
                              "failed": ApplicationResult.FAILED, "blocked": ApplicationResult.BLOCKED,
                              "already_applied": ApplicationResult.ALREADY_APPLIED}
                attempt = ApplicationAttempt(
                    job_hash=job.hash, result=result_map.get(result["result"], ApplicationResult.FAILED),
                    failure_stage=result.get("failure_stage"),
                    form_answers_json=result.get("form_answers", {}),
                    user_id=captured_user_id,
                )
                save_attempt(session, attempt, user_id=captured_user_id)
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
    if tm.is_running:
        return JSONResponse({"error": "A task is already running"}, status_code=409)

    from job_hunter.web.deps import get_effective_settings
    settings = get_effective_settings(request)
    data_dir = get_user_data_dir(request)
    params = _load_run_params(settings, data_dir=data_dir)

    from job_hunter.orchestration.pipeline import run_pipeline

    # Create click queue for interactive CAPTCHA solving during discover phase
    click_queue: asyncio.Queue[dict | None] = asyncio.Queue()
    request.app.state._discovery_click_queue = click_queue

    async def _captcha_handler(page, screenshot_dir):
        from job_hunter.linkedin.session import solve_captcha_interactively

        async def _get_click():
            return await click_queue.get()

        return await solve_captcha_interactively(
            page,
            get_remote_click=_get_click,
            screenshot_dir=screenshot_dir,
            progress=logger.info,
            timeout_s=300,
        )

    async def _run():
        try:
            return await run_pipeline(**params, captcha_handler=_captcha_handler, settings=settings)
        finally:
            request.app.state._discovery_click_queue = None

    tm.start_task("pipeline", _run())
    return JSONResponse({"started": "pipeline"}, status_code=202)


@router.post("/api/run/market")
async def run_market(request: Request):
    """Run the full market intelligence pipeline as a background task."""
    tm: TaskManager = request.app.state.task_manager
    engine = request.app.state.engine
    if tm.is_running:
        return JSONResponse({"error": "A task is already running"}, status_code=409)

    from job_hunter.web.deps import get_effective_settings
    settings = get_effective_settings(request)
    data_dir = get_user_data_dir(request)

    from job_hunter.db.repo import make_session
    from job_hunter.market.extract import (
        FakeMarketExtractor,
        HeuristicExtractor,
        OpenAIMarketExtractor,
    )
    from job_hunter.market.pipeline import run_market_pipeline
    from job_hunter.market.title_normalizer import (
        FakeTitleNormalizer,
        HeuristicTitleNormalizer,
        OpenAITitleNormalizer,
    )

    # Choose extractor based on mock mode and LLM provider
    if settings.mock:
        extractor = FakeMarketExtractor()
        title_norm = FakeTitleNormalizer()
    elif settings.llm_provider == "local":
        local_url = settings.local_llm_url or "http://localhost:8080/v1"
        local_model = settings.local_llm_model or "local"
        from job_hunter.llm_client import get_task_params
        ext_tp = get_task_params(settings, "market_extract")
        title_tp = get_task_params(settings, "title_normalize")
        extractor = OpenAIMarketExtractor(
            api_key="local-no-key-needed",
            model=local_model,
            base_url=local_url,
            temperature=ext_tp.temperature,
            max_tokens=ext_tp.max_tokens,
        )
        title_norm = OpenAITitleNormalizer(
            api_key="local-no-key-needed",
            model=local_model,
            base_url=local_url,
            temperature=title_tp.temperature,
            max_tokens=title_tp.max_tokens,
        )
    elif settings.openai_api_key:
        from job_hunter.llm_client import get_task_params
        ext_tp = get_task_params(settings, "market_extract")
        title_tp = get_task_params(settings, "title_normalize")
        extractor = OpenAIMarketExtractor(
            api_key=settings.openai_api_key,
            temperature=ext_tp.temperature,
            max_tokens=ext_tp.max_tokens,
        )
        title_norm = OpenAITitleNormalizer(
            api_key=settings.openai_api_key,
            temperature=title_tp.temperature,
            max_tokens=title_tp.max_tokens,
        )
    else:
        extractor = HeuristicExtractor()
        title_norm = HeuristicTitleNormalizer()

    # Load user profile if available
    user_profile = None
    profile_path = data_dir / "user_profile.yml"
    if profile_path.exists():
        try:
            user_profile = load_user_profile(profile_path)
        except Exception:
            pass

    async def _run():
        session = make_session(engine)
        try:
            summary = await asyncio.to_thread(
                run_market_pipeline,
                session,
                extractor=extractor,
                profile=user_profile,
                candidate_key="default",
                title_normalizer=title_norm,
            )
            return summary
        finally:
            session.close()

    tm.start_task("market", _run())
    return JSONResponse({"started": "market"}, status_code=202)


@router.post("/api/run/report")
async def run_report(request: Request):
    """Generate a daily report (fast, no SSE needed)."""
    engine = request.app.state.engine
    data_dir = get_user_data_dir(request)

    from job_hunter.db.repo import make_session
    from job_hunter.reporting.report import generate_report

    session = make_session(engine)
    try:
        summary = generate_report(session=session, data_dir=data_dir)
        return JSONResponse({
            "date": summary.get("date"),
            "md_path": str(summary.get("md_path", "")),
            "json_path": str(summary.get("json_path", "")),
        })
    finally:
        session.close()


class CaptchaClickRequest(BaseModel):
    """Remote click on a CAPTCHA screenshot during discovery."""
    x: float = 0
    y: float = 0
    cancel: bool = False


@router.post("/api/run/captcha-click")
async def captcha_click(body: CaptchaClickRequest, request: Request):
    """Send a click to the running discovery's interactive CAPTCHA solver.

    The frontend translates image click coordinates to the 1280×900 viewport
    and POSTs them here.  The discovery task picks them up from the queue and
    executes the click via Playwright.

    Send ``{"cancel": true}`` to stop the interaction loop.
    """
    queue: asyncio.Queue | None = getattr(
        request.app.state, "_discovery_click_queue", None,
    )
    if queue is None:
        return JSONResponse(
            {"error": "No discovery task is waiting for CAPTCHA clicks."},
            status_code=400,
        )
    if body.cancel:
        await queue.put(None)
        return {"submitted": True, "action": "cancel"}
    await queue.put({"x": body.x, "y": body.y})
    return {"submitted": True, "action": "click", "x": body.x, "y": body.y}


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

