"""Tests for mock LinkedIn job discovery and parsing — Phase 2 stubs."""

from __future__ import annotations

import pytest

from job_hunter.linkedin.discover import discover_jobs
from job_hunter.linkedin.parse import parse_job_card, parse_job_detail


class TestDiscoverJobsMock:
    @pytest.mark.skip(reason="Phase 2 — not yet implemented")
    def test_discover_returns_list(self) -> None:
        # Will be implemented in Phase 2
        result = discover_jobs(profile_name="default", mock=True)
        assert isinstance(result, list)


class TestParseJobCard:
    @pytest.mark.skip(reason="Phase 2 — not yet implemented")
    def test_parse_extracts_title(self) -> None:
        html = '<a class="job-card-list__title">Python Dev</a>'
        result = parse_job_card(html)
        assert result["title"] == "Python Dev"


class TestParseJobDetail:
    @pytest.mark.skip(reason="Phase 2 — not yet implemented")
    def test_parse_extracts_description(self) -> None:
        html = '<div class="show-more-less-html__markup"><p>Job desc</p></div>'
        result = parse_job_detail(html)
        assert "Job desc" in result["description_text"]

