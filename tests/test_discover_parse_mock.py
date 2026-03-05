"""Tests for mock LinkedIn job discovery and parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_hunter.db.models import Job, JobStatus
from job_hunter.db.repo import get_memory_engine, init_db, make_session, upsert_job, get_jobs_by_status
from job_hunter.linkedin.discover import discover_jobs
from job_hunter.linkedin.parse import parse_job_card, parse_job_cards, parse_job_detail
from job_hunter.utils.hashing import job_hash

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "src" / "job_hunter" / "linkedin" / "mock_site" / "fixtures"


# ---------------------------------------------------------------------------
# parse_job_card
# ---------------------------------------------------------------------------

class TestParseJobCard:
    def test_extracts_all_fields(self) -> None:
        html = (FIXTURES_DIR / "job_list.html").read_text()
        # Parse the first card only
        card = parse_job_card(html)
        assert card["external_id"] == "mock-001"
        assert card["title"] == "Senior Python Developer"
        assert card["company"] == "Acme Corp"
        assert card["location"] == "Remote"
        assert card["url"] == "/jobs/view/mock-001"

    def test_minimal_fragment(self) -> None:
        html = """
        <div class="job-card-container" data-job-id="x-42">
          <a class="job-card-list__title" href="/jobs/view/x-42">Test Role</a>
          <span class="job-card-container__primary-description">TestCo</span>
          <li class="job-card-container__metadata-item">Berlin</li>
        </div>
        """
        card = parse_job_card(html)
        assert card["external_id"] == "x-42"
        assert card["title"] == "Test Role"
        assert card["company"] == "TestCo"
        assert card["location"] == "Berlin"

    def test_missing_elements_return_empty_strings(self) -> None:
        html = '<div class="job-card-container"></div>'
        card = parse_job_card(html)
        assert card["title"] == ""
        assert card["company"] == ""
        assert card["location"] == ""
        assert card["url"] == ""


# ---------------------------------------------------------------------------
# parse_job_cards (batch)
# ---------------------------------------------------------------------------

class TestParseJobCards:
    def test_parses_all_cards_from_list_page(self) -> None:
        html = (FIXTURES_DIR / "job_list.html").read_text()
        cards = parse_job_cards(html)
        assert len(cards) == 3

        ids = [c["external_id"] for c in cards]
        assert ids == ["mock-001", "mock-002", "mock-003"]

        titles = [c["title"] for c in cards]
        assert "Senior Python Developer" in titles
        assert "Machine Learning Engineer" in titles
        assert "Data Scientist" in titles


# ---------------------------------------------------------------------------
# parse_job_detail
# ---------------------------------------------------------------------------

class TestParseJobDetail:
    def test_extracts_detail_fields(self) -> None:
        html = (FIXTURES_DIR / "job_detail.html").read_text()
        detail = parse_job_detail(html)
        assert detail["title"] == "Senior Python Developer"
        assert detail["company"] == "Acme Corp"
        assert "Senior Python Developer" in detail["description_text"]
        assert "5+ years Python experience" in detail["description_text"]
        assert detail["easy_apply"] is True

    def test_easy_apply_false_when_no_button(self) -> None:
        html = (FIXTURES_DIR / "job_detail_003.html").read_text()
        detail = parse_job_detail(html)
        assert detail["title"] == "Data Scientist"
        assert detail["company"] == "Initech"
        assert detail["easy_apply"] is False

    def test_extracts_description_from_minimal_html(self) -> None:
        html = '<div class="show-more-less-html__markup"><p>Job desc here</p></div>'
        detail = parse_job_detail(html)
        assert "Job desc here" in detail["description_text"]

    def test_detail_002(self) -> None:
        html = (FIXTURES_DIR / "job_detail_002.html").read_text()
        detail = parse_job_detail(html)
        assert detail["title"] == "Machine Learning Engineer"
        assert detail["company"] == "Globex Inc"
        assert detail["easy_apply"] is True
        assert "recommendation systems" in detail["description_text"]


# ---------------------------------------------------------------------------
# discover_jobs (async, mock mode)
# ---------------------------------------------------------------------------

class TestDiscoverJobsMock:
    @pytest.mark.asyncio
    async def test_discover_returns_three_jobs(self) -> None:
        jobs = await discover_jobs(profile_name="default", mock=True)
        assert isinstance(jobs, list)
        assert len(jobs) == 3

    @pytest.mark.asyncio
    async def test_discovered_jobs_have_required_fields(self) -> None:
        jobs = await discover_jobs(profile_name="default", mock=True)
        required_keys = {"external_id", "url", "title", "company", "location",
                         "description_text", "easy_apply", "source", "hash"}
        for job in jobs:
            assert required_keys.issubset(job.keys()), f"Missing keys in {job}"

    @pytest.mark.asyncio
    async def test_discovered_external_ids(self) -> None:
        jobs = await discover_jobs(profile_name="default", mock=True)
        ids = [j["external_id"] for j in jobs]
        assert "mock-001" in ids
        assert "mock-002" in ids
        assert "mock-003" in ids

    @pytest.mark.asyncio
    async def test_discovered_jobs_have_distinct_hashes(self) -> None:
        jobs = await discover_jobs(profile_name="default", mock=True)
        hashes = [j["hash"] for j in jobs]
        assert len(set(hashes)) == 3

    @pytest.mark.asyncio
    async def test_easy_apply_varies(self) -> None:
        jobs = await discover_jobs(profile_name="default", mock=True)
        by_id = {j["external_id"]: j for j in jobs}
        # mock-001 and mock-002 have Easy Apply buttons, mock-003 does not
        assert by_id["mock-001"]["easy_apply"] is True
        assert by_id["mock-002"]["easy_apply"] is True
        assert by_id["mock-003"]["easy_apply"] is False

    @pytest.mark.asyncio
    async def test_description_text_populated(self) -> None:
        jobs = await discover_jobs(profile_name="default", mock=True)
        for job in jobs:
            assert len(job["description_text"]) > 20, f"Description too short for {job['external_id']}"


# ---------------------------------------------------------------------------
# Integration: discover → DB
# ---------------------------------------------------------------------------

class TestDiscoverToDb:
    @pytest.mark.asyncio
    async def test_discovered_jobs_persist_to_db(self) -> None:
        job_dicts = await discover_jobs(profile_name="default", mock=True)

        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)

        for jd in job_dicts:
            job = Job(**jd)
            upsert_job(session, job)

        session.commit()

        new_jobs = get_jobs_by_status(session, JobStatus.NEW)
        assert len(new_jobs) == 3

        titles = {j.title for j in new_jobs}
        assert "Senior Python Developer" in titles
        assert "Machine Learning Engineer" in titles
        assert "Data Scientist" in titles

    @pytest.mark.asyncio
    async def test_idempotent_discovery(self) -> None:
        """Discovering the same jobs twice should not create duplicates."""
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)

        for _round in range(2):
            job_dicts = await discover_jobs(profile_name="default", mock=True)
            for jd in job_dicts:
                job = Job(**jd)
                upsert_job(session, job)
            session.commit()

        all_jobs = get_jobs_by_status(session, JobStatus.NEW)
        assert len(all_jobs) == 3  # not 6

