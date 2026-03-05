"""LinkedIn job discovery — navigate search results and collect job cards."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from job_hunter.linkedin.parse import parse_job_cards, parse_job_detail
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


async def _extract_jobs_via_js(page: Any) -> list[dict[str, Any]]:
    """Extract job data from LinkedIn search page using JavaScript.

    LinkedIn renders job cards with data attributes and links that contain
    job IDs. This is more robust than CSS selectors since it reads the
    actual DOM structure regardless of class names.
    """
    js_code = """
    () => {
        const jobs = [];
        // Strategy 1: Find all links that look like job URLs
        const links = document.querySelectorAll('a[href*="/jobs/view/"]');
        const seen = new Set();
        for (const a of links) {
            const href = a.getAttribute('href') || '';
            const match = href.match(/\\/jobs\\/view\\/(\\d+)/);
            if (!match) continue;
            const jobId = match[1];
            if (seen.has(jobId)) continue;
            seen.add(jobId);

            // Walk up to find the card container (usually a <li> or <div>)
            let card = a.closest('li') || a.closest('div[data-job-id]') || a.parentElement;

            // Extract title from the link text
            let title = a.innerText.trim();
            // Sometimes the link wraps a <span> or <strong>
            if (!title) {
                const inner = a.querySelector('span, strong');
                title = inner ? inner.innerText.trim() : '';
            }

            // Extract company — look for nearby text elements
            let company = '';
            let location = '';
            if (card) {
                // Try common subtitle patterns
                const subtitles = card.querySelectorAll(
                    '.artdeco-entity-lockup__subtitle, ' +
                    '.job-card-container__primary-description, ' +
                    'a[data-control-name="company_link"], ' +
                    '[class*="company"], [class*="subtitle"]'
                );
                for (const el of subtitles) {
                    const text = el.innerText.trim();
                    if (text && !company) { company = text; break; }
                }

                // Try location / caption
                const captions = card.querySelectorAll(
                    '.artdeco-entity-lockup__caption, ' +
                    '.job-card-container__metadata-item, ' +
                    '[class*="location"], [class*="caption"], [class*="metadata"]'
                );
                for (const el of captions) {
                    const text = el.innerText.trim();
                    if (text && !location) { location = text; break; }
                }

                // Fallback: get all text spans in the card and guess
                if (!company || !location) {
                    const spans = card.querySelectorAll('span');
                    const texts = [];
                    for (const s of spans) {
                        const t = s.innerText.trim();
                        if (t && t !== title && t.length > 1 && t.length < 100) texts.push(t);
                    }
                    if (!company && texts.length > 0) company = texts[0];
                    if (!location && texts.length > 1) location = texts[1];
                }
            }

            jobs.push({
                external_id: jobId,
                title: title,
                company: company,
                location: location,
                url: '/jobs/view/' + jobId + '/',
            });
        }

        // Strategy 2: Check for data-job-id attributes
        if (jobs.length === 0) {
            const cards = document.querySelectorAll('[data-job-id], [data-occludable-job-id]');
            for (const card of cards) {
                const jobId = card.getAttribute('data-job-id') ||
                              card.getAttribute('data-occludable-job-id') || '';
                if (!jobId || seen.has(jobId)) continue;
                seen.add(jobId);

                const titleEl = card.querySelector('a') || card.querySelector('[class*="title"]');
                const title = titleEl ? titleEl.innerText.trim() : '';

                jobs.push({
                    external_id: jobId,
                    title: title,
                    company: '',
                    location: '',
                    url: '/jobs/view/' + jobId + '/',
                });
            }
        }

        return jobs;
    }
    """
    try:
        result = await page.evaluate(js_code)
        return result if isinstance(result, list) else []
    except Exception as exc:
        logger.warning("JS extraction failed: %s", exc)
        return []


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
        # Prefer real Chrome (channel="chrome") — harder for LinkedIn to fingerprint
        # than Playwright's bundled Chromium
        try:
            browser = await pw.chromium.launch(
                headless=headless, slow_mo=slowmo_ms, channel="chrome",
            )
            logger.info("Launched Chrome (channel=chrome)")
        except Exception:
            browser = await pw.chromium.launch(
                headless=headless, slow_mo=slowmo_ms,
            )
            logger.info("Launched Chromium (default)")
        context = await session.create_context(browser)
        page = await context.new_page()

        try:
            # Step 0: Verify cookies are valid by visiting the feed
            logger.info("Verifying LinkedIn login status…")
            await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(3000)
            current_url = page.url
            if "/login" in current_url or "/checkpoint" in current_url or "authwall" in current_url:
                logger.error(
                    "Cookies are expired or invalid — redirected to %s. "
                    "Run 'hunt login' to re-authenticate.", current_url
                )
                # Save updated cookies anyway in case some were refreshed
                new_cookies = await context.cookies()
                session.save_cookies(new_cookies)
                raise RuntimeError(
                    f"LinkedIn cookies expired — redirected to {current_url}. "
                    f"Run 'hunt login' to re-authenticate."
                )

            # Check if we see the feed (page key should not be guest)
            page_html = await page.content()
            if "d_jobs_guest" in page_html or "public_jobs" in page_html:
                logger.warning("LinkedIn is serving guest pages despite cookies. Re-login required.")
                raise RuntimeError(
                    "LinkedIn is serving guest pages despite cookies. "
                    "Cookies may be expired. Run 'hunt login' to re-authenticate."
                )

            logger.info("Login verified — session is active.")
            # Save refreshed cookies
            new_cookies = await context.cookies()
            session.save_cookies(new_cookies)

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

                # Use networkidle to wait for React/JS to finish rendering
                await page.goto(search_url, wait_until="networkidle", timeout=45_000)

                # Wait for either job cards to appear or a timeout
                try:
                    await page.wait_for_selector(
                        'a[href*="/jobs/view/"], div.job-card-container, '
                        'li.jobs-search-results__list-item, '
                        '.scaffold-layout__list-item',
                        timeout=15_000,
                    )
                    logger.debug("Job elements detected on page")
                except Exception:
                    logger.debug("No job elements after 15s, trying scroll…")

                # Scroll to trigger lazy-loaded job cards
                for scroll_pct in [0.3, 0.5, 0.7, 1.0]:
                    await page.evaluate(
                        f"window.scrollTo(0, document.body.scrollHeight * {scroll_pct})"
                    )
                    await page.wait_for_timeout(1500)

                # Check for challenge
                if await detect_challenge(page):
                    logger.warning("Challenge detected on search page! Stopping discovery.")
                    break

                # Check if we got redirected to login
                current_url = page.url
                if "/login" in current_url or "/checkpoint" in current_url:
                    logger.warning("Redirected to login/checkpoint: %s. Cookies may be expired.", current_url)
                    raise RuntimeError(
                        f"Redirected to {current_url} during search. "
                        f"Cookies expired mid-session. Run 'hunt login'."
                    )

                # Parse job cards from the page
                list_html = await page.content()

                # Check if we got a guest page instead of logged-in results
                if "d_jobs_guest" in list_html or "public_jobs_nav" in list_html:
                    logger.warning("Search page served as guest despite login verification.")
                    debug_path = Path(cookies_path).parent / "debug_search_page.html"
                    debug_path.write_text(list_html[:50000], encoding="utf-8")
                    logger.info("Saved debug HTML to %s", debug_path)
                    raise RuntimeError(
                        "LinkedIn served a guest page for job search. "
                        "Your session may have been invalidated. Run 'hunt login'. "
                        f"Debug HTML saved to {debug_path}"
                    )

                cards = parse_job_cards(list_html)
                logger.info("Page %d: found %d job cards via CSS selectors", page_num + 1, len(cards))

                # Fallback: try JS-based extraction if CSS selectors found nothing
                if not cards:
                    logger.info("CSS selectors matched nothing. Trying JS-based extraction…")
                    cards = await _extract_jobs_via_js(page)
                    logger.info("JS extraction found %d job cards", len(cards))

                if not cards:
                    # Log page title and a snippet for debugging
                    title = await page.title()
                    logger.warning(
                        "No job cards found on page %d. Page title: '%s', URL: %s. "
                        "LinkedIn may have changed their DOM structure.",
                        page_num + 1, title, page.url,
                    )
                    # Save debug HTML for inspection
                    debug_path = Path(cookies_path).parent / "debug_search_page.html"
                    debug_path.write_text(list_html[:50000], encoding="utf-8")
                    logger.info("Saved debug HTML to %s", debug_path)
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
                        await page.goto(detail_url, wait_until="networkidle", timeout=20_000)
                        await page.wait_for_timeout(2000)

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

