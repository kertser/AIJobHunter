"""Apply worker — Easy Apply automation via Playwright."""

from __future__ import annotations


async def apply_to_job(*, job_url: str, resume_path: str, dry_run: bool = False) -> dict:
    """Execute the Easy Apply wizard for a single job.

    Returns a dict with keys: result, failure_stage, form_answers.
    """
    raise NotImplementedError

