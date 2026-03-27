"""FastAPI application factory for the AI Job Hunter web GUI."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse, Response
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
        # Resolve the .env path for settings persistence
        if not hasattr(app.state, "dotenv_path") or app.state.dotenv_path is None:
            app.state.dotenv_path = Path(".env").resolve()
        if not hasattr(app.state, "task_manager") or app.state.task_manager is None:
            app.state.task_manager = TaskManager()
        app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

        # ── Secret key for JWT auth ──
        from job_hunter.auth.security import _ensure_secret
        secret = _ensure_secret(settings.secret_key)
        if not settings.secret_key:
            # Persist so it survives restarts
            settings.secret_key = secret
            try:
                from job_hunter.config.loader import save_settings_env
                dotenv_path = getattr(app.state, "dotenv_path", None)
                save_settings_env(settings, dotenv_path)
            except Exception:
                pass
        app.state.secret_key = secret

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

        # Global template variables
        from datetime import datetime as _dt_cls
        from job_hunter import __version__ as _app_version
        app.state.templates.env.globals["current_year"] = _dt_cls.now().year
        app.state.templates.env.globals["app_version"] = _app_version

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

        # Start scheduler if enabled
        scheduler = None
        try:
            from job_hunter.config.loader import load_schedule
            from job_hunter.scheduling.scheduler import PipelineScheduler

            schedule_config = load_schedule(settings.data_dir / "schedule.yml")
            scheduler = PipelineScheduler()
            scheduler.wire(app.state)
            scheduler.start(schedule_config)
        except Exception:
            pass  # Degrade gracefully in tests or if APScheduler not available
        app.state.scheduler = scheduler

        yield
        # Shutdown
        if scheduler:
            try:
                scheduler.stop()
            except Exception:
                pass
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
    from job_hunter.web.routers import account as account_router
    from job_hunter.web.routers import admin as admin_router
    from job_hunter.web.routers import auth as auth_router
    from job_hunter.web.routers import dashboard, jobs, onboarding, profiles, reports, resume_review, run, settings as settings_router, schedule
    from job_hunter.market.web.router import router as market_router

    # ── Login-required middleware ──
    # Public paths that don't require authentication
    _PUBLIC_PREFIXES = (
        "/login", "/register", "/api/auth/", "/api/health",
        "/static/", "/favicon.ico",
    )

    @app.middleware("http")
    async def login_required_middleware(request, call_next):
        path = request.url.path
        # Allow public paths through
        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)
        # Check for auth token
        from job_hunter.web.deps import get_current_user_optional
        user = get_current_user_optional(request)
        if user is None:
            # For API calls, return 401 JSON; for pages, redirect to login
            if path.startswith("/api/"):
                from fastapi.responses import JSONResponse as _JR
                return _JR({"detail": "Not authenticated"}, status_code=401)
            return RedirectResponse(url="/login", status_code=302)
        # Attach user to request state for downstream use
        request.state.user = user
        return await call_next(request)

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        ico_path = STATIC_DIR / "favicon.ico"
        if ico_path.exists():
            return FileResponse(str(ico_path), media_type="image/x-icon")
        return Response(status_code=204)

    @app.get("/api/health")
    async def health():
        """Health check endpoint for Docker / monitoring."""
        scheduler_ok = False
        try:
            sched = getattr(app.state, "scheduler", None)
            scheduler_ok = sched is not None and sched.running
        except Exception:
            pass
        db_ok = False
        try:
            from sqlalchemy import text
            from job_hunter.db.repo import make_session
            session = make_session(app.state.engine)
            session.execute(text("SELECT 1"))
            session.close()
            db_ok = True
        except Exception:
            pass
        from job_hunter import __version__
        return {
            "status": "ok",
            "version": __version__,
            "db_ok": db_ok,
            "scheduler_running": scheduler_ok,
        }

    app.include_router(auth_router.router)
    app.include_router(account_router.router)
    app.include_router(admin_router.router)
    app.include_router(dashboard.router)
    app.include_router(onboarding.router)
    app.include_router(jobs.router)
    app.include_router(profiles.router)
    app.include_router(run.router)
    app.include_router(reports.router)
    app.include_router(resume_review.router)
    app.include_router(settings_router.router)
    app.include_router(schedule.router)
    app.include_router(market_router)

    return app

