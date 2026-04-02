"""Content hashing for deduplication."""

from __future__ import annotations

import hashlib
import re


def job_hash(*, external_id: str, title: str, company: str) -> str:
    """Return a deterministic SHA-256 hex digest for a job listing.

    The hash is used to detect duplicates across discovery runs.
    """
    payload = f"{external_id}|{title}|{company}"
    return hashlib.sha256(payload.encode()).hexdigest()


_WS_RE = re.compile(r"\s+")


def normalize_for_dedup(title: str, company: str) -> tuple[str, str]:
    """Normalise title and company for content-based duplicate detection.

    Returns ``(norm_title, norm_company)`` — lowercased, stripped, with
    collapsed whitespace.
    """
    norm_title = _WS_RE.sub(" ", title.strip()).lower()
    norm_company = _WS_RE.sub(" ", company.strip()).lower()
    return norm_title, norm_company


