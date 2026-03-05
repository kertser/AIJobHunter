"""Application policies — rate limits, blacklists, and idempotency guards."""

from __future__ import annotations


def can_apply_today(*, applied_today: int, max_per_day: int = 25) -> bool:
    """Check whether the daily application cap has been reached."""
    return applied_today < max_per_day


def is_blacklisted(*, company: str, title: str, blacklist_companies: list[str], blacklist_titles: list[str]) -> bool:
    """Return True if the job matches a blacklist entry."""
    company_lower = company.lower()
    title_lower = title.lower()
    for bc in blacklist_companies:
        if bc.lower() in company_lower:
            return True
    for bt in blacklist_titles:
        if bt.lower() in title_lower:
            return True
    return False

