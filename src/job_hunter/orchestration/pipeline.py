"""Main pipeline: discover → score → queue → apply → report."""

from __future__ import annotations


async def run_pipeline(*, profile_name: str, mock: bool = False, dry_run: bool = False) -> dict:
    """Execute the full pipeline for a given search profile.

    Returns a summary dict suitable for the daily report.
    """
    raise NotImplementedError

