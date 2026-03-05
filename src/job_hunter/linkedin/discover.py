"""LinkedIn job discovery — navigate search results and collect job cards."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from job_hunter.linkedin import selectors as sel
from job_hunter.linkedin.parse import parse_job_card, parse_job_cards, parse_job_detail
from job_hunter.utils.hashing import job_hash

logger = logging.getLogger("job_hunter.linkedin.discover")


async def discover_jobs(
    *,
    profile_name: str,
    mock: bool = False,
    headless: bool = True,
    slowmo_ms: int = 0,
    cookies_path: str | Path = "data/cookies.json",
    keywords: list[str] | None = None,
    location: str = "",
    remote: bool = False,
    seniority: list[str] | None = None,
    max_pages: int = 3,
) -> list[dict[str, Any]]:
    """Discover jobs matching the given profile.

    When *mock* is ``True`` the discovery navigates a local HTTP server
    serving the HTML fixtures.  Otherwise it navigates real LinkedIn
    using saved cookies.

    Returns a list of dicts, each containing all fields needed to create
    a ``Job`` DB row.
    """
    if mock:
        return await _discover_mock(headless=headless, slowmo_ms=slowmo_ms)

    return await _discover_real(
        headless=headless,
        slowmo_ms=slowmo_ms,
        cookies_path=cookies_path,
        keywords=keywords or [],
        location=location,
        remote=remote,
        seniority=seniority,
        max_pages=max_pages,
    )


async def _discover_real(
    *,
    headless: bool = True,
    slowmo_ms: int = 0,
    cookies_path: str | Path = "data/cookies.json",
    keywords: list[str],
    location: str = "",
    remote: bool = False,
    seniority: list[str] | None = None,
    max_pages: int = 3,
) -> list[dict[str, Any]]:
    """Run discovery against real LinkedIn using saved cookies."""
    from playwright.async_api import async_playwright

    from job_hunter.linkedin.forms import detect_challenge
    from job_hunter.linkedin.session import LinkedInSession, build_search_url
    from job_hunter.utils.rate_limit import RateLimiter

    session = LinkedInSession(cookies_path=cookies_path)
    if not session.has_cookies():
        raise FileNotFoundError(
            f"No cookies found at {cookies_path}. Run 'hunt login' first."
        )

    rate = RateLimiter(min_delay_ms=2000, max_delay_ms=5000)
    jobs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless, slow_mo=slowmo_ms)
        context = await session.create_context(browser)
        page = await context.new_page()

        try:
            for page_num in range(max_pages):
                search_url = build_search_url(
                    keywords=keywords,
                    location=location,
                    remote=remote,
                    seniority=seniority,
                    page=page_num,
                )
                logger.info("Navigating to search page %d: %s", page_num + 1, search_url)
                await rate.wait()
                await page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)

                # Wait for content to load
                await page.wait_for_timeout(2000)

                # Check for challenge
                if await detect_challenge(page):
                    logger.warning("Challenge detected on search page! Stopping discovery.")
                    break

                # Parse job cards from the page
                list_html = await page.content()
                cards = parse_job_cards(list_html)
                logger.info("Page %d: found %d job cards", page_num + 1, len(cards))

                if not cards:
                    logger.info("No more job cards found. Stopping pagination.")
                    break

                # Visit each card's detail page
                for card in cards:
                    ext_id = card.get("external_id", "")
                    if not ext_id or ext_id in seen_ids:
                        continue
                    seen_ids.add(ext_id)

                    card_url = card.get("url", "")
                    if not card_url:
                        continue

                    # Build full URL
                    if card_url.startswith("/"):
                        detail_url = f"https://www.linkedin.com{card_url}"
                    else:
                        detail_url = card_url

                    await rate.wait()
                    logger.debug("Fetching detail page: %s", detail_url)

                    try:
                        await page.goto(detail_url, wait_until="domcontentloaded", timeout=15_000)
                        await page.wait_for_timeout(1500)

                        if await detect_challenge(page):
                            logger.warning("Challenge detected on detail page! Stopping.")
                            return jobs

                        detail_html = await page.content()
                        detail = parse_job_detail(detail_html)
                    except Exception as exc:
                        logger.warning("Failed to fetch detail for %s: %s", ext_id, exc)
                        detail = {}

                    job_data: dict[str, Any] = {
                        "external_id": ext_id,
                        "url": detail_url,
                        "title": detail.get("title") or card.get("title", ""),
                        "company": detail.get("company") or card.get("company", ""),
                        "location": card.get("location", ""),
                        "description_text": detail.get("description_text", ""),
                        "easy_apply": detail.get("easy_apply", False),
                        "source": "linkedin",
                        "hash": job_hash(
                            external_id=ext_id,
                            title=detail.get("title") or card.get("title", ""),
                            company=detail.get("company") or card.get("company", ""),
                        ),
                    }
                    jobs.append(job_data)
                    logger.debug("Discovered: %s at %s", job_data["title"], job_data["company"])

        finally:
            await context.close()
            await browser.close()

    logger.info("Real discovery complete: %d jobs found", len(jobs))
    return jobs


async def _discover_mock(*, headless: bool = True, slowmo_ms: int = 0) -> list[dict[str, Any]]:
    """Run discovery against the mock LinkedIn server using Playwright."""
    from playwright.async_api import async_playwright

    from job_hunter.linkedin.mock_site import MockLinkedInServer

    server = MockLinkedInServer()
    base_url = server.start()

    jobs: list[dict[str, Any]] = []

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=headless, slow_mo=slowmo_ms)
            page = await browser.new_page()

            # 1. Navigate to the job list page
            list_url = f"{base_url}/jobs/search"
            logger.info("Navigating to %s", list_url)
            await page.goto(list_url, wait_until="domcontentloaded")

            # 2. Get the full page HTML and parse all cards
            list_html = await page.content()
            cards = parse_job_cards(list_html)
            logger.info("Found %d job cards", len(cards))

            # 3. Visit each card's detail page
            for card in cards:
                card_url = card.get("url", "")
                if not card_url:
                    continue

                detail_url = f"{base_url}{card_url}"
                logger.debug("Fetching detail page: %s", detail_url)
                await page.goto(detail_url, wait_until="domcontentloaded")
                detail_html = await page.content()

                detail = parse_job_detail(detail_html)

                job_data: dict[str, Any] = {
                    "external_id": card["external_id"],
                    "url": card_url,
                    "title": detail.get("title") or card.get("title", ""),
                    "company": detail.get("company") or card.get("company", ""),
                    "location": card.get("location", ""),
                    "description_text": detail.get("description_text", ""),
                    "easy_apply": detail.get("easy_apply", False),
                    "source": "linkedin",
                    "hash": job_hash(
                        external_id=card["external_id"],
                        title=detail.get("title") or card.get("title", ""),
                        company=detail.get("company") or card.get("company", ""),
                    ),
                }
                jobs.append(job_data)
                logger.debug("Discovered job: %s at %s", job_data["title"], job_data["company"])

            await browser.close()

    finally:
        server.stop()

    logger.info("Discovery complete: %d jobs found", len(jobs))
    return jobs

