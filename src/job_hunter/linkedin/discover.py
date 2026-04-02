"""LinkedIn job discovery — navigate search results and collect job cards."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from job_hunter.linkedin.parse import parse_job_cards, parse_job_detail
from job_hunter.matching.description_cleaner import clean_description_llm, clean_description_rules, looks_llm_formatted
from job_hunter.utils.hashing import job_hash

logger = logging.getLogger("job_hunter.linkedin.discover")


def _parse_relative_date(text: str) -> datetime | None:
    """Parse a relative date string like '2 weeks ago' into a UTC datetime.

    Also handles ISO format strings from <time datetime="..."> attributes.
    Returns None if the text cannot be parsed.
    """
    if not text:
        return None

    # Try ISO format first (from <time datetime="...">)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        pass

    # Parse relative dates: "X unit(s) ago"
    m = re.search(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", text, re.IGNORECASE)
    if not m:
        return None

    amount = int(m.group(1))
    unit = m.group(2).lower()
    now = datetime.now(timezone.utc)

    if unit == "second":
        return now - timedelta(seconds=amount)
    elif unit == "minute":
        return now - timedelta(minutes=amount)
    elif unit == "hour":
        return now - timedelta(hours=amount)
    elif unit == "day":
        return now - timedelta(days=amount)
    elif unit == "week":
        return now - timedelta(weeks=amount)
    elif unit == "month":
        return now - timedelta(days=amount * 30)
    elif unit == "year":
        return now - timedelta(days=amount * 365)
    return None


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
    max_pages: int = 10,
    openai_api_key: str = "",
    captcha_handler: Any | None = None,
    settings: Any | None = None,
) -> list[dict[str, Any]]:
    """Discover jobs matching the given profile.

    When *mock* is ``True`` the discovery navigates a local HTTP server
    serving the HTML fixtures.  Otherwise it navigates real LinkedIn
    using saved cookies.

    *captcha_handler* is an optional async callable ``(page, screenshot_dir) → bool``
    that is invoked when a CAPTCHA/challenge blocks discovery.  It should
    solve the challenge interactively (e.g. via screenshot streaming + remote
    clicks) and return ``True`` if the challenge was cleared.  When ``None``,
    the original error-raising behaviour is preserved.

    *settings* is an optional ``AppSettings`` instance.  When provided it is
    forwarded to the description cleaner so that ``llm_provider="local"``
    is respected.

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
        openai_api_key=openai_api_key,
        captcha_handler=captcha_handler,
        settings=settings,
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


async def _expand_all_show_more(page: Any) -> None:
    """Click all 'Show more' / '… more' / 'See more' buttons on the page.

    LinkedIn truncates job descriptions with a "… more" or "Show more" link.
    This expands them all so the full text is available for extraction.
    """
    try:
        # Use JS to find and click all expandable elements
        clicked = await page.evaluate("""
            () => {
                let count = 0;
                // Strategy 1: buttons/links with "show more" / "see more" text
                const allClickable = document.querySelectorAll(
                    'button, a, span[role="button"], [class*="show-more"]'
                );
                for (const el of allClickable) {
                    const text = el.innerText.trim().toLowerCase();
                    if (text === 'show more' || text === '… more' || text === '...more'
                        || text === 'see more' || text === '\u2026 more'
                        || text === 'more' || text.endsWith('… more')) {
                        el.click();
                        count++;
                    }
                }
                // Strategy 2: LinkedIn's specific show-more-less button
                const showMoreBtns = document.querySelectorAll(
                    'button.show-more-less-html__button, ' +
                    'button[aria-label="Show more"], ' +
                    'button[aria-label="show more"], ' +
                    'a.show-more-less-html__button--more, ' +
                    'footer button[aria-label*="more"]'
                );
                for (const btn of showMoreBtns) {
                    if (!btn.classList.contains('show-more-less-html__button--less')) {
                        btn.click();
                        count++;
                    }
                }
                return count;
            }
        """)
        if clicked:
            logger.debug("Clicked %d 'show more' buttons", clicked)
            # Wait for content to expand
            await page.wait_for_timeout(1500)
    except Exception as exc:
        logger.debug("Failed to expand show-more: %s", exc)


async def _extract_detail_via_js(page: Any) -> dict[str, Any]:
    """Extract job detail data from a LinkedIn job detail page using JavaScript.

    LinkedIn's 2026 job detail pages use obfuscated CSS class names (hash-based
    CSS modules) and React SPA rendering. Traditional CSS selectors break
    constantly, so this uses:
    - <title> tag parsing for title + company
    - data-testid attributes for description
    - Text boundary extraction as fallback
    - Full-page text scan for Easy Apply
    """
    js_code = """
    () => {
        const result = {title: '', company: '', description_text: '', easy_apply: false, posted_at_text: ''};

        // === TITLE + COMPANY from <title> tag ===
        // LinkedIn format: "Job Title | Company Name | LinkedIn"
        const pageTitle = document.title || '';
        const titleParts = pageTitle.split('|').map(s => s.trim());
        if (titleParts.length >= 3) {
            result.title = titleParts[0];
            result.company = titleParts[1];
        } else if (titleParts.length === 2) {
            result.title = titleParts[0];
        }

        // Try to refine title from actual DOM (h1, h2, or any prominent heading)
        const headings = document.querySelectorAll('h1, h2');
        for (const h of headings) {
            const txt = h.innerText.trim();
            // Skip generic headings
            if (txt.length > 5 && txt.length < 200
                && !txt.toLowerCase().includes('about')
                && !txt.toLowerCase().includes('similar')
                && !txt.toLowerCase().includes('people')
                && !txt.toLowerCase().includes('meet the')
                && !txt.toLowerCase().includes('premium')) {
                // Check if it looks like a job title (matches part of page title)
                if (result.title && txt.includes(result.title.substring(0, 10))) {
                    result.title = txt;
                    break;
                }
            }
        }

        // Try to refine company from DOM — look for links to company pages
        const companyLinks = document.querySelectorAll('a[href*="/company/"]');
        for (const a of companyLinks) {
            const txt = a.innerText.trim();
            if (txt.length > 1 && txt.length < 100 && !txt.toLowerCase().includes('follow')) {
                result.company = txt;
                break;
            }
        }

        // === EASY APPLY ===
        // Strategy 1: Look for buttons with Easy Apply text
        const allButtons = document.querySelectorAll('button');
        for (const btn of allButtons) {
            if (btn.innerText.includes('Easy Apply')) {
                result.easy_apply = true;
                break;
            }
        }
        // Strategy 2: Check page text (first 3000 chars covers the header area)
        if (!result.easy_apply) {
            const headerText = document.body.innerText.substring(0, 3000);
            if (headerText.includes('Easy Apply')) {
                result.easy_apply = true;
            }
        }

        // === POSTED DATE ===
        // LinkedIn shows relative dates like "2 weeks ago", "3 days ago", "1 month ago"
        // in the header area of the job detail page, often near "· X applicants"
        const headerText2 = document.body.innerText.substring(0, 4000);
        const datePatterns = [
            /(\\d+)\\s+(second|minute|hour|day|week|month|year)s?\\s+ago/i,
            /Reposted\\s+(\\d+)\\s+(day|week|month)s?\\s+ago/i,
        ];
        for (const pat of datePatterns) {
            const m = headerText2.match(pat);
            if (m) {
                result.posted_at_text = m[0];
                break;
            }
        }
        // Also try <time> elements with datetime attribute
        if (!result.posted_at_text) {
            const timeEls = document.querySelectorAll('time[datetime]');
            for (const t of timeEls) {
                const dt = t.getAttribute('datetime');
                if (dt && dt.length > 5) {
                    result.posted_at_text = dt;
                    break;
                }
            }
        }

        // === DESCRIPTION ===
        // Strategy 1: Use data-testid for expandable text box
        const expandable = document.querySelector('[data-testid="expandable-text-box"]');
        if (expandable && expandable.innerText.trim().length > 50) {
            result.description_text = expandable.innerText.trim();
        }

        // Strategy 2: Find "About the job" h2 and grab its parent's next sibling
        if (!result.description_text) {
            const allH2 = document.querySelectorAll('h2');
            for (const h2 of allH2) {
                const txt = h2.innerText.trim().toLowerCase();
                if (txt === 'about the job' || txt === 'about this job') {
                    // Walk up to find the containing section, then get next sibling
                    let container = h2.parentElement;
                    for (let i = 0; i < 5 && container; i++) {
                        const next = container.nextElementSibling;
                        if (next && next.innerText.trim().length > 50) {
                            result.description_text = next.innerText.trim();
                            break;
                        }
                        container = container.parentElement;
                    }
                    if (result.description_text) break;
                }
            }
        }

        // Strategy 3: Text boundary extraction from full page text
        if (!result.description_text) {
            const body = document.body.innerText;
            const startMarkers = ['About the job', 'About this job'];
            const endMarkers = [
                'Set alert for similar', 'About the company',
                'People you can reach', 'Meet the hiring team',
                'Similar jobs', 'Interested in working',
            ];
            for (const start of startMarkers) {
                const startIdx = body.indexOf(start);
                if (startIdx === -1) continue;
                const contentStart = startIdx + start.length;
                let endIdx = body.length;
                for (const end of endMarkers) {
                    const idx = body.indexOf(end, contentStart);
                    if (idx !== -1 && idx < endIdx) endIdx = idx;
                }
                const desc = body.substring(contentStart, endIdx).trim();
                if (desc.length > 50) {
                    result.description_text = desc;
                    break;
                }
            }
        }

        // Strategy 4: Find longest text in a data-testid element
        if (!result.description_text) {
            const testidEls = document.querySelectorAll('[data-testid]');
            let best = '';
            for (const el of testidEls) {
                const txt = el.innerText.trim();
                if (txt.length > best.length && txt.length > 100 && txt.length < 15000) {
                    best = txt;
                }
            }
            if (best) result.description_text = best;
        }

        // Clean up artifacts
        if (result.description_text) {
            result.description_text = result.description_text
                .replace(/^\\s*Show more\\s*/i, '')
                .replace(/\\s*Show more\\s*$/i, '')
                .replace(/\\s*Show less\\s*$/i, '')
                .replace(/\\s*… more\\s*$/i, '')
                .replace(/\\s*\\.\\.\\.\\s*more\\s*$/i, '')
                .trim();
        }

        return result;
    }
    """
    try:
        result = await page.evaluate(js_code)
        return result if isinstance(result, dict) else {}
    except Exception as exc:
        logger.warning("JS detail extraction failed: %s", exc)
        return {}


async def _discover_real(
    *,
    headless: bool = True,
    slowmo_ms: int = 0,
    cookies_path: str | Path = "data/cookies.json",
    keywords: list[str],
    location: str = "",
    remote: bool = False,
    seniority: list[str] | None = None,
    max_pages: int = 10,
    openai_api_key: str = "",
    captcha_handler: Any | None = None,
    settings: Any | None = None,
) -> list[dict[str, Any]]:
    """Run discovery against real LinkedIn using saved cookies.

    Uses extensive human-simulation techniques to avoid CAPTCHA triggers:
    - Organic warm-up browsing (feed → jobs hub → search)
    - Random mouse movements between actions
    - Natural scroll patterns with jitter
    - Variable delays modelled on real human timing
    - Cookie refresh mid-session
    - Referer headers on every navigation
    """
    import random

    from playwright.async_api import async_playwright

    from job_hunter.linkedin.forms import detect_challenge, try_solve_recaptcha
    from job_hunter.linkedin.session import LinkedInSession, build_search_url
    from job_hunter.utils.rate_limit import RateLimiter

    session = LinkedInSession(cookies_path=cookies_path)
    if not session.has_cookies():
        raise FileNotFoundError(
            f"No cookies found at {cookies_path}. "
            "Upload cookies via Settings in the web UI, or run 'hunt login' locally."
        )

    # Slower, more variable pacing — mimics a human who reads content
    rate = RateLimiter(min_delay_ms=3000, max_delay_ms=7000)
    jobs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # ── Human-like interaction helpers ──────────────────────────────────

    async def _human_delay(lo: float = 1.0, hi: float = 3.0) -> None:
        """Random pause — models a human reading / thinking."""
        await page.wait_for_timeout(int(random.uniform(lo, hi) * 1000))

    async def _random_mouse_move(page_obj: Any) -> None:
        """Move mouse to a random spot — real users always have mouse activity."""
        try:
            x = random.randint(200, 1080)
            y = random.randint(150, 750)
            await page_obj.mouse.move(x, y, steps=random.randint(5, 20))
        except Exception:
            pass

    async def _human_scroll(page_obj: Any) -> None:
        """Scroll like a human — varying speeds, pauses, not exact percentages."""
        scroll_positions = sorted(random.sample(range(15, 95), k=random.randint(3, 6)))
        for pct in scroll_positions:
            actual_pct = pct + random.uniform(-5, 5)
            await page_obj.evaluate(
                f"window.scrollTo({{top: document.body.scrollHeight * {actual_pct / 100}, "
                f"behavior: 'smooth'}})"
            )
            if random.random() < 0.3:
                await _human_delay(1.5, 3.5)  # "reading" pause
            else:
                await _human_delay(0.6, 1.5)  # quick scroll
            if random.random() < 0.4:
                await _random_mouse_move(page_obj)

    async def _solve_captcha_or_fail(page_obj: Any, context_label: str) -> bool:
        """Unified CAPTCHA handling: auto-solve → interactive → give up.

        Returns True if challenge is cleared (or was never present).
        Returns False if unresolvable (caller decides what to do).
        """
        if not await detect_challenge(page_obj):
            return True

        logger.warning("Challenge/captcha detected on %s. Attempting to solve…", context_label)

        # Step 1: auto-click reCAPTCHA checkbox
        solved = await try_solve_recaptcha(page_obj, timeout_ms=15_000)
        if solved:
            logger.info("reCAPTCHA clicked — waiting for page to update…")
            await page_obj.wait_for_timeout(5000)
            if not await detect_challenge(page_obj):
                logger.info("reCAPTCHA solved — continuing.")
                return True

        # Step 2: interactive handler (web UI remote clicks)
        if await detect_challenge(page_obj) and captcha_handler is not None:
            logger.info("Trying interactive CAPTCHA solving on %s…", context_label)
            screenshot_dir = Path(cookies_path).parent
            handler_solved = await captcha_handler(page_obj, screenshot_dir)
            if handler_solved and not await detect_challenge(page_obj):
                logger.info("Interactive CAPTCHA solving succeeded!")
                return True

        # Step 3: still blocked
        if await detect_challenge(page_obj):
            return False
        return True

    # ═══════════════════════════════════════════════════════════════════

    async with async_playwright() as pw:
        context = await session.launch_stealth_context(
            pw, headless=headless, slowmo_ms=slowmo_ms,
        )
        page = context.pages[0] if context.pages else await context.new_page()

        try:
            # ═══════════════════════════════════════════════════════════
            # Phase 0: Warm-up — organic browsing to establish normalcy
            # ═══════════════════════════════════════════════════════════
            # A real user arrives at LinkedIn, glances at the feed, maybe
            # checks notifications, THEN navigates to Jobs.  We replicate
            # this path so LinkedIn's session scoring sees normal behaviour.

            logger.info("Verifying LinkedIn login status…")
            await page.goto(
                "https://www.linkedin.com/feed/",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            # "Read" the feed for a realistic duration
            await _human_delay(3.0, 6.0)
            await _random_mouse_move(page)

            current_url = page.url
            if "/login" in current_url or "/checkpoint" in current_url or "authwall" in current_url:
                logger.error(
                    "Cookies are expired or invalid — redirected to %s. "
                    "Run 'hunt login' to re-authenticate.", current_url,
                )
                new_cookies = await context.cookies()
                session.save_cookies(new_cookies)
                raise RuntimeError(
                    f"LinkedIn cookies expired — redirected to {current_url}. "
                    f"Run 'hunt login' to re-authenticate."
                )

            page_html = await page.content()
            if "d_jobs_guest" in page_html or "public_jobs" in page_html:
                logger.warning("LinkedIn is serving guest pages despite cookies. Re-login required.")
                raise RuntimeError(
                    "LinkedIn is serving guest pages despite cookies. "
                    "Cookies may be expired. Run 'hunt login' to re-authenticate."
                )

            logger.info("Login verified — session is active.")

            # Scroll the feed a bit and move mouse — looks like a real user
            await _human_scroll(page)
            await _random_mouse_move(page)
            await _human_delay(1.0, 3.0)

            # Save refreshed cookies after warm-up
            new_cookies = await context.cookies()
            session.save_cookies(new_cookies)

            # ── Navigate organically to Jobs hub first ──
            # Instead of jumping straight to a search URL, go to the Jobs
            # tab like a real user would (via the nav bar link).
            logger.info("Navigating to Jobs hub…")
            try:
                jobs_link = page.locator('a[href*="/jobs/"]').first
                if await jobs_link.count() > 0:
                    await _random_mouse_move(page)
                    await jobs_link.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=15_000)
                else:
                    await page.goto(
                        "https://www.linkedin.com/jobs/",
                        wait_until="domcontentloaded",
                        timeout=30_000,
                    )
            except Exception:
                await page.goto(
                    "https://www.linkedin.com/jobs/",
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
            await _human_delay(2.0, 5.0)
            await _random_mouse_move(page)

            # Refresh cookies — LinkedIn may set new tokens after Jobs nav
            new_cookies = await context.cookies()
            session.save_cookies(new_cookies)

            # ═══════════════════════════════════════════════════════════
            # Phase 1: Search pages
            # ═══════════════════════════════════════════════════════════

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

                # Navigate with referer — looks like internal navigation
                await page.goto(
                    search_url,
                    wait_until="domcontentloaded",
                    timeout=30_000,
                    referer="https://www.linkedin.com/jobs/",
                )
                # Longer wait for first page, shorter for subsequent
                if page_num == 0:
                    await _human_delay(4.0, 7.0)
                else:
                    await _human_delay(3.0, 5.0)

                await _random_mouse_move(page)

                # Wait for job cards to appear
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

                # Human-like scrolling to load lazy content
                await _human_scroll(page)
                await _random_mouse_move(page)

                # ── CAPTCHA check ──
                captcha_clear = await _solve_captcha_or_fail(page, f"search page {page_num + 1}")
                if not captcha_clear:
                    challenge_html = await page.content()
                    debug_path = Path(cookies_path).parent / "debug_search_page.html"
                    debug_path.write_text(challenge_html[:100000], encoding="utf-8")
                    raise RuntimeError(
                        "LinkedIn showed a CAPTCHA/challenge on the search page. "
                        "Go to Settings → uncheck 'Headless' and try again. "
                        "The browser will open visibly so you can solve the captcha. "
                        "Alternatively, run 'hunt login' to refresh your session."
                    )

                # After CAPTCHA, re-navigate if we ended up elsewhere
                if page.url != search_url and "/jobs/search" not in page.url:
                    await page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
                    await _human_delay(3.0, 5.0)
                    await _human_scroll(page)

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

                if page_num == 0:
                    debug_path = Path(cookies_path).parent / "debug_search_page.html"
                    debug_path.write_text(list_html[:100000], encoding="utf-8")
                    logger.info("Saved search page HTML to %s (%d bytes)", debug_path, len(list_html))

                if "d_jobs_guest" in list_html or "public_jobs_nav" in list_html:
                    logger.warning("Search page served as guest despite login verification.")
                    raise RuntimeError(
                        "LinkedIn served a guest page for job search. "
                        "Your session may have been invalidated. Run 'hunt login'. "
                        f"Debug HTML saved to {debug_path}"
                    )

                cards = parse_job_cards(list_html)
                logger.info("Page %d: found %d job cards via CSS selectors", page_num + 1, len(cards))

                if not cards:
                    logger.info("CSS selectors matched nothing. Trying JS-based extraction…")
                    cards = await _extract_jobs_via_js(page)
                    logger.info("JS extraction found %d job cards", len(cards))

                if not cards:
                    if page_num == 0:
                        title = await page.title()
                        current_page_url = page.url
                        logger.warning(
                            "No job cards found on page 1. Page title: '%s', URL: %s. "
                            "LinkedIn may have changed their DOM structure. "
                            "Check debug HTML at data/debug_search_page.html",
                            title, current_page_url,
                        )
                    else:
                        logger.info("No more job cards on page %d — end of results.", page_num + 1)
                    break

                # ═══════════════════════════════════════════════════════
                # Phase 2: Visit detail pages
                # ═══════════════════════════════════════════════════════

                for card in cards:
                    ext_id = card.get("external_id", "")
                    if not ext_id or ext_id in seen_ids:
                        continue
                    seen_ids.add(ext_id)

                    detail_url = f"https://www.linkedin.com/jobs/view/{ext_id}/"

                    await rate.wait()
                    logger.info("Fetching detail: %s (%s)", ext_id, card.get("title", "?"))
                    await _random_mouse_move(page)

                    detail: dict[str, Any] = {}
                    try:
                        await page.goto(
                            detail_url,
                            wait_until="domcontentloaded",
                            timeout=20_000,
                            referer=search_url,
                        )
                        await _human_delay(2.5, 5.0)
                        await _random_mouse_move(page)

                        try:
                            await page.wait_for_selector(
                                "[data-testid='expandable-text-box'], "
                                "h1, h2, "
                                "a[href*='/company/']",
                                timeout=8_000,
                            )
                        except Exception:
                            await _human_delay(2.0, 4.0)

                        # CAPTCHA check on detail page
                        if not await _solve_captcha_or_fail(page, f"detail page {ext_id}"):
                            logger.warning("Unresolvable CAPTCHA on detail page. Stopping.")
                            return jobs

                        # Expand truncated descriptions
                        await _expand_all_show_more(page)

                        # Scroll to "read" the description (human behaviour)
                        await _human_scroll(page)

                        if len(seen_ids) <= 1:
                            detail_html_debug = await page.content()
                            debug_detail = Path(cookies_path).parent / "debug_detail_page.html"
                            debug_detail.write_text(detail_html_debug[:200000], encoding="utf-8")
                            logger.info("Saved detail page HTML to %s", debug_detail)

                        detail = await _extract_detail_via_js(page)

                        desc_len = len(detail.get("description_text", ""))
                        logger.info(
                            "Detail %s: title='%s', company='%s', desc=%d chars, easy_apply=%s",
                            ext_id,
                            detail.get("title", ""),
                            detail.get("company", ""),
                            desc_len,
                            detail.get("easy_apply", False),
                        )

                    except Exception as exc:
                        logger.warning("Failed to fetch detail for %s: %s", ext_id, exc)

                    raw_desc = detail.get("description_text", "")
                    if (openai_api_key or settings) and raw_desc:
                        cleaned_desc = clean_description_llm(
                            raw_desc, openai_api_key, settings=settings,
                        )
                    else:
                        cleaned_desc = clean_description_rules(raw_desc)
                    desc_formatted = looks_llm_formatted(cleaned_desc)

                    posted_at_text = detail.get("posted_at_text", "")
                    posted_at = _parse_relative_date(posted_at_text)
                    if posted_at_text:
                        logger.debug("Posted date text: '%s' → %s", posted_at_text, posted_at)

                    job_data: dict[str, Any] = {
                        "external_id": ext_id,
                        "url": detail_url,
                        "title": detail.get("title") or card.get("title", ""),
                        "company": detail.get("company") or card.get("company", ""),
                        "location": detail.get("location") or card.get("location", ""),
                        "description_text": cleaned_desc,
                        "description_formatted": desc_formatted,
                        "easy_apply": detail.get("easy_apply", False),
                        "posted_at": posted_at,
                        "source": "linkedin",
                        "hash": job_hash(
                            external_id=ext_id,
                            title=detail.get("title") or card.get("title", ""),
                            company=detail.get("company") or card.get("company", ""),
                        ),
                    }
                    jobs.append(job_data)
                    logger.debug("Discovered: %s at %s", job_data["title"], job_data["company"])

                # Periodically refresh cookies to keep session alive
                if page_num % 3 == 2:
                    new_cookies = await context.cookies()
                    session.save_cookies(new_cookies)

        finally:
            # Save cookies one final time before closing
            try:
                final_cookies = await context.cookies()
                session.save_cookies(final_cookies)
            except Exception:
                pass
            await context.close()

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

