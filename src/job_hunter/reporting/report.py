"""Daily report generation — Markdown + JSON summaries."""

from __future__ import annotations

from pathlib import Path


def generate_report(*, data_dir: Path, date: str | None = None) -> dict:
    """Generate the daily report for *date* (defaults to today).

    Writes ``reports/YYYY-MM-DD.md`` and ``reports/YYYY-MM-DD.json``
    inside *data_dir*.

    Returns a summary dict.
    """
    raise NotImplementedError

