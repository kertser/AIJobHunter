"""Tests for mock Easy Apply flow — Phase 4."""

from __future__ import annotations

import pytest

from job_hunter.db.models import ApplicationAttempt, ApplicationResult, Job, JobStatus
from job_hunter.db.repo import get_memory_engine, init_db, make_session, save_attempt, upsert_job, get_jobs_by_status
from job_hunter.linkedin.apply import apply_to_job
from job_hunter.linkedin.mock_site import MockLinkedInServer
from job_hunter.utils.hashing import job_hash


@pytest.fixture()
def mock_server():
    """Start the mock LinkedIn server for the duration of a test."""
    server = MockLinkedInServer()
    base_url = server.start()
    yield base_url
    server.stop()


# ---------------------------------------------------------------------------
# apply_to_job — wizard flow
# ---------------------------------------------------------------------------

class TestApplyDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_reaches_review_step(self, mock_server: str) -> None:
        result = await apply_to_job(
            job_url=f"{mock_server}/jobs/view/mock-001",
            resume_path="tests/fixtures/resume.txt",
            dry_run=True,
            mock=True,
        )
        assert result["result"] == "dry_run"
        assert result["failure_stage"] is None
        assert result["started_at"] is not None
        assert result["ended_at"] is not None

    @pytest.mark.asyncio
    async def test_dry_run_fills_form_answers(self, mock_server: str) -> None:
        result = await apply_to_job(
            job_url=f"{mock_server}/jobs/view/mock-001",
            resume_path="tests/fixtures/resume.txt",
            dry_run=True,
            mock=True,
        )
        # Step 2 should have filled some form fields
        assert isinstance(result["form_answers"], dict)


class TestApplySuccess:
    @pytest.mark.asyncio
    async def test_full_submit_returns_success(self, mock_server: str) -> None:
        result = await apply_to_job(
            job_url=f"{mock_server}/jobs/view/mock-001",
            resume_path="tests/fixtures/resume.txt",
            dry_run=False,
            mock=True,
        )
        assert result["result"] == "success"
        assert result["failure_stage"] is None


class TestApplyNoEasyApply:
    @pytest.mark.asyncio
    async def test_no_easy_apply_button_fails(self, mock_server: str) -> None:
        # mock-003 has no Easy Apply button
        result = await apply_to_job(
            job_url=f"{mock_server}/jobs/view/mock-003",
            resume_path="tests/fixtures/resume.txt",
            dry_run=False,
            mock=True,
        )
        assert result["result"] == "failed"
        assert result["failure_stage"] == "no_easy_apply"


class TestApplyChallenge:
    @pytest.mark.asyncio
    async def test_challenge_page_returns_blocked(self, mock_server: str) -> None:
        result = await apply_to_job(
            job_url=f"{mock_server}/challenge",
            resume_path="tests/fixtures/resume.txt",
            dry_run=False,
            mock=True,
        )
        assert result["result"] == "blocked"
        assert result["failure_stage"] == "challenge"


# ---------------------------------------------------------------------------
# Integration: apply → DB
# ---------------------------------------------------------------------------

class TestApplyToDb:
    @pytest.mark.asyncio
    async def test_apply_result_persists(self, mock_server: str) -> None:
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)

        # Create a queued job
        job = Job(
            external_id="mock-001",
            url="/jobs/view/mock-001",
            title="Senior Python Developer",
            company="Acme Corp",
            hash=job_hash(external_id="mock-001", title="Senior Python Developer", company="Acme Corp"),
            easy_apply=True,
            status=JobStatus.QUEUED,
        )
        upsert_job(session, job)
        session.commit()

        # Apply
        result = await apply_to_job(
            job_url=f"{mock_server}{job.url}",
            resume_path="tests/fixtures/resume.txt",
            dry_run=True,
            mock=True,
        )

        # Save attempt
        attempt = ApplicationAttempt(
            job_hash=job.hash,
            started_at=result["started_at"],
            ended_at=result["ended_at"],
            result=ApplicationResult.DRY_RUN,
            form_answers_json=result.get("form_answers", {}),
        )
        save_attempt(session, attempt)
        job.status = JobStatus.APPLIED
        session.commit()

        # Verify
        applied_jobs = get_jobs_by_status(session, JobStatus.APPLIED)
        assert len(applied_jobs) == 1
        assert applied_jobs[0].external_id == "mock-001"

    @pytest.mark.asyncio
    async def test_blocked_job_marked_in_db(self, mock_server: str) -> None:
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)

        job = Job(
            external_id="blocked-001",
            url="/challenge",
            title="Blocked Job",
            company="BadCo",
            hash=job_hash(external_id="blocked-001", title="Blocked Job", company="BadCo"),
            easy_apply=True,
            status=JobStatus.QUEUED,
        )
        upsert_job(session, job)
        session.commit()

        result = await apply_to_job(
            job_url=f"{mock_server}{job.url}",
            resume_path="tests/fixtures/resume.txt",
            dry_run=False,
            mock=True,
        )

        attempt = ApplicationAttempt(
            job_hash=job.hash,
            result=ApplicationResult.BLOCKED,
            failure_stage=result["failure_stage"],
        )
        save_attempt(session, attempt)
        job.status = JobStatus.BLOCKED
        session.commit()

        blocked_jobs = get_jobs_by_status(session, JobStatus.BLOCKED)
        assert len(blocked_jobs) == 1
        assert blocked_jobs[0].external_id == "blocked-001"

