"""LinkedIn job discovery — navigate search results and collect job cards."""

from __future__ import annotations


async def discover_jobs(*, profile_name: str, mock: bool = False) -> list[dict]:
    """Discover jobs matching the given profile.

    Returns raw job-card dicts ready for parsing.
    """
    raise NotImplementedError

