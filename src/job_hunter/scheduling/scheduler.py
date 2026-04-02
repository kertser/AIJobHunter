"""APScheduler-based pipeline scheduler with history tracking."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from job_hunter.config.models import PipelineMode, ScheduleConfig, ScheduleRunRecord

logger = logging.getLogger("job_hunter.scheduling")

# Day-of-week mapping for APScheduler
_DOW_MAP = {
    "mon": "mon", "tue": "tue", "wed": "wed", "thu": "thu",
    "fri": "fri", "sat": "sat", "sun": "sun",
}

JOB_ID = "scheduled_pipeline"


class PipelineScheduler:
    """Wraps APScheduler to run pipeline jobs on a cron schedule.

    Integrates with the FastAPI app's TaskManager so only one task
    runs at a time.
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._config: ScheduleConfig = ScheduleConfig()
        self._app_state: Any = None  # set by wire()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def wire(self, app_state: Any) -> None:
        """Bind to FastAPI app.state so the trigger can access engine, settings, etc."""
        self._app_state = app_state

    def start(self, config: ScheduleConfig) -> None:
        """Start the scheduler with the given config."""
        self._config = config
        if config.enabled:
            self._add_job(config)
        self._scheduler.start()
        logger.info(
            "Scheduler started (enabled=%s, time=%s, days=%s, mode=%s)",
            config.enabled, config.time_of_day,
            ",".join(config.days_of_week), config.pipeline_mode.value,
        )

    def stop(self) -> None:
        """Shut down the scheduler."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    def reschedule(self, config: ScheduleConfig) -> None:
        """Update schedule config live — add, remove or reschedule the job."""
        self._config = config

        # Remove existing job if present
        if self._scheduler.get_job(JOB_ID):
            self._scheduler.remove_job(JOB_ID)

        if config.enabled:
            self._add_job(config)
            logger.info(
                "Scheduler rescheduled: %s on %s (%s)",
                config.time_of_day, ",".join(config.days_of_week),
                config.pipeline_mode.value,
            )
        else:
            logger.info("Scheduler disabled")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @property
    def config(self) -> ScheduleConfig:
        return self._config

    @property
    def running(self) -> bool:
        return self._scheduler.running

    def get_next_run_time(self) -> str | None:
        """Return ISO string of next scheduled run, or None."""
        job = self._scheduler.get_job(JOB_ID)
        if job and job.next_run_time:
            return job.next_run_time.isoformat()
        return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _add_job(self, config: ScheduleConfig) -> None:
        """Register the cron job with APScheduler."""
        hour, minute = config.time_of_day.split(":")
        day_of_week = ",".join(
            _DOW_MAP.get(d.lower().strip(), d) for d in config.days_of_week
        )
        trigger = CronTrigger(
            day_of_week=day_of_week,
            hour=int(hour),
            minute=int(minute),
        )
        self._scheduler.add_job(
            self._trigger_pipeline,
            trigger,
            id=JOB_ID,
            replace_existing=True,
        )

    async def _trigger_pipeline(self) -> None:
        """Called by APScheduler at the scheduled time."""
        app_state = self._app_state
        if app_state is None:
            logger.error("Scheduler not wired to app state — skipping run")
            return

        from job_hunter.web.task_manager import TaskManager
        tm: TaskManager = app_state.task_manager

        if tm.is_running:
            logger.warning("Scheduled run skipped — a task is already running")
            return

        config = self._config
        settings = app_state.settings
        engine = app_state.engine

        logger.info(
            "⏰ Scheduled pipeline starting: mode=%s, profile=%s",
            config.pipeline_mode.value, config.profile_name,
        )

        started_at = datetime.now(timezone.utc).isoformat()
        summary: dict[str, Any] = {}
        error = ""

        try:
            coro = self._build_pipeline_coro(config, settings, engine)
            # Start via TaskManager so SSE subscribers see progress
            tm.start_task(f"scheduled_{config.pipeline_mode.value}", coro)
            # Wait for completion
            if tm._task:
                await tm._task
            summary = tm._result or {}
        except Exception as exc:
            error = str(exc)
            logger.error("Scheduled run failed: %s", exc)

        # Record history
        record = ScheduleRunRecord(
            started_at=started_at,
            completed_at=datetime.now(timezone.utc).isoformat(),
            mode=config.pipeline_mode.value,
            summary=summary,
            error=error,
        )
        try:
            from job_hunter.config.loader import append_schedule_history
            history_path = settings.data_dir / "schedule_history.yml"
            append_schedule_history(record, history_path)
        except Exception as exc:
            logger.error("Failed to save schedule history: %s", exc)

        # Send email notification
        try:
            from job_hunter.notifications.email import (
                build_notifier_from_settings,
                send_pipeline_summary,
            )
            notifier = build_notifier_from_settings(settings)
            if notifier:
                result_summary = summary.copy()
                if error:
                    result_summary["error"] = error
                send_pipeline_summary(
                    notifier, result_summary, config.pipeline_mode.value,
                )
        except Exception as exc:
            logger.error("Failed to send notification email: %s", exc)

    async def _build_pipeline_coro(
        self, config: ScheduleConfig, settings: Any, engine: Any,
    ) -> Any:
        """Build the appropriate pipeline coroutine based on mode."""
        from job_hunter.web.routers.run import _load_run_params

        if config.pipeline_mode == PipelineMode.MARKET:
            return await self._run_market(settings, engine)
        else:
            return await self._run_application_pipeline(config, settings, engine)

    async def _run_application_pipeline(
        self, config: ScheduleConfig, settings: Any, engine: Any,
    ) -> dict[str, Any]:
        """Run discover / discover+score / full pipeline."""
        from job_hunter.web.routers.run import _load_run_params
        from job_hunter.orchestration.pipeline import run_pipeline

        params = _load_run_params(settings)
        params["profile_name"] = config.profile_name

        if config.pipeline_mode == PipelineMode.DISCOVER:
            # Discover only — call run_pipeline with apply disabled
            params["dry_run"] = True
            result = await run_pipeline(**params, settings=settings)
            return result if isinstance(result, dict) else {"result": "ok"}

        elif config.pipeline_mode == PipelineMode.DISCOVER_SCORE:
            params["dry_run"] = True
            result = await run_pipeline(**params, settings=settings)
            return result if isinstance(result, dict) else {"result": "ok"}

        else:  # FULL
            result = await run_pipeline(**params, settings=settings)
            return result if isinstance(result, dict) else {"result": "ok"}

    async def _run_market(self, settings: Any, engine: Any) -> dict[str, Any]:
        """Run the full market intelligence pipeline."""
        from job_hunter.config.loader import load_user_profile
        from job_hunter.db.repo import make_session
        from job_hunter.market.extract import HeuristicExtractor, OpenAIMarketExtractor
        from job_hunter.market.pipeline import run_market_pipeline
        from job_hunter.market.title_normalizer import (
            HeuristicTitleNormalizer,
            OpenAITitleNormalizer,
        )

        if settings.openai_api_key:
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

        user_profile = None
        profile_path = settings.data_dir / "user_profile.yml"
        if profile_path.exists():
            try:
                user_profile = load_user_profile(profile_path)
            except Exception:
                pass

        session = make_session(engine)
        try:
            summary = await asyncio.to_thread(
                run_market_pipeline, session,
                extractor=extractor, profile=user_profile,
                candidate_key="default", title_normalizer=title_norm,
            )
            return summary
        finally:
            session.close()

