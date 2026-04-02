"""Main pipeline: discover → score → queue → apply → report."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from job_hunter.db.models import (
    ApplicationAttempt,
    ApplicationResult,
    Job,
    JobStatus,
    Score,
)
from job_hunter.db.repo import (
    count_applied_today,
    get_engine,
    get_jobs_by_status,
    init_db,
    make_session,
    save_attempt,
    save_score,
    upsert_job,
)
from job_hunter.matching.embeddings import Embedder, FakeEmbedder, OpenAIEmbedder
from job_hunter.matching.llm_eval import FakeLLMEvaluator, LLMEvaluator, OpenAILLMEvaluator
from job_hunter.matching.scoring import compute_score, decide_job_status, decision_to_db
from job_hunter.orchestration.policies import can_apply_today, is_blacklisted
from job_hunter.reporting.report import generate_report

logger = logging.getLogger("job_hunter.orchestration.pipeline")


async def run_pipeline(
    *,
    profile_name: str,
    mock: bool = False,
    dry_run: bool = False,
    headless: bool = True,
    slowmo_ms: int = 0,
    data_dir: Path = Path("data"),
    openai_api_key: str = "",
    llm_provider: str = "openai",
    local_llm_url: str = "",
    local_llm_model: str = "",
    min_fit_score: int = 75,
    min_similarity: float = 0.35,
    max_applications_per_day: int = 25,
    blacklist_companies: list[str] | None = None,
    blacklist_titles: list[str] | None = None,
    resume_text: str = "",
    resume_path: str = "",
    user_preferences: dict | None = None,
    cookies_path: str = "",
    keywords: list[str] | None = None,
    location: str = "",
    remote: bool = False,
    seniority: list[str] | None = None,
    captcha_handler: Any | None = None,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Execute the full pipeline for a given search profile.

    Returns a summary dict suitable for the daily report.
    """
    blacklist_companies = blacklist_companies or []
    blacklist_titles = blacklist_titles or []

    engine = get_engine(data_dir)
    init_db(engine)
    session = make_session(engine)

    summary: dict[str, Any] = {
        "profile": profile_name,
        "discovered": 0,
        "scored": 0,
        "queued": 0,
        "skipped": 0,
        "review": 0,
        "applied": 0,
        "dry_run": 0,
        "failed": 0,
        "blocked": 0,
    }

    # --- Phase 1: Discover ---
    logger.info("Pipeline step 1/4: Discover")
    from job_hunter.linkedin.discover import discover_jobs

    job_dicts = await discover_jobs(
        profile_name=profile_name,
        mock=mock,
        headless=headless,
        slowmo_ms=slowmo_ms,
        cookies_path=str(data_dir / "cookies.json"),
        keywords=keywords or [],
        location=location,
        remote=remote,
        seniority=seniority,
        captcha_handler=captcha_handler,
        settings=settings,
    )
    for jd in job_dicts:
        job = Job(**jd)
        upsert_job(session, job)
    session.commit()
    summary["discovered"] = len(job_dicts)
    logger.info("Discovered %d jobs", len(job_dicts))

    # --- Phase 2: Score ---
    logger.info("Pipeline step 2/4: Score")
    embedder: Embedder
    evaluator: LLMEvaluator

    if mock:
        embedder = FakeEmbedder(fixed_similarity=0.5)
        evaluator = FakeLLMEvaluator(fit_score=80, decision="apply")
    elif llm_provider == "local":
        # Local LLM mode: no OpenAI calls at all.
        # Use FakeEmbedder (local models produce poor embeddings)
        # and point the LLM evaluator at the local sidecar.
        embedder = FakeEmbedder(fixed_similarity=0.5)
        _scoring_kwargs: dict[str, Any] = {}
        if settings is not None:
            from job_hunter.llm_client import get_task_params
            tp = get_task_params(settings, "scoring")
            _scoring_kwargs = {"temperature": tp.temperature, "max_tokens": tp.max_tokens}
        evaluator = OpenAILLMEvaluator(
            api_key="local-no-key-needed",
            model=local_llm_model or "local",
            base_url=local_llm_url or "http://localhost:8080/v1",
            **_scoring_kwargs,
        )
    else:
        embedder = OpenAIEmbedder(api_key=openai_api_key)
        _scoring_kwargs = {}
        if settings is not None:
            from job_hunter.llm_client import get_task_params
            tp = get_task_params(settings, "scoring")
            _scoring_kwargs = {"temperature": tp.temperature, "max_tokens": tp.max_tokens}
        evaluator = OpenAILLMEvaluator(api_key=openai_api_key, **_scoring_kwargs)

    new_jobs = get_jobs_by_status(session, JobStatus.NEW)
    total_to_score = len(new_jobs)
    logger.info("Found %d new job(s) to score", total_to_score)
    consecutive_errors = 0
    for idx, job in enumerate(new_jobs, 1):
        # Blacklist check
        if is_blacklisted(
            company=job.company,
            title=job.title,
            blacklist_companies=blacklist_companies,
            blacklist_titles=blacklist_titles,
        ):
            job.status = JobStatus.SKIPPED
            job.notes = "Blacklisted"
            summary["skipped"] += 1
            logger.info("  [%d/%d] ⏭️ %s at %s — blacklisted", idx, total_to_score, job.title, job.company)
            continue

        logger.info("  [%d/%d] Scoring: %s at %s", idx, total_to_score, job.title, job.company)

        # Run compute_score in a thread so the event loop stays free
        # for SSE progress delivery between jobs.
        try:
            result = await asyncio.to_thread(
                compute_score,
                resume_text=resume_text,
                job_description=job.description_text,
                embedder=embedder,
                llm_evaluator=evaluator,
                user_preferences=user_preferences,
            )
        except Exception as exc:
            exc_str = str(exc)
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
            job_hash=job.hash,
            resume_id="default",
            embedding_similarity=result["embedding_similarity"],
            llm_fit_score=result["llm_fit_score"],
            missing_skills=result["missing_skills"],
            risk_flags=result["risk_flags"],
            decision=decision_to_db(result["decision"]),
        )
        save_score(session, score_row)

        new_status = decide_job_status(
            easy_apply=job.easy_apply,
            fit_score=result["llm_fit_score"],
            similarity=result["embedding_similarity"],
            decision_str=result["decision"],
            min_fit_score=min_fit_score,
            min_similarity=min_similarity,
        )
        job.status = new_status
        summary["scored"] += 1

        if new_status == JobStatus.QUEUED:
            summary["queued"] += 1
        elif new_status == JobStatus.SKIPPED:
            summary["skipped"] += 1
        elif new_status == JobStatus.REVIEW:
            summary["review"] += 1

        logger.info(
            "    → fit=%d, sim=%.3f, decision=%s → %s",
            result["llm_fit_score"],
            result["embedding_similarity"],
            result["decision"],
            new_status.value,
        )

    session.commit()
    logger.info("Scored %d jobs", summary["scored"])

    # --- Phase 3: Apply ---
    logger.info("Pipeline step 3/4: Apply")
    from job_hunter.linkedin.apply import apply_to_job
    from job_hunter.linkedin.mock_site import MockLinkedInServer

    queued_jobs = get_jobs_by_status(session, JobStatus.QUEUED)
    applied_today = count_applied_today(session)
    total_to_apply = len(queued_jobs)
    logger.info("Found %d queued job(s) to apply", total_to_apply)

    mock_server = None
    if mock and queued_jobs:
        mock_server = MockLinkedInServer()
        base_url = mock_server.start()

    try:
        for apply_idx, job in enumerate(queued_jobs, 1):
            if not can_apply_today(applied_today=applied_today, max_per_day=max_applications_per_day):
                logger.info("Daily cap reached (%d). Stopping apply phase.", max_applications_per_day)
                break

            logger.info("  [%d/%d] Applying: %s at %s", apply_idx, total_to_apply, job.title, job.company)
            job_url = f"{base_url}{job.url}" if mock else job.url

            result = await apply_to_job(
                job_url=job_url,
                resume_path=resume_path,
                dry_run=dry_run,
                headless=headless,
                slowmo_ms=slowmo_ms,
                mock=mock,
                cookies_path=str(data_dir / "cookies.json"),
                openai_api_key=openai_api_key,
                settings=settings,
            )

            result_map = {
                "success": ApplicationResult.SUCCESS,
                "dry_run": ApplicationResult.DRY_RUN,
                "failed": ApplicationResult.FAILED,
                "blocked": ApplicationResult.BLOCKED,
            }
            db_result = result_map.get(result["result"], ApplicationResult.FAILED)

            attempt = ApplicationAttempt(
                job_hash=job.hash,
                started_at=result.get("started_at"),
                ended_at=result.get("ended_at"),
                result=db_result,
                failure_stage=result.get("failure_stage"),
                form_answers_json=result.get("form_answers", {}),
            )
            save_attempt(session, attempt)

            status_map = {
                "success": JobStatus.APPLIED,
                "dry_run": JobStatus.APPLIED,
                "failed": JobStatus.FAILED,
                "blocked": JobStatus.BLOCKED,
            }
            job.status = status_map.get(result["result"], JobStatus.FAILED)

            if result["result"] == "success":
                summary["applied"] += 1
                applied_today += 1
            elif result["result"] == "dry_run":
                summary["dry_run"] += 1
            elif result["result"] == "blocked":
                summary["blocked"] += 1
                session.commit()
                break
            else:
                summary["failed"] += 1

        session.commit()
    finally:
        if mock_server is not None:
            mock_server.stop()

    logger.info("Apply phase complete: %d applied, %d dry-run, %d failed, %d blocked",
                summary["applied"], summary["dry_run"], summary["failed"], summary["blocked"])

    # --- Phase 4: Report ---
    logger.info("Pipeline step 4/4: Report")
    report_summary = await asyncio.to_thread(
        generate_report, session=session, data_dir=data_dir,
    )
    summary["report_date"] = report_summary.get("date")
    summary["report_md_path"] = report_summary.get("md_path")
    summary["report_json_path"] = report_summary.get("json_path")

    logger.info("Pipeline complete for profile '%s'", profile_name)
    return summary

