"""Settings router — view/edit runtime configuration."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Request, UploadFile, File
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from job_hunter.web.deps import get_db

logger = logging.getLogger("job_hunter.web.settings")

router = APIRouter(tags=["settings"])


class SettingsUpdate(BaseModel):
    mock: bool | None = None
    dry_run: bool | None = None
    headless: bool | None = None
    slowmo_ms: int | None = None
    log_level: str | None = None
    openai_api_key: str | None = None
    llm_provider: str | None = None
    local_llm_url: str | None = None
    local_llm_model: str | None = None
    llm_temperature: float | None = None
    llm_max_tokens: int | None = None
    # Email notification settings
    email_provider: str | None = None
    resend_api_key: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool | None = None
    notification_email: str | None = None
    notifications_enabled: bool | None = None


@router.get("/settings")
async def settings_page(request: Request):
    templates = request.app.state.templates
    from job_hunter.web.deps import get_effective_settings
    settings = get_effective_settings(request)
    return templates.TemplateResponse(request, "settings.html", {
        "settings": settings,
        "api_key_masked": _mask_key(settings.openai_api_key),
        "smtp_password_masked": _mask_key(settings.smtp_password),
        "resend_api_key_masked": _mask_key(settings.resend_api_key),
    })


@router.get("/api/settings")
async def get_settings_api(request: Request):
    from job_hunter.web.deps import get_effective_settings
    s = get_effective_settings(request)
    return {
        "mock": s.mock,
        "dry_run": s.dry_run,
        "headless": s.headless,
        "slowmo_ms": s.slowmo_ms,
        "log_level": s.log_level.value,
        "data_dir": str(s.data_dir),
        "llm_provider": s.llm_provider,
        "local_llm_url": s.local_llm_url,
        "local_llm_model": s.local_llm_model,
        "openai_api_key": _mask_key(s.openai_api_key),
        "email_provider": s.email_provider,
        "resend_api_key": _mask_key(s.resend_api_key),
        "smtp_host": s.smtp_host,
        "smtp_port": s.smtp_port,
        "smtp_user": s.smtp_user,
        "notification_email": s.notification_email,
        "notifications_enabled": s.notifications_enabled,
    }


@router.put("/api/settings")
async def update_settings_api(body: SettingsUpdate, request: Request, session: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)

    # Collect non-None updates
    updates: dict = {}
    if body.mock is not None:
        updates["mock"] = body.mock
    if body.dry_run is not None:
        updates["dry_run"] = body.dry_run
    if body.headless is not None:
        updates["headless"] = body.headless
    if body.slowmo_ms is not None:
        updates["slowmo_ms"] = body.slowmo_ms
    if body.log_level is not None:
        from job_hunter.config.models import LogLevel
        updates["log_level"] = body.log_level  # store as string for User row
    if body.openai_api_key and body.openai_api_key.strip():
        updates["openai_api_key"] = body.openai_api_key.strip()
    if body.llm_provider is not None:
        updates["llm_provider"] = body.llm_provider
    if body.local_llm_url is not None:
        updates["local_llm_url"] = body.local_llm_url
    if body.local_llm_model is not None:
        updates["local_llm_model"] = body.local_llm_model
    if body.llm_temperature is not None:
        updates["llm_temperature"] = body.llm_temperature
    if body.llm_max_tokens is not None:
        updates["llm_max_tokens"] = body.llm_max_tokens
    if body.email_provider is not None:
        updates["email_provider"] = body.email_provider
    if body.resend_api_key and body.resend_api_key.strip():
        updates["resend_api_key"] = body.resend_api_key.strip()
    if body.smtp_host is not None:
        updates["smtp_host"] = body.smtp_host
    if body.smtp_port is not None:
        updates["smtp_port"] = body.smtp_port
    if body.smtp_user is not None:
        updates["smtp_user"] = body.smtp_user
    if body.smtp_password and body.smtp_password.strip():
        updates["smtp_password"] = body.smtp_password.strip()
    if body.smtp_use_tls is not None:
        updates["smtp_use_tls"] = body.smtp_use_tls
    if body.notification_email is not None:
        updates["notification_email"] = body.notification_email
    if body.notifications_enabled is not None:
        updates["notifications_enabled"] = body.notifications_enabled


    if user is not None:
        # Write per-user settings to the User row
        from job_hunter.auth.repo import update_user_settings
        update_user_settings(session, user.id, **updates)
    else:
        # Fallback: mutate global settings (legacy / CLI)
        s = request.app.state.settings
        for k, v in updates.items():
            if k == "log_level":
                from job_hunter.config.models import LogLevel
                s.log_level = LogLevel(v)
            elif hasattr(s, k):
                setattr(s, k, v)
        from job_hunter.config.loader import save_settings_env
        dotenv_path = getattr(request.app.state, "dotenv_path", None)
        save_settings_env(s, dotenv_path)


    return {"updated": True}


@router.post("/api/settings/test-email")
async def test_email(request: Request):
    """Send a test email to verify notification configuration."""
    from job_hunter.notifications.email import (
        build_notifier_from_settings,
        send_test_email,
    )
    from job_hunter.web.deps import get_effective_settings

    s = get_effective_settings(request)

    if not s.notification_email:
        return JSONResponse(
            {"error": "Notification recipient email must be set."},
            status_code=400,
        )

    # Temporarily force enabled so build_notifier works for the test
    orig_enabled = s.notifications_enabled
    s.notifications_enabled = True
    try:
        notifier = build_notifier_from_settings(s)
    finally:
        s.notifications_enabled = orig_enabled

    if notifier is None:
        provider = s.email_provider
        if provider == "resend":
            return JSONResponse(
                {"error": "Resend API key is not configured."},
                status_code=400,
            )
        return JSONResponse(
            {"error": "SMTP host is not configured."},
            status_code=400,
        )

    ok = send_test_email(notifier)
    if ok:
        return {"sent": True}
    detail = getattr(notifier, "last_error", "") or "Failed to send. Check settings."
    return JSONResponse({"error": detail}, status_code=500)


@router.get("/api/settings/llm-status")
async def llm_status(request: Request):
    """Check whether the local LLM sidecar is reachable and return model info."""
    import asyncio as _aio
    from urllib.request import urlopen, Request as UrlRequest
    from urllib.error import URLError

    from job_hunter.web.deps import get_effective_settings
    s = get_effective_settings(request)
    url = (s.local_llm_url or "http://localhost:8080/v1").rstrip("/")
    models_url = f"{url}/models"

    def _check():
        try:
            req = UrlRequest(models_url, method="GET")
            with urlopen(req, timeout=5) as resp:
                import json as _json
                data = _json.loads(resp.read())
                models = [m.get("id", "?") for m in data.get("data", [])]
                return {"online": True, "url": url, "models": models}
        except (URLError, OSError, Exception) as exc:
            return {"online": False, "url": url, "error": str(exc)}

    return await _aio.to_thread(_check)


# ---------------------------------------------------------------------------
# LLM container management (Docker sidecar control from web UI)
# ---------------------------------------------------------------------------


@router.get("/api/settings/llm-container")
async def llm_container_status(request: Request):
    """Return the Docker container status for the LLM sidecar."""
    import asyncio as _aio
    from job_hunter.web.docker_ctl import container_status
    return await _aio.to_thread(container_status)


@router.post("/api/settings/llm-container/start")
async def llm_container_start(request: Request):
    """Start the LLM sidecar container."""
    import asyncio as _aio
    from job_hunter.web.docker_ctl import start_container
    result = await _aio.to_thread(start_container)
    if not result.get("ok"):
        return JSONResponse(result, status_code=500)
    return result


@router.post("/api/settings/llm-container/stop")
async def llm_container_stop(request: Request):
    """Stop the LLM sidecar container."""
    import asyncio as _aio
    from job_hunter.web.docker_ctl import stop_container
    result = await _aio.to_thread(stop_container)
    if not result.get("ok"):
        return JSONResponse(result, status_code=500)
    return result


@router.post("/api/settings/llm-container/restart")
async def llm_container_restart(request: Request):
    """Restart the LLM sidecar container."""
    import asyncio as _aio
    from job_hunter.web.docker_ctl import restart_container
    result = await _aio.to_thread(restart_container)
    if not result.get("ok"):
        return JSONResponse(result, status_code=500)
    return result


def _mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return "not set" if not key else "****"
    return f"****{key[-4:]}"


def _cookies_path(request: Request):
    """Return the path to the global LinkedIn cookies file."""
    from job_hunter.web.deps import get_effective_settings
    s = get_effective_settings(request)
    return s.data_dir / "cookies.json"


@router.get("/api/settings/cookies-status")
async def cookies_status(request: Request):
    """Check whether LinkedIn cookies exist."""
    path = _cookies_path(request)
    if path.exists() and path.stat().st_size > 10:
        import datetime
        mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime)
        return {"exists": True, "updated": mtime.strftime("%Y-%m-%d %H:%M")}
    return {"exists": False}


@router.post("/api/settings/cookies")
async def upload_cookies(request: Request, file: UploadFile = File(...)):
    """Upload a LinkedIn cookies JSON file."""
    content = await file.read()
    if len(content) > 2 * 1024 * 1024:
        return JSONResponse({"error": "File too large (max 2 MB)"}, status_code=400)
    try:
        cookies = json.loads(content)
        if not isinstance(cookies, list):
            raise ValueError("Expected a JSON array of cookie objects")
    except (json.JSONDecodeError, ValueError) as exc:
        return JSONResponse({"error": f"Invalid cookies JSON: {exc}"}, status_code=400)

    path = _cookies_path(request)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
    return {"uploaded": True, "count": len(cookies)}


@router.delete("/api/settings/cookies")
async def delete_cookies(request: Request):
    """Delete the LinkedIn cookies file."""
    path = _cookies_path(request)
    if path.exists():
        path.unlink()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# LinkedIn browser login (primary — opens a real browser window)
# ---------------------------------------------------------------------------


@router.post("/api/settings/linkedin-browser-login")
async def linkedin_browser_login(request: Request):
    """Open a real browser window for the user to log into LinkedIn.

    Launches a headed Chromium browser with stealth patches.  The user logs
    in normally (handles 2FA, CAPTCHAs, etc. themselves).  Once the ``li_at``
    cookie appears, it's saved automatically and the browser closes.

    **Only works when the server runs locally** — the browser window opens
    on the machine where the server is running.
    """
    from job_hunter.web.task_manager import TaskManager

    tm: TaskManager = request.app.state.task_manager
    if tm.is_running:
        return JSONResponse({"error": "A task is already running"}, status_code=409)

    from job_hunter.web.deps import get_effective_settings
    settings = get_effective_settings(request)
    cookies_path = settings.data_dir / "cookies.json"

    from job_hunter.linkedin.session import LinkedInSession
    session = LinkedInSession(cookies_path=cookies_path)

    async def _run() -> dict:
        await session.login(headless=False, timeout_ms=180_000)
        return {"status": "success"}

    tm.start_task("linkedin-browser-login", _run())
    return JSONResponse({"started": "linkedin-browser-login"}, status_code=202)


# ---------------------------------------------------------------------------
# LinkedIn cookie paste (fallback — user copies li_at from browser)
# ---------------------------------------------------------------------------


class LiAtPasteRequest(BaseModel):
    li_at: str


@router.post("/api/settings/cookies-paste")
async def paste_li_at_cookie(body: LiAtPasteRequest, request: Request):
    """Save a LinkedIn session from a pasted ``li_at`` cookie value.

    This is the simplest authentication method: the user copies the ``li_at``
    cookie value from their browser's DevTools and pastes it here.  We construct
    a Playwright-compatible cookies JSON file from just that one value — it's
    the only cookie LinkedIn needs for authenticated API/page access.
    """
    value = body.li_at.strip()
    if not value:
        return JSONResponse({"error": "Cookie value cannot be empty"}, status_code=400)
    if len(value) < 10:
        return JSONResponse(
            {"error": "Value looks too short to be a valid li_at cookie"},
            status_code=400,
        )

    cookies = [
        {
            "name": "li_at",
            "value": value,
            "domain": ".linkedin.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "None",
        },
    ]

    path = _cookies_path(request)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
    return {"saved": True}


# ---------------------------------------------------------------------------
# Auto-extract li_at from installed browsers
# ---------------------------------------------------------------------------


def _save_li_at_to_file(request: Request, value: str) -> None:
    """Persist a ``li_at`` value as a Playwright-format cookies.json."""
    cookies = [
        {
            "name": "li_at",
            "value": value,
            "domain": ".linkedin.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "None",
        },
    ]
    path = _cookies_path(request)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")


# ── Strategy 1: browser_cookie3 (Firefox — Chrome/Edge fail on v127+)


def _try_extract_firefox() -> tuple[str | None, str | None]:
    """Try reading ``li_at`` from Firefox via ``browser_cookie3``.

    Returns ``(value, "Firefox")`` on success, ``(None, error_msg)`` on failure.
    """
    try:
        import browser_cookie3
    except ImportError:
        return None, "Firefox: browser_cookie3 not installed"

    try:
        cj = browser_cookie3.firefox(domain_name=".linkedin.com")
        for cookie in cj:
            if cookie.name == "li_at" and cookie.value:
                return cookie.value, "Firefox"
        return None, "Firefox: no li_at cookie (not logged in?)"
    except Exception as exc:
        return None, f"Firefox: {exc}"


# ── Strategy 2: Copy Chrome/Edge cookie DB to a temp dir, then launch the
#    real browser binary headless to decrypt via CDP.
#
#    Key insight: Chrome's own binary can always decrypt its app-bound cookies.
#    By copying the Cookies DB + Local State to a temp profile we avoid any
#    file-lock conflict with the running browser instance.


import os
import shutil
import sys
import tempfile
from pathlib import Path


def _find_chromium_browsers() -> list[tuple[str, Path, Path]]:
    """Find Chrome/Edge installations on Windows.

    Returns ``[(display_name, binary_path, user_data_dir), ...]``.
    """
    if sys.platform != "win32":
        return []

    local = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")

    candidates = [
        (
            "Chrome",
            [
                Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(pf86) / "Google" / "Chrome" / "Application" / "chrome.exe",
            ],
            Path(local) / "Google" / "Chrome" / "User Data",
        ),
        (
            "Edge",
            [
                Path(pf) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(pf86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            ],
            Path(local) / "Microsoft" / "Edge" / "User Data",
        ),
    ]

    results: list[tuple[str, Path, Path]] = []
    for name, binaries, user_data in candidates:
        for b in binaries:
            if b.exists() and user_data.exists():
                results.append((name, b, user_data))
                break
    return results


def _copy_cookie_profile(user_data_dir: Path, tmp_dir: Path) -> None:
    """Copy the minimum files Chrome needs to load cookies into *tmp_dir*.

    Copies:
    * ``Local State``  — contains the (encrypted) cookie-decryption key.
    * ``Default/Network/Cookies`` — the SQLite cookie database.

    Uses **SQLite's backup API** with ``immutable=1`` to read the Cookies
    database even while Chrome holds an exclusive lock on it.
    """
    import sqlite3

    # 1. Encryption key (regular JSON file — read raw bytes to bypass locks)
    ls = user_data_dir / "Local State"
    if ls.exists():
        try:
            data = ls.read_bytes()
            (tmp_dir / "Local State").write_bytes(data)
        except OSError:
            pass  # non-fatal: Chrome may still decrypt without it

    # 2. Cookie database — use SQLite backup to bypass Chrome's file lock.
    #    The ``immutable=1`` URI flag tells SQLite to skip all file locking,
    #    which lets us read the DB even though Chrome has it exclusively open.
    for rel in (
        Path("Default") / "Network" / "Cookies",
        Path("Default") / "Cookies",
    ):
        src = user_data_dir / rel
        if src.exists():
            dst_dir = tmp_dir / rel.parent
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / src.name

            src_uri = "file:///" + src.resolve().as_posix() + "?immutable=1"
            src_conn = sqlite3.connect(src_uri, uri=True)
            dst_conn = sqlite3.connect(str(dst))
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
                src_conn.close()
            return

    raise FileNotFoundError("Cookies database not found in profile")


async def _cdp_extract_cookie(binary: Path, user_data_dir: Path) -> str | None:
    """Copy the cookie DB to a temp profile, launch the browser headless,
    and read ``li_at`` via CDP.

    The browser's own binary decrypts the app-bound-encrypted cookies
    transparently — no third-party decryption library needed.
    """
    import asyncio as _aio

    tmp_dir = Path(tempfile.mkdtemp(prefix="aijh_cookie_"))
    try:
        _copy_cookie_profile(user_data_dir, tmp_dir)

        proc = await _aio.create_subprocess_exec(
            str(binary),
            f"--user-data-dir={tmp_dir}",
            "--remote-debugging-port=0",
            "--headless=new",
            "--no-first-run",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            stdout=_aio.subprocess.DEVNULL,
            stderr=_aio.subprocess.DEVNULL,
        )

        try:
            # Wait for DevToolsActivePort (contains the debug port number)
            port_file = tmp_dir / "DevToolsActivePort"
            port: int | None = None
            for _ in range(100):  # up to 10 s
                await _aio.sleep(0.1)
                if port_file.exists():
                    try:
                        content = port_file.read_text().strip()
                        port = int(content.split("\n")[0])
                        break
                    except (ValueError, IndexError):
                        continue

            if port is None:
                raise RuntimeError("Browser did not expose a debug port in time")

            # Connect via Playwright CDP and read cookies
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(
                    f"http://localhost:{port}",
                    timeout=10_000,
                )
                try:
                    contexts = browser.contexts
                    if not contexts:
                        return None
                    cookies = await contexts[0].cookies(
                        ["https://www.linkedin.com"],
                    )
                    for c in cookies:
                        if c["name"] == "li_at" and c.get("value"):
                            return c["value"]
                    return None
                finally:
                    try:
                        await browser.close()
                    except Exception:
                        pass
        finally:
            try:
                proc.terminate()
                await _aio.wait_for(proc.wait(), timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def _try_extract_via_cdp() -> tuple[str | None, str | None]:
    """Attempt CDP-based cookie extraction from Chrome or Edge.

    Works **while the browser is running** — we copy the cookie database to
    a temporary profile and launch a separate headless instance.

    Returns ``(value, browser_name)`` on success, ``(None, error_msg)``
    on failure.
    """
    browsers = _find_chromium_browsers()
    if not browsers:
        return None, "No Chrome or Edge installation found"

    errors: list[str] = []
    for name, binary, user_data_dir in browsers:
        try:
            value = await _cdp_extract_cookie(binary, user_data_dir)
            if value:
                return value, name
            errors.append(f"{name}: no li_at cookie found (not logged in?)")
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    return None, "; ".join(errors) if errors else "No browsers available"


@router.post("/api/settings/cookies-extract")
async def extract_cookies_from_browser(request: Request):
    """Auto-extract the ``li_at`` cookie from an installed browser.

    **Strategy:**

    1. Try **Firefox** via ``browser_cookie3`` (quick, always works).
    2. Try **Chrome / Edge** via CDP — copies the cookie database to a
       temporary profile and launches the real browser binary headless.
       Chrome decrypts its own app-bound cookies transparently.  Works
       **while the browser is open** (no need to close it).
    """
    import asyncio as _aio

    # 1. Quick attempt: Firefox via browser_cookie3
    value, ff_info = await _aio.to_thread(_try_extract_firefox)
    if value:
        _save_li_at_to_file(request, value)
        return {"saved": True, "browser": ff_info}

    # 2. Chrome / Edge via CDP (copy DB → headless launch → extract)
    value, cdp_info = await _try_extract_via_cdp()
    if value:
        _save_li_at_to_file(request, value)
        return {"saved": True, "browser": cdp_info}

    # Both failed — build a helpful composite error
    parts = []
    if ff_info:
        parts.append(ff_info)
    if cdp_info:
        parts.append(cdp_info)
    detail = " · ".join(parts) or "No browsers found"
    return JSONResponse(
        {"error": detail},
        status_code=404,
    )



@router.get("/api/settings/extension-zip")
async def download_extension_zip(request: Request):
    """Serve the Chrome extension as a downloadable ZIP file."""
    import io
    import zipfile
    from pathlib import Path

    from fastapi.responses import StreamingResponse

    ext_dir = Path(__file__).resolve().parent.parent / "static" / "extension"
    if not ext_dir.is_dir():
        return JSONResponse({"error": "Extension files not found"}, status_code=404)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in sorted(ext_dir.iterdir()):
            if fpath.is_file():
                zf.write(fpath, f"ai-job-hunter-extension/{fpath.name}")
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=ai-job-hunter-extension.zip",
        },
    )


# ---------------------------------------------------------------------------
# LinkedIn remote login (programmatic, for headless / Docker environments)
# ---------------------------------------------------------------------------


class LinkedInLoginRequest(BaseModel):
    email: str
    password: str


class LinkedInVerifyRequest(BaseModel):
    code: str


class LinkedInClickRequest(BaseModel):
    x: float = 0
    y: float = 0
    cancel: bool = False


@router.post("/api/settings/linkedin-login")
async def linkedin_login(body: LinkedInLoginRequest, request: Request):
    """Start a programmatic LinkedIn login as a background task.

    The browser runs headless on the server.  Progress is streamed via SSE
    on ``/api/run/status``.  If LinkedIn requests a verification code the
    task pauses and the client should call ``POST /api/settings/linkedin-verify``
    with the code.  If a visual challenge (CAPTCHA) appears, the user can
    click on the streamed screenshot via ``POST /api/settings/linkedin-click``.
    """
    from job_hunter.web.task_manager import TaskManager

    tm: TaskManager = request.app.state.task_manager
    if tm.is_running:
        return JSONResponse({"error": "A task is already running"}, status_code=409)

    from job_hunter.web.deps import get_effective_settings
    settings = get_effective_settings(request)
    cookies_path = settings.data_dir / "cookies.json"
    screenshot_dir = settings.data_dir

    # Create a Future the login task will await when verification is needed.
    loop = asyncio.get_running_loop()
    verify_future: asyncio.Future[str] = loop.create_future()
    # Stash on app.state so the /linkedin-verify endpoint can resolve it.
    request.app.state._linkedin_verify_future = verify_future

    # Create a Queue for remote click coordinates (CAPTCHA interaction).
    click_queue: asyncio.Queue[dict | None] = asyncio.Queue()
    request.app.state._linkedin_click_queue = click_queue

    from job_hunter.linkedin.session import LinkedInSession

    session = LinkedInSession(cookies_path=cookies_path)

    async def _run() -> dict:
        def _get_code() -> asyncio.Future[str]:
            """Return the Future; remote_login will ``await`` it."""
            return verify_future

        async def _get_click() -> dict | None:
            """Await the next click from the web UI."""
            return await click_queue.get()

        try:
            result = await session.remote_login(
                email=body.email,
                password=body.password,
                headless=True,
                get_verification_code=_get_code,
                get_remote_click=_get_click,
                screenshot_dir=screenshot_dir,
            )
        finally:
            # Clean up
            request.app.state._linkedin_verify_future = None
            request.app.state._linkedin_click_queue = None
        return result

    tm.start_task("linkedin-login", _run())
    return JSONResponse({"started": "linkedin-login"}, status_code=202)


@router.post("/api/settings/linkedin-verify")
async def linkedin_verify(body: LinkedInVerifyRequest, request: Request):
    """Submit a LinkedIn verification code to the running login task.

    Only valid while a ``linkedin-login`` task is in progress and waiting
    for a verification code.
    """
    future: asyncio.Future[str] | None = getattr(
        request.app.state, "_linkedin_verify_future", None,
    )
    if future is None or future.done():
        return JSONResponse(
            {"error": "No login task is waiting for a verification code."},
            status_code=400,
        )
    future.set_result(body.code.strip())
    return {"submitted": True}


@router.post("/api/settings/linkedin-click")
async def linkedin_click(body: LinkedInClickRequest, request: Request):
    """Send a click to the running remote-interaction loop.

    The frontend translates image click coordinates to the 1280×900 viewport
    and POSTs them here.  The login task picks them up from the queue and
    executes the click via Playwright.

    Send ``{"cancel": true}`` to stop the interaction loop.
    """
    queue: asyncio.Queue | None = getattr(
        request.app.state, "_linkedin_click_queue", None,
    )
    if queue is None:
        return JSONResponse(
            {"error": "No login task is waiting for remote clicks."},
            status_code=400,
        )
    if body.cancel:
        await queue.put(None)
        return {"submitted": True, "action": "cancel"}
    await queue.put({"x": body.x, "y": body.y})
    return {"submitted": True, "action": "click", "x": body.x, "y": body.y}


@router.get("/api/settings/checkpoint-screenshot")
async def checkpoint_screenshot(request: Request, name: str = "checkpoint_initial"):
    """Serve a login checkpoint screenshot taken by the remote-login task.

    Query params:
        name — screenshot filename without path (e.g. ``checkpoint_initial.png``)
    """
    from fastapi.responses import FileResponse

    from job_hunter.web.deps import get_effective_settings
    settings = get_effective_settings(request)

    # Sanitise: only allow simple filenames, must end with .png
    safe_name = name if name.endswith(".png") else f"{name}.png"
    if "/" in safe_name or "\\" in safe_name or ".." in safe_name:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)

    path = settings.data_dir / safe_name
    if not path.exists():
        return JSONResponse({"error": "No screenshot available"}, status_code=404)
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )

