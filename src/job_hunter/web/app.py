"""FastAPI application factory for the AI Job Hunter web GUI."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from job_hunter.config.models import AppSettings
from job_hunter.db.repo import get_engine, init_db
from job_hunter.utils.logging import setup_logging
from job_hunter.web.task_manager import TaskManager

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


def create_app(settings: AppSettings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""
    if settings is None:
        from job_hunter.config.loader import load_settings
        settings = load_settings()

    # Ensure logging is configured so all job_hunter.* loggers emit at INFO+
    setup_logging(settings.log_level.value if hasattr(settings.log_level, 'value') else str(settings.log_level))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup — use pre-injected engine if available (for tests)
        if not hasattr(app.state, "engine") or app.state.engine is None:
            engine = get_engine(settings.data_dir)
            init_db(engine)
            app.state.engine = engine
            owns_engine = True
        else:
            owns_engine = False
        app.state.settings = settings
        if not hasattr(app.state, "task_manager") or app.state.task_manager is None:
            app.state.task_manager = TaskManager()
        app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

        # Register custom Jinja2 filters
        import markupsafe
        try:
            import markdown as _md

            def _md_filter(text: str) -> markupsafe.Markup:
                """Convert Markdown text to safe HTML."""
                if not text:
                    return markupsafe.Markup("")
                html = _md.markdown(text, extensions=["nl2br", "sane_lists"])
                return markupsafe.Markup(html)
        except ImportError:
            def _md_filter(text: str) -> markupsafe.Markup:
                """Fallback: escape and preserve whitespace."""
                import html as _html
                escaped = _html.escape(text or "")
                return markupsafe.Markup(f"<pre style='white-space:pre-wrap'>{escaped}</pre>")

        app.state.templates.env.filters["markdown"] = _md_filter

        # Date formatting filter
        from datetime import datetime as _dt
        def _datefmt(val, fmt: str = "%b %d, %Y") -> str:
            """Format a datetime as a short date string, or '—' for None."""
            if val is None:
                return "—"
            if isinstance(val, _dt):
                return val.strftime(fmt)
            return str(val)

        app.state.templates.env.filters["datefmt"] = _datefmt

        yield
        # Shutdown
        if owns_engine:
            app.state.engine.dispose()

    app = FastAPI(
        title="AI Job Hunter",
        description="Web GUI for AI Job Hunter",
        lifespan=lifespan,
    )

    # Static files
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Import and include routers
    from job_hunter.web.routers import dashboard, jobs, onboarding, profiles, reports, resume_review, run, settings as settings_router
    from job_hunter.market.web.router import router as market_router

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        ico_path = STATIC_DIR / "favicon.ico"
        if ico_path.exists():
            return FileResponse(str(ico_path), media_type="image/x-icon")
        return Response(status_code=204)

    app.include_router(dashboard.router)
    app.include_router(onboarding.router)
    app.include_router(jobs.router)
    app.include_router(profiles.router)
    app.include_router(run.router)
    app.include_router(reports.router)
    app.include_router(resume_review.router)
    app.include_router(settings_router.router)
    app.include_router(market_router)

    return app

