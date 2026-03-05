"""Tests for orchestration pipeline and policies."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_hunter.db.models import Job, JobStatus
from job_hunter.db.repo import get_memory_engine, get_jobs_by_status, init_db, make_session, upsert_job
from job_hunter.orchestration.pipeline import run_pipeline
from job_hunter.orchestration.policies import can_apply_today, is_blacklisted
from job_hunter.utils.hashing import job_hash


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------

class TestCanApplyToday:
    def test_under_cap(self) -> None:
        assert can_apply_today(applied_today=5, max_per_day=25) is True

    def test_at_cap(self) -> None:
        assert can_apply_today(applied_today=25, max_per_day=25) is False

    def test_over_cap(self) -> None:
        assert can_apply_today(applied_today=30, max_per_day=25) is False

    def test_zero_cap(self) -> None:
        assert can_apply_today(applied_today=0, max_per_day=0) is False


class TestIsBlacklisted:
    def test_blacklisted_company(self) -> None:
        assert is_blacklisted(
            company="SpamCorp LLC", title="Python Dev",
            blacklist_companies=["SpamCorp"], blacklist_titles=[],
        ) is True

    def test_blacklisted_title(self) -> None:
        assert is_blacklisted(
            company="Acme", title="Junior Python Developer",
            blacklist_companies=[], blacklist_titles=["Junior"],
        ) is True

    def test_not_blacklisted(self) -> None:
        assert is_blacklisted(
            company="Acme", title="Senior Python Developer",
            blacklist_companies=["SpamCorp"], blacklist_titles=["Junior"],
        ) is False

    def test_case_insensitive(self) -> None:
        assert is_blacklisted(
            company="spamcorp", title="Python Dev",
            blacklist_companies=["SpamCorp"], blacklist_titles=[],
        ) is True


# ---------------------------------------------------------------------------
# Pipeline (mock mode, end-to-end)
# ---------------------------------------------------------------------------

class TestRunPipeline:
    @pytest.mark.asyncio
    async def test_full_pipeline_mock_dry_run(self, tmp_path: Path) -> None:
        summary = await run_pipeline(
            profile_name="default",
            mock=True,
            dry_run=True,
            data_dir=tmp_path,
            resume_text="Senior Python Developer with 8 years experience",
            resume_path="tests/fixtures/resume.txt",
        )

        # Should have discovered 3 jobs
        assert summary["discovered"] == 3

        # Should have scored 3 jobs
        assert summary["scored"] == 3

        # mock-001 and mock-002 have easy_apply → queued → dry_run applied
        # mock-003 has no easy_apply → review
        assert summary["queued"] >= 1
        assert summary["review"] >= 0

        # Report should have been generated
        assert summary.get("report_date") is not None
        report_md = tmp_path / "reports" / f"{summary['report_date']}.md"
        assert report_md.exists()

    @pytest.mark.asyncio
    async def test_pipeline_idempotent(self, tmp_path: Path) -> None:
        """Running pipeline twice should not create duplicates."""
        for _ in range(2):
            await run_pipeline(
                profile_name="default",
                mock=True,
                dry_run=True,
                data_dir=tmp_path,
                resume_text="Python dev",
                resume_path="tests/fixtures/resume.txt",
            )

        # Check DB has only 3 jobs (not 6)
        from job_hunter.db.repo import get_all_jobs, get_engine, make_session
        engine = get_engine(tmp_path)
        session = make_session(engine)
        all_jobs = get_all_jobs(session)
        assert len(all_jobs) == 3

    @pytest.mark.asyncio
    async def test_pipeline_with_blacklist(self, tmp_path: Path) -> None:
        summary = await run_pipeline(
            profile_name="default",
            mock=True,
            dry_run=True,
            data_dir=tmp_path,
            resume_text="Python dev",
            resume_path="tests/fixtures/resume.txt",
            blacklist_companies=["Initech"],
        )

        # Initech job should be skipped
        assert summary["skipped"] >= 1

    @pytest.mark.asyncio
    async def test_pipeline_generates_json_report(self, tmp_path: Path) -> None:
        summary = await run_pipeline(
            profile_name="default",
            mock=True,
            dry_run=True,
            data_dir=tmp_path,
            resume_text="Python dev",
            resume_path="tests/fixtures/resume.txt",
        )

        json_path = Path(summary["report_json_path"])
        assert json_path.exists()

        import json
        with open(json_path) as f:
            data = json.load(f)
        assert data["total_jobs"] == 3

