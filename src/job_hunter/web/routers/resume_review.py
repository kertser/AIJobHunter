"""Resume Review router — analyse resume gaps against target jobs."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from job_hunter.web.deps import get_user_data_dir

logger = logging.getLogger("job_hunter.web.resume_review")

router = APIRouter(tags=["resume_review"])


@router.get("/resume-review")
async def resume_review_page(request: Request):
    templates = request.app.state.templates
    engine = request.app.state.engine

    user = getattr(request.state, "user", None)
    user_id = user.id if user else None
    data_dir = get_user_data_dir(request)

    from job_hunter.db.repo import get_top_missing_skills, make_session

    session = make_session(engine)
    missing_skills = get_top_missing_skills(session, limit=20, user_id=user_id)
    session.close()

    # Load user profile
    user_skills: list[str] = []
    user_summary = ""
    try:
        from job_hunter.config.loader import load_user_profile
        up_path = data_dir / "user_profile.yml"
        if up_path.exists():
            up = load_user_profile(up_path)
            user_skills = up.skills
            user_summary = f"{up.name} — {up.title}\n{up.summary}"
    except Exception:
        pass

    return templates.TemplateResponse(request, "resume_review.html", {
        "missing_skills": missing_skills,
        "user_skills": user_skills,
        "user_summary": user_summary,
    })


@router.post("/api/resume-review")
async def run_resume_review(request: Request):
    """Analyse resume gaps and suggest improvements using LLM."""
    engine = request.app.state.engine

    user = getattr(request.state, "user", None)
    user_id = user.id if user else None
    data_dir = get_user_data_dir(request)

    from job_hunter.web.deps import get_effective_settings
    eff = get_effective_settings(request)
    api_key = eff.openai_api_key

    from job_hunter.llm_client import is_local_provider
    if not api_key and not is_local_provider(eff):
        return JSONResponse(
            {"error": "No LLM available — set an OpenAI API key or switch to a local LLM in Settings."},
            status_code=400,
        )

    from job_hunter.config.loader import load_user_profile
    from job_hunter.db.repo import get_top_missing_skills, make_session

    session = make_session(engine)
    missing_skills = get_top_missing_skills(session, limit=20, user_id=user_id)

    # Get top scored job descriptions for context (scoped to user)
    from sqlalchemy import select
    from job_hunter.db.models import Job, Score
    score_q = select(Score).order_by(Score.llm_fit_score.desc()).limit(15)
    if user_id is not None:
        score_q = score_q.where(Score.user_id == user_id)
    top_scores = session.execute(score_q).scalars().all()

    job_contexts = []
    for score in top_scores:
        job = session.execute(
            select(Job).where(Job.hash == score.job_hash)
        ).scalar_one_or_none()
        if job:
            job_contexts.append(
                f"**{job.title}** at {job.company} — "
                f"Fit: {score.llm_fit_score}/100, "
                f"Missing: {', '.join(score.missing_skills or [])}"
            )
    session.close()

    # Load user profile from per-user data dir
    user_profile_text = ""
    try:
        up_path = data_dir / "user_profile.yml"
        if up_path.exists():
            up = load_user_profile(up_path)
            user_profile_text = (
                f"Name: {up.name}\nTitle: {up.title}\n"
                f"Summary: {up.summary}\n"
                f"Skills: {', '.join(up.skills)}\n"
                f"Experience: {up.experience_years} years\n"
                f"Education: {', '.join(up.education)}\n"
                f"Spoken Languages: {', '.join(up.spoken_languages)}\n"
                f"Programming Languages: {', '.join(up.programming_languages)}\n"
            )
    except Exception:
        pass

    if not user_profile_text:
        return JSONResponse(
            {"error": "No user profile found. Complete profile setup first."},
            status_code=400,
        )

    # Build the LLM prompt
    missing_str = "\n".join(
        f"  - {skill} (mentioned in {count} job{'s' if count > 1 else ''})"
        for skill, count in missing_skills
    )
    jobs_str = "\n".join(f"  - {ctx}" for ctx in job_contexts[:10])

    system_prompt = """\
You are a career coach and resume improvement specialist. Analyse the candidate's
resume/profile against the target jobs they've been applying for.

Provide a structured Markdown report with:

1. **Resume Strength Assessment** — what's working well
2. **Critical Skill Gaps** — skills that appear frequently in target jobs but are missing
3. **Resume Improvement Suggestions** — specific, actionable changes:
   - Keywords to add
   - Sections to expand or restructure
   - Experience bullet points to rephrase
   - Skills to highlight more prominently
4. **Learning Recommendations** — courses, certifications, or projects to fill gaps
5. **Quick Wins** — changes that can be made immediately for maximum impact

Be specific, practical, and encouraging. Reference actual skills and job requirements."""

    user_message = (
        f"=== CANDIDATE PROFILE ===\n{user_profile_text}\n\n"
        f"=== TOP MISSING SKILLS (from {len(missing_skills)} target jobs) ===\n"
        f"{missing_str or 'No scored jobs yet.'}\n\n"
        f"=== RECENT TARGET JOBS ===\n{jobs_str or 'No scored jobs yet.'}"
    )

    try:
        from job_hunter.llm_client import build_llm_client, get_chat_model, get_task_params
        client = build_llm_client(eff)
        model = get_chat_model(eff)
        params = get_task_params(eff, "resume_review")
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=params.temperature,
            **({"max_tokens": params.max_tokens} if params.max_tokens else {}),
        )
        review_text = response.choices[0].message.content or "No review generated."
    except Exception as exc:
        logger.error("Resume review LLM call failed: %s", exc)
        return JSONResponse({"error": f"LLM error: {exc}"}, status_code=500)

    return {"review": review_text}

