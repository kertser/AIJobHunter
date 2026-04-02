"""Onboarding router — first-run profile setup from resume + LinkedIn."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, RedirectResponse

from job_hunter.web.deps import get_user_data_dir
from job_hunter.web.task_manager import TaskManager

logger = logging.getLogger("job_hunter.web.onboarding")
router = APIRouter(tags=["onboarding"])


@router.get("/onboarding")
async def onboarding_page(request: Request):
    """Show the onboarding wizard. Indicate if profile already exists."""
    data_dir = get_user_data_dir(request)
    profile_exists = (data_dir / "user_profile.yml").exists()
    templates = request.app.state.templates

    from job_hunter.web.deps import get_effective_settings
    eff = get_effective_settings(request)
    has_api_key = bool(eff.openai_api_key and eff.openai_api_key.strip())
    is_mock = eff.mock

    return templates.TemplateResponse(request, "onboarding.html", {
        "profile_exists": profile_exists,
        "has_api_key": has_api_key,
        "is_mock": is_mock,
    })


@router.post("/api/onboarding/generate")
async def generate_profiles(
    request: Request,
    resume: UploadFile = File(...),
    linkedin_url: str = Form(""),
    openai_api_key: str = Form(""),
):
    """Upload resume PDF + optional LinkedIn URL and generate profiles."""
    tm: TaskManager = request.app.state.task_manager

    if tm.is_running:
        return JSONResponse({"error": "A task is already running"}, status_code=409)

    # Validate file
    if not resume.filename or not resume.filename.lower().endswith(".pdf"):
        return JSONResponse({"error": "Please upload a PDF file"}, status_code=400)

    # If the user supplied an API key, persist it to their per-user settings
    submitted_key = openai_api_key.strip()
    if submitted_key:
        user = getattr(request.state, "user", None)
        if user is not None:
            from job_hunter.auth.repo import update_user_settings
            from job_hunter.db.repo import make_session
            session = make_session(request.app.state.engine)
            try:
                secret_key = getattr(request.app.state, "secret_key", "") or ""
                update_user_settings(session, user.id, secret_key=secret_key, openai_api_key=submitted_key)
                session.commit()
            finally:
                session.close()
            logger.info("Saved OpenAI API key for user %s during onboarding", user.id)

    # Save uploaded resume to per-user data dir
    data_dir = get_user_data_dir(request)
    data_dir.mkdir(parents=True, exist_ok=True)
    resume_path = data_dir / "resume.pdf"
    content = await resume.read()
    if len(content) > 10 * 1024 * 1024:  # 10 MB limit
        return JSONResponse({"error": "File too large (max 10 MB)"}, status_code=400)
    resume_path.write_bytes(content)
    logger.info("Saved resume PDF to %s (%d bytes)", resume_path, len(content))

    from job_hunter.web.deps import get_effective_settings
    eff = get_effective_settings(request)
    # Prefer the just-submitted key (in case effective settings haven't refreshed)
    api_key = submitted_key or eff.openai_api_key
    mock = eff.mock
    headless = eff.headless
    linkedin = linkedin_url.strip() or None

    async def _run():
        from job_hunter.profile.extract import extract_texts
        from job_hunter.config.loader import save_profiles, save_user_profile

        logger.info("Extracting text from resume%s…",
                     f" + LinkedIn ({linkedin})" if linkedin else "")

        # extract_texts is synchronous (may launch Playwright for LinkedIn URL)
        extracted = await asyncio.to_thread(
            extract_texts,
            resume_path=resume_path,
            linkedin_source=linkedin,
            headless=headless,
        )
        logger.info("Extracted %d characters of text", len(extracted))

        if mock:
            from job_hunter.profile.generator import FakeProfileGenerator
            generator = FakeProfileGenerator()
        else:
            if not api_key:
                raise ValueError(
                    "OpenAI API key not set. Go to Settings and enter your key."
                )
            from job_hunter.profile.generator import OpenAIProfileGenerator
            from job_hunter.llm_client import get_task_params
            tp = get_task_params(eff, "profile_gen")
            generator = OpenAIProfileGenerator(
                api_key=api_key,
                temperature=tp.temperature,
                max_tokens=tp.max_tokens,
            )

        logger.info("Generating profiles via LLM…")
        result = await asyncio.to_thread(generator.generate, extracted)

        # Save results to per-user data dir
        save_user_profile(result.user_profile, data_dir / "user_profile.yml")
        save_profiles(result.search_profiles, data_dir / "profiles.yml")
        logger.info("Saved user profile and %d search profile(s)", len(result.search_profiles))

        return {
            "user_profile": result.user_profile.model_dump(),
            "search_profiles": [p.model_dump() for p in result.search_profiles],
        }

    tm.start_task("onboarding", _run())
    return JSONResponse({"started": "onboarding"}, status_code=202)

