"""Parse job list and detail pages into structured data."""

from __future__ import annotations


def parse_job_card(html: str) -> dict:
    """Extract job metadata from a single job-card HTML fragment."""
    raise NotImplementedError


def parse_job_detail(html: str) -> dict:
    """Extract full job details from a job-detail page."""
    raise NotImplementedError

