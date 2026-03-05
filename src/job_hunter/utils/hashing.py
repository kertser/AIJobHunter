"""Content hashing for deduplication."""

from __future__ import annotations

import hashlib


def job_hash(*, external_id: str, title: str, company: str) -> str:
    """Return a deterministic SHA-256 hex digest for a job listing.

    The hash is used to detect duplicates across discovery runs.
    """
    payload = f"{external_id}|{title}|{company}"
    return hashlib.sha256(payload.encode()).hexdigest()

