"""Tests for mock Easy Apply flow — Phase 4 stubs."""

from __future__ import annotations

import pytest

from job_hunter.linkedin.apply import apply_to_job


class TestApplyMockFlow:
    @pytest.mark.skip(reason="Phase 4 — not yet implemented")
    def test_mock_apply_success(self) -> None:
        # Will be implemented in Phase 4
        result = apply_to_job(
            job_url="https://mock/jobs/view/mock-001",
            resume_path="tests/fixtures/resume.txt",
            dry_run=True,
        )
        assert result["result"] == "dry_run"

    @pytest.mark.skip(reason="Phase 4 — not yet implemented")
    def test_mock_apply_failure(self) -> None:
        result = apply_to_job(
            job_url="https://mock/jobs/view/broken",
            resume_path="tests/fixtures/resume.txt",
            dry_run=True,
        )
        assert result["result"] == "failed"

