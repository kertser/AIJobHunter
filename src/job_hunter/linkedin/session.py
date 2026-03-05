"""LinkedIn session management — cookie-based authentication."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("job_hunter.linkedin.session")

LINKEDIN_BASE = "https://www.linkedin.com"
LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"
LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"


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
            raise FileNotFoundError(f"No cookies found at {self.cookies_path}")
        with open(self.cookies_path, encoding="utf-8") as f:
            cookies: list[dict[str, Any]] = json.load(f)
        logger.debug("Loaded %d cookies from %s", len(cookies), self.cookies_path)
        return cookies

    def save_cookies(self, cookies: list[dict[str, Any]]) -> None:
        """Save cookies to disk."""
        self.cookies_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cookies_path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2)

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
        await context.add_init_script("""
            // Override navigator.webdriver — Playwright sets it to true
            Object.defineProperty(navigator, 'webdriver', { get: () => false });

            // Mask automation-related properties
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });

            // Chrome runtime stub
            window.chrome = { runtime: {} };
        """)

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

