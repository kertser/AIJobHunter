"""Text extraction from PDFs and LinkedIn profile URLs."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import fitz  # PyMuPDF

logger = logging.getLogger("job_hunter.profile.extract")


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def extract_text_from_pdf(path: Path) -> str:
    """Extract all text from a PDF file.

    Returns the concatenated text of every page, separated by newlines.
    Raises ``FileNotFoundError`` if *path* does not exist and
    ``ValueError`` if the file contains no extractable text.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    doc = fitz.open(str(path))
    pages: list[str] = []
    for page in doc:
        text = page.get_text()
        if text.strip():
            pages.append(text.strip())
    doc.close()

    if not pages:
        raise ValueError(f"No extractable text found in {path}")

    full_text = "\n\n".join(pages)
    logger.debug("Extracted %d characters from %s (%d pages)", len(full_text), path.name, len(pages))
    return full_text


# ---------------------------------------------------------------------------
# LinkedIn profile URL extraction
# ---------------------------------------------------------------------------

def _is_linkedin_url(value: str) -> bool:
    """Return True if *value* looks like a LinkedIn profile URL."""
    return bool(re.match(r"https?://(www\.)?linkedin\.com/in/", value))


def extract_text_from_linkedin_url(url: str, *, headless: bool = True) -> str:
    """Scrape a public LinkedIn profile page and return its visible text.

    Uses Playwright to render the JavaScript-heavy page, scrolls to load
    lazy content, then extracts the ``innerText`` of the main profile
    sections.

    Raises ``ValueError`` if no meaningful text could be extracted.
    """
    from playwright.sync_api import sync_playwright

    logger.info("Fetching LinkedIn profile: %s", url)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = context.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            # Wait for the main profile content to appear
            page.wait_for_selector("main", timeout=15_000)

            # Scroll down a few times to trigger lazy-loaded sections
            for _ in range(5):
                page.keyboard.press("End")
                page.wait_for_timeout(800)

            # Try to expand "see more" sections
            see_more_buttons = page.query_selector_all(
                "button.inline-show-more-text__button, "
                "button[aria-label*='Show more'], "
                "button[aria-label*='see more']"
            )
            for btn in see_more_buttons[:10]:  # cap to avoid infinite loops
                try:
                    btn.click(timeout=2_000)
                    page.wait_for_timeout(300)
                except Exception:
                    pass

            # Extract text from the main content area
            main = page.query_selector("main")
            if main is None:
                raise ValueError(f"Could not find main content on {url}")

            raw_text = main.inner_text()

        finally:
            context.close()
            browser.close()

    # Clean up: collapse excessive whitespace
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    text = "\n".join(lines)

    if len(text) < 50:
        raise ValueError(
            f"Extracted too little text ({len(text)} chars) from {url}. "
            "The profile may be private or LinkedIn may have blocked the request."
        )

    logger.info("Extracted %d characters from LinkedIn profile", len(text))
    return text


# ---------------------------------------------------------------------------
# Unified extraction
# ---------------------------------------------------------------------------

def extract_texts(
    resume_path: Path,
    linkedin_source: str | Path | None = None,
    *,
    headless: bool = True,
) -> str:
    """Extract and concatenate text from the resume and optional LinkedIn source.

    *linkedin_source* can be:
    - A ``Path`` to a PDF file
    - A LinkedIn profile URL string (``https://linkedin.com/in/...``)
    - ``None`` to skip LinkedIn

    Sections are clearly labelled so the LLM can distinguish between them.
    """
    parts: list[str] = []

    parts.append("=== RESUME ===")
    parts.append(extract_text_from_pdf(resume_path))

    if linkedin_source is not None:
        source_str = str(linkedin_source)
        parts.append("\n=== LINKEDIN PROFILE ===")

        if _is_linkedin_url(source_str):
            parts.append(extract_text_from_linkedin_url(source_str, headless=headless))
        else:
            # Treat as a file path (PDF)
            parts.append(extract_text_from_pdf(Path(source_str)))

    return "\n\n".join(parts)

