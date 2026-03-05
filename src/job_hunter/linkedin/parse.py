"""Parse job list and detail pages into structured data."""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup, Tag

from job_hunter.linkedin import selectors as sel

logger = logging.getLogger("job_hunter.linkedin.parse")


def _select_first(soup: BeautifulSoup | Tag, selectors: list[str]) -> Tag | None:
    """Try each CSS selector in order and return the first match."""
    for css in selectors:
        el = soup.select_one(css)
        if el is not None:
            return el
    return None


def _select_all(soup: BeautifulSoup | Tag, selectors: list[str]) -> list[Tag]:
    """Try each CSS selector in order and return results from first that matches."""
    for css in selectors:
        results = soup.select(css)
        if results:
            return results
    return []


def parse_job_card(html: str) -> dict:
    """Extract job metadata from a single job-card HTML fragment.

    *html* should be the outer HTML of one job card element
    **or** a full page containing one or more cards (only the first card is
    parsed in that case).

    Returns::

        {
            "external_id": "mock-001",
            "title": "Senior Python Developer",
            "company": "Acme Corp",
            "location": "Remote",
            "url": "/jobs/view/mock-001",
        }
    """
    soup = BeautifulSoup(html, "html.parser")

    card: Tag | None = _select_first(soup, sel.JOB_CARD_SELECTORS)
    if card is None:
        # Maybe the fragment *is* the card itself
        card = soup

    external_id = card.get("data-job-id", "") or card.get("data-occludable-job-id", "")
    # Also try to extract job ID from links if not in attrs
    if not external_id:
        link = _select_first(card, sel.JOB_CARD_LINK_SELECTORS)
        if link:
            href = str(link.get("href", ""))
            # Extract ID from URL like /jobs/view/12345678/
            parts = [p for p in href.split("/") if p.isdigit()]
            if parts:
                external_id = parts[0]

    title_el = _select_first(card, sel.JOB_CARD_TITLE_SELECTORS)
    title = title_el.get_text(strip=True) if title_el else ""

    company_el = _select_first(card, sel.JOB_CARD_COMPANY_SELECTORS)
    company = company_el.get_text(strip=True) if company_el else ""

    location_el = _select_first(card, sel.JOB_CARD_LOCATION_SELECTORS)
    location = location_el.get_text(strip=True) if location_el else ""

    link_el = _select_first(card, sel.JOB_CARD_LINK_SELECTORS)
    url = link_el.get("href", "") if link_el else ""

    return {
        "external_id": str(external_id),
        "title": title,
        "company": company,
        "location": location,
        "url": str(url),
    }


def parse_job_cards(html: str) -> list[dict]:
    """Parse all job cards from a full job-list page.

    Returns a list of dicts, one per card.
    """
    soup = BeautifulSoup(html, "html.parser")
    cards = _select_all(soup, sel.JOB_CARD_SELECTORS)
    logger.debug("Found %d job cards (tried %d selector variants)", len(cards), len(sel.JOB_CARD_SELECTORS))
    return [parse_job_card(str(card)) for card in cards]


def parse_job_detail(html: str) -> dict:
    """Extract full job details from a job-detail page.

    Returns::

        {
            "title": "Senior Python Developer",
            "company": "Acme Corp",
            "description_text": "We are looking for ...",
            "easy_apply": True,
        }
    """
    soup = BeautifulSoup(html, "html.parser")

    title_el = _select_first(soup, sel.JOB_DETAIL_TITLE_SELECTORS)
    title = title_el.get_text(strip=True) if title_el else ""

    company_el = _select_first(soup, sel.JOB_DETAIL_COMPANY_SELECTORS)
    company = company_el.get_text(strip=True) if company_el else ""

    desc_el = _select_first(soup, sel.JOB_DETAIL_DESCRIPTION_SELECTORS)
    description_text = desc_el.get_text(separator="\n", strip=True) if desc_el else ""

    easy_apply_el = _select_first(soup, sel.EASY_APPLY_BUTTON_SELECTORS)
    easy_apply = easy_apply_el is not None

    return {
        "title": title,
        "company": company,
        "description_text": description_text,
        "easy_apply": easy_apply,
    }

