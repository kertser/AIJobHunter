"""Parse job list and detail pages into structured data."""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup, Tag

from job_hunter.linkedin import selectors as sel

logger = logging.getLogger("job_hunter.linkedin.parse")


def parse_job_card(html: str) -> dict:
    """Extract job metadata from a single job-card HTML fragment.

    *html* should be the outer HTML of one ``div.job-card-container`` element
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

    card: Tag | None = soup.select_one(sel.JOB_CARD)
    if card is None:
        # Maybe the fragment *is* the card itself
        card = soup

    external_id = card.get("data-job-id", "")

    title_el = card.select_one(sel.JOB_CARD_TITLE)
    title = title_el.get_text(strip=True) if title_el else ""

    company_el = card.select_one(sel.JOB_CARD_COMPANY)
    company = company_el.get_text(strip=True) if company_el else ""

    location_el = card.select_one(sel.JOB_CARD_LOCATION)
    location = location_el.get_text(strip=True) if location_el else ""

    link_el = card.select_one(sel.JOB_CARD_LINK)
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
    cards = soup.select(sel.JOB_CARD)
    logger.debug("Found %d job cards", len(cards))
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

    title_el = soup.select_one(sel.JOB_DETAIL_TITLE)
    title = title_el.get_text(strip=True) if title_el else ""

    company_el = soup.select_one(sel.JOB_DETAIL_COMPANY)
    company = company_el.get_text(strip=True) if company_el else ""

    desc_el = soup.select_one(sel.JOB_DETAIL_DESCRIPTION)
    description_text = desc_el.get_text(separator="\n", strip=True) if desc_el else ""

    easy_apply_el = soup.select_one(sel.EASY_APPLY_BUTTON)
    easy_apply = easy_apply_el is not None

    return {
        "title": title,
        "company": company,
        "description_text": description_text,
        "easy_apply": easy_apply,
    }

