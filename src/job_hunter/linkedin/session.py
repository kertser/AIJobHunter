"""LinkedIn session management — cookie-based authentication."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("job_hunter.linkedin.session")

LINKEDIN_BASE = "https://www.linkedin.com"
LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"
LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"

# Stealth init-script shared by all browser contexts
_STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => false });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    window.chrome = { runtime: {} };
"""


class LinkedInSession:
    """Manages a Playwright browser context with saved LinkedIn cookies.

    Usage::

        session = LinkedInSession(cookies_path="data/cookies.json")

        # First time: manual login
        await session.login(headless=False)

        # Subsequent runs: reuse saved cookies
        context = await session.create_context(playwright_browser)
    """

    def __init__(self, cookies_path: str | Path = "data/cookies.json") -> None:
        self.cookies_path = Path(cookies_path)

    def has_cookies(self) -> bool:
        """Return True if saved cookies exist on disk."""
        return self.cookies_path.exists() and self.cookies_path.stat().st_size > 10

    async def login(
        self,
        *,
        headless: bool = False,
        slowmo_ms: int = 100,
        timeout_ms: int = 120_000,
    ) -> None:
        """Open a browser for manual LinkedIn login, then save cookies.

        The browser opens in non-headless mode so the user can type their
        credentials and solve any challenges.  Once the feed page loads
        (indicating a successful login), cookies are automatically saved.
        """
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(
                    headless=headless, slow_mo=slowmo_ms, channel="chrome",
                )
            except Exception:
                browser = await pw.chromium.launch(headless=headless, slow_mo=slowmo_ms)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            page = await context.new_page()

            logger.info("Navigating to LinkedIn login page…")
            await page.goto(LINKEDIN_LOGIN_URL, wait_until="domcontentloaded")

            # Wait for the user to log in — detected by navigation to the feed
            logger.info("Please log in manually. Waiting up to %ds…", timeout_ms // 1000)
            try:
                await page.wait_for_url(
                    f"{LINKEDIN_BASE}/feed/**",
                    timeout=timeout_ms,
                )
            except Exception:
                # Also accept other post-login pages
                current = page.url
                if "linkedin.com" in current and "/login" not in current:
                    logger.info("Detected post-login page: %s", current)
                else:
                    raise TimeoutError(
                        f"Login not detected within {timeout_ms // 1000}s. "
                        f"Current URL: {current}"
                    )

            logger.info("Login detected! Saving cookies…")
            await self._save_cookies_from_context(context)
            await browser.close()

        logger.info("Cookies saved to %s", self.cookies_path)

    async def _save_cookies_from_context(self, context: Any) -> None:
        """Extract cookies from a browser context and save to disk."""
        cookies = await context.cookies()
        self.cookies_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cookies_path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2)
        logger.debug("Saved %d cookies", len(cookies))

    def load_cookies(self) -> list[dict[str, Any]]:
        """Load cookies from disk."""
        if not self.has_cookies():
            raise FileNotFoundError(
                f"No cookies found at {self.cookies_path}. "
                "Upload cookies via Settings in the web UI, or run 'hunt login' locally."
            )
        with open(self.cookies_path, encoding="utf-8") as f:
            cookies: list[dict[str, Any]] = json.load(f)
        logger.debug("Loaded %d cookies from %s", len(cookies), self.cookies_path)
        return cookies

    def save_cookies(self, cookies: list[dict[str, Any]]) -> None:
        """Save cookies to disk."""
        self.cookies_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cookies_path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2)

    # ------------------------------------------------------------------
    # Remote (programmatic) login
    # ------------------------------------------------------------------

    async def remote_login(
        self,
        *,
        email: str,
        password: str,
        headless: bool = True,
        slowmo_ms: int = 300,
        on_progress: Callable[[str], None] | None = None,
        get_verification_code: Callable[[], Any] | None = None,
    ) -> dict[str, str]:
        """Programmatic login for headless / remote-server environments.

        1. Navigates to the login page and fills credentials.
        2. If LinkedIn presents a verification/checkpoint page and
           *get_verification_code* is provided, it awaits the callable
           (which should return the PIN as a string) and enters it.
        3. On success, saves cookies and returns ``{"status": "success"}``.
        4. On verification needed without a callback, returns
           ``{"status": "verification_needed", "hint": "..."}``.
        5. On failure returns ``{"status": "error", "detail": "..."}``.

        *on_progress* is called with human-readable status messages (suitable
        for SSE streaming).
        """
        from playwright.async_api import async_playwright

        def _progress(msg: str) -> None:
            logger.info(msg)
            if on_progress:
                on_progress(msg)

        _progress("Launching headless browser…")

        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(
                    headless=headless, slow_mo=slowmo_ms, channel="chrome",
                )
            except Exception:
                browser = await pw.chromium.launch(headless=headless, slow_mo=slowmo_ms)

            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            await context.add_init_script(_STEALTH_SCRIPT)
            page = await context.new_page()

            try:
                result = await self._do_remote_login(
                    page, context,
                    email=email,
                    password=password,
                    progress=_progress,
                    get_verification_code=get_verification_code,
                )
            finally:
                await browser.close()

        return result

    async def _do_remote_login(
        self,
        page: Any,
        context: Any,
        *,
        email: str,
        password: str,
        progress: Callable[[str], None],
        get_verification_code: Callable[[], Any] | None,
    ) -> dict[str, str]:
        """Core login logic (extracted for readability)."""

        progress("Navigating to LinkedIn login page…")
        await page.goto(LINKEDIN_LOGIN_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

        # Fill credentials
        progress("Entering credentials…")
        email_input = page.locator('input#username, input[name="session_key"]')
        pwd_input = page.locator('input#password, input[name="session_password"]')

        if await email_input.count() == 0 or await pwd_input.count() == 0:
            return {"status": "error", "detail": "Login form not found — LinkedIn may have changed its layout."}

        await email_input.first.fill(email)
        await pwd_input.first.fill(password)

        # Click sign-in
        progress("Submitting login form…")
        submit = page.locator('button[type="submit"], button[data-litms-control-urn*="login-submit"]')
        if await submit.count() > 0:
            await submit.first.click()
        else:
            await pwd_input.first.press("Enter")

        # Wait for navigation
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
        await page.wait_for_timeout(2000)

        # Check result
        url = page.url

        # --- SUCCESS: landed on feed / main pages ---
        if self._is_logged_in(url):
            progress("Login successful! Saving cookies…")
            await self._save_cookies_from_context(context)
            progress(f"✅ Saved cookies to {self.cookies_path}")
            return {"status": "success"}

        # --- WRONG CREDENTIALS ---
        error_el = page.locator(
            '#error-for-password, div[role="alert"], '
            'div.form__label--error, p.form__label--error'
        )
        if await error_el.count() > 0:
            try:
                txt = (await error_el.first.text_content() or "").strip()
            except Exception:
                txt = ""
            detail = txt or "Incorrect email or password."
            progress(f"❌ Login failed: {detail}")
            return {"status": "error", "detail": detail}

        # --- VERIFICATION / CHECKPOINT ---
        if self._is_checkpoint(url):
            progress("LinkedIn is requesting a security check…")

            # Dump page text for diagnostics
            page_text = await self._get_visible_text(page)
            progress(f"Page content: {page_text[:500]}")

            # Step 1: Try to trigger code delivery — LinkedIn checkpoint
            # pages often require clicking a "Send code" / "Submit" button
            # BEFORE any code is actually sent to the user.
            sent = await self._try_trigger_code_send(page, progress)

            # Re-read URL — clicking may have navigated
            url = page.url
            if self._is_logged_in(url):
                progress("Login successful after checkpoint! Saving cookies…")
                await self._save_cookies_from_context(context)
                progress(f"✅ Saved cookies to {self.cookies_path}")
                return {"status": "success"}

            # Step 2: Extract a human-readable hint from the (possibly updated) page
            hint = await self._get_checkpoint_hint(page)
            if sent:
                progress(f"Code requested — check your email / phone. ({hint})")
            else:
                progress(f"Verification required: {hint}")

            # Step 3: Wait for the user to supply the code
            if get_verification_code is not None:
                progress("Waiting for verification code…")
                code = get_verification_code()
                if asyncio.isfuture(code) or asyncio.iscoroutine(code):
                    code = await code
                code = str(code).strip()
                if not code:
                    return {"status": "verification_needed", "hint": hint}

                return await self._submit_verification(page, context, code, progress)
            else:
                return {"status": "verification_needed", "hint": hint}

        # --- UNKNOWN STATE ---
        page_text = await self._get_visible_text(page)
        progress(f"Unexpected post-login page: {url}")
        progress(f"Page content: {page_text[:400]}")
        return {"status": "error", "detail": f"Unexpected page after login: {url}"}

    async def _submit_verification(
        self,
        page: Any,
        context: Any,
        code: str,
        progress: Callable[[str], None],
    ) -> dict[str, str]:
        """Enter a verification PIN and check result."""
        progress(f"Entering verification code ({len(code)} chars)…")

        pin_input = page.locator(
            'input#input__email_verification_pin, '
            'input[name="pin"], '
            'input#input__phone_verification_pin, '
            'input#input__challenge_response'
        )
        if await pin_input.count() == 0:
            return {"status": "error", "detail": "Could not find verification code input field."}

        await pin_input.first.fill(code)

        submit = page.locator('button#email-pin-submit-button, button[type="submit"]')
        if await submit.count() > 0:
            await submit.first.click()
        else:
            await pin_input.first.press("Enter")

        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
        await page.wait_for_timeout(2000)

        url = page.url
        if self._is_logged_in(url):
            progress("Verification accepted! Saving cookies…")
            await self._save_cookies_from_context(context)
            progress(f"✅ Saved cookies to {self.cookies_path}")
            return {"status": "success"}

        # Check for error on the verification page (wrong code)
        error_el = page.locator('div[role="alert"], p.form__label--error, div.body__banner--error')
        if await error_el.count() > 0:
            try:
                txt = (await error_el.first.text_content() or "").strip()
            except Exception:
                txt = ""
            detail = txt or "Verification code was incorrect."
            progress(f"❌ Verification failed: {detail}")
            return {"status": "error", "detail": detail}

        progress(f"Unexpected page after verification: {url}")
        return {"status": "error", "detail": f"Unexpected page after verification: {url}"}

    @staticmethod
    def _is_logged_in(url: str) -> bool:
        """Check if the URL indicates a successful login."""
        logged_in_paths = ("/feed", "/jobs", "/messaging", "/mynetwork", "/notifications")
        return any(f"linkedin.com{p}" in url for p in logged_in_paths)

    @staticmethod
    def _is_checkpoint(url: str) -> bool:
        return any(kw in url for kw in (
            "/checkpoint", "/challenge", "/authwall",
            "/uas/login-submit", "/uas/consumer-email-challenge",
        ))

    @staticmethod
    async def _get_visible_text(page: Any) -> str:
        """Return the visible text of the page body (for diagnostics)."""
        try:
            text = await page.locator("body").inner_text()
            # Collapse whitespace
            import re
            return re.sub(r"\s+", " ", text).strip()
        except Exception:
            return "(could not read page text)"

    @staticmethod
    async def _try_trigger_code_send(page: Any, progress: Callable[[str], None]) -> bool:
        """Click a 'Send code' / 'Submit' button on checkpoint pages.

        LinkedIn checkpoint pages often show a form where the user must
        *choose* a verification method and click a button before any code
        is sent.  This method detects common patterns and clicks for the
        user so the code is actually delivered.

        Returns True if a trigger button was found and clicked.
        """
        # Pattern 1: explicit "Send code" / "Submit" buttons on challenge page
        send_selectors = [
            # Email / phone challenge "Submit" button
            'button#email-pin-submit-button',
            'form#email-pin-challenge button[type="submit"]',
            'form#phone-pin-challenge button[type="submit"]',
            # Generic challenge form submit
            'form.challenge button[type="submit"]',
            'form[action*="challenge"] button[type="submit"]',
            # Newer checkpoint UI
            'button[data-litms-control-urn*="checkpoint-challenge-submit"]',
            # "Verify" / "Send" / "Submit" by text
            'button:has-text("Send")',
            'button:has-text("Verify")',
            'button:has-text("Submit")',
        ]
        for sel in send_selectors:
            try:
                btn = page.locator(sel)
                if await btn.count() > 0 and await btn.first.is_visible():
                    label = (await btn.first.text_content() or "").strip()
                    progress(f"Clicking '{label or 'Submit'}' to trigger code delivery…")
                    await btn.first.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=10_000)
                    await page.wait_for_timeout(2000)
                    return True
            except Exception:
                continue

        # Pattern 2: radio-button method selector (email vs phone vs app)
        # If present, select the first option and submit the enclosing form.
        radios = page.locator(
            'input[type="radio"][name="challengeType"], '
            'input[type="radio"][name="verificationMethod"]'
        )
        try:
            if await radios.count() > 0:
                await radios.first.check()
                progress("Selected first verification method and submitting…")
                form_submit = page.locator(
                    'form button[type="submit"], form input[type="submit"]'
                )
                if await form_submit.count() > 0:
                    await form_submit.first.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=10_000)
                    await page.wait_for_timeout(2000)
                    return True
        except Exception:
            pass

        progress("No 'Send code' button found — LinkedIn may be waiting for manual action.")
        return False

    @staticmethod
    async def _get_checkpoint_hint(page: Any) -> str:
        """Extract a human-readable hint about what LinkedIn is asking."""
        # Try several selectors for description text, broadest last
        selectors = [
            "h1",
            "p.challenge-description",
            "p.rt-checkpoint__message",
            "p.content__primary",
            "div.body__content p",
            "main p",
        ]
        parts: list[str] = []
        for sel in selectors:
            try:
                els = page.locator(sel)
                count = await els.count()
                for i in range(min(count, 3)):
                    txt = (await els.nth(i).text_content() or "").strip()
                    if txt and txt not in parts and len(txt) > 5:
                        parts.append(txt)
            except Exception:
                continue
            if parts:
                break

        if parts:
            return " | ".join(parts)[:500]

        return (
            "LinkedIn requires verification but the page content could not be read. "
            "Check the log above for the raw page text."
        )

    async def create_context(
        self,
        browser: Any,
    ) -> Any:
        """Create an authenticated browser context using saved cookies.

        Returns a Playwright BrowserContext with cookies pre-loaded.
        """
        cookies = self.load_cookies()

        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )

        # Stealth: mask automation indicators so LinkedIn doesn't serve guest pages
        await context.add_init_script(_STEALTH_SCRIPT)

        await context.add_cookies(cookies)
        logger.info("Browser context created with %d cookies", len(cookies))
        return context


def build_search_url(
    *,
    keywords: list[str],
    location: str = "",
    remote: bool = False,
    seniority: list[str] | None = None,
    page: int = 0,
) -> str:
    """Build a LinkedIn job search URL from profile parameters.

    Returns a URL like:
    https://www.linkedin.com/jobs/search/?keywords=Python+Developer&location=Remote&...
    """
    from urllib.parse import quote_plus, urlencode

    params: dict[str, str] = {}

    if keywords:
        params["keywords"] = " ".join(keywords)

    if location:
        params["location"] = location

    if remote:
        params["f_WT"] = "2"  # LinkedIn filter for remote work

    # Seniority levels mapping
    seniority_map = {
        "internship": "1",
        "entry": "2",
        "entry level": "2",
        "associate": "3",
        "mid-senior": "4",
        "mid-senior level": "4",
        "senior": "4",
        "director": "5",
        "executive": "6",
    }
    if seniority:
        codes = []
        for s in seniority:
            code = seniority_map.get(s.lower())
            if code and code not in codes:
                codes.append(code)
        if codes:
            params["f_E"] = ",".join(codes)

    # Easy Apply filter
    params["f_AL"] = "true"

    # Pagination
    if page > 0:
        params["start"] = str(page * 25)

    base = "https://www.linkedin.com/jobs/search/"
    return f"{base}?{urlencode(params, quote_via=quote_plus)}"

