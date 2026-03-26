"""Schedule router — view/edit automated pipeline schedule."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(tags=["schedule"])


class ScheduleUpdate(BaseModel):
    enabled: bool = False
    time_of_day: str = "09:00"
    days_of_week: list[str] = []
    pipeline_mode: str = "full"
    profile_name: str = "default"


@router.get("/schedule")
async def schedule_page(request: Request):
    templates = request.app.state.templates
    scheduler = getattr(request.app.state, "scheduler", None)

    from job_hunter.config.loader import load_schedule, load_schedule_history
    settings = request.app.state.settings

    config = scheduler.config if scheduler else load_schedule(settings.data_dir / "schedule.yml")
    next_run = scheduler.get_next_run_time() if scheduler else None
    history = load_schedule_history(settings.data_dir / "schedule_history.yml")
    history.reverse()  # newest first

    return templates.TemplateResponse(request, "schedule.html", {
        "config": config,
        "next_run": next_run,
        "history": history[:20],
    })


@router.get("/api/schedule")
async def get_schedule(request: Request):
    scheduler = getattr(request.app.state, "scheduler", None)
    settings = request.app.state.settings

    from job_hunter.config.loader import load_schedule, load_schedule_history
    config = scheduler.config if scheduler else load_schedule(settings.data_dir / "schedule.yml")
    next_run = scheduler.get_next_run_time() if scheduler else None
    history = load_schedule_history(settings.data_dir / "schedule_history.yml")

    return {
        "config": config.model_dump(),
        "next_run": next_run,
        "history": [r.model_dump() for r in history[-10:]],
    }


@router.put("/api/schedule")
async def update_schedule(body: ScheduleUpdate, request: Request):
    from job_hunter.config.loader import save_schedule
    from job_hunter.config.models import PipelineMode, ScheduleConfig

    config = ScheduleConfig(
        enabled=body.enabled,
        time_of_day=body.time_of_day,
        days_of_week=body.days_of_week,
        pipeline_mode=PipelineMode(body.pipeline_mode),
        profile_name=body.profile_name,
    )

    settings = request.app.state.settings
    save_schedule(config, settings.data_dir / "schedule.yml")

    scheduler = getattr(request.app.state, "scheduler", None)
    next_run = None
    if scheduler:
        scheduler.reschedule(config)
        next_run = scheduler.get_next_run_time()

    return {"updated": True, "next_run": next_run}


@router.post("/api/schedule/trigger")
async def trigger_now(request: Request):
    """Manually trigger the scheduled pipeline right now."""
    scheduler = getattr(request.app.state, "scheduler", None)
    if not scheduler:
        return JSONResponse({"error": "Scheduler not available"}, status_code=503)

    tm = request.app.state.task_manager

    if tm.is_running:
        return JSONResponse({"error": "A task is already running"}, status_code=409)

    if not scheduler.config.enabled:
        return JSONResponse(
            {"error": "Schedule is disabled. Enable it first."}, status_code=400,
        )

    # Fire the trigger directly
    import asyncio
    asyncio.create_task(scheduler._trigger_pipeline())
    return JSONResponse({"started": True}, status_code=202)

