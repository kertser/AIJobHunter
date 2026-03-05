"""Tests for the web GUI — FastAPI endpoints and pages."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from job_hunter.config.models import AppSettings
from job_hunter.db.models import Decision, Job, JobStatus, Score
from job_hunter.db.repo import get_memory_engine, init_db, make_session, save_score, upsert_job
from job_hunter.utils.hashing import job_hash
from job_hunter.web.app import create_app


@pytest.fixture()
def test_app(tmp_path: Path):
    """Create a test app with in-memory DB and sample data."""
    settings = AppSettings(data_dir=tmp_path, mock=True, dry_run=True)
    app = create_app(settings)

    # Pre-inject in-memory engine BEFORE the lifespan runs
    engine = get_memory_engine()
    init_db(engine)
    app.state.engine = engine

    # Seed sample data
    session = make_session(engine)
    jobs = [
        Job(
            external_id="w1", url="/j/w1", title="Python Dev", company="Acme",
            hash=job_hash(external_id="w1", title="Python Dev", company="Acme"),
            easy_apply=True, status=JobStatus.APPLIED, location="Remote",
            description_text="Python backend developer",
        ),
        Job(
            external_id="w2", url="/j/w2", title="Java Dev", company="Globex",
            hash=job_hash(external_id="w2", title="Java Dev", company="Globex"),
            easy_apply=True, status=JobStatus.QUEUED, location="NYC",
            description_text="Java enterprise developer",
        ),
        Job(
            external_id="w3", url="/j/w3", title="ML Engineer", company="Initech",
            hash=job_hash(external_id="w3", title="ML Engineer", company="Initech"),
            easy_apply=False, status=JobStatus.NEW, location="SF",
            description_text="ML pipeline engineer",
        ),
    ]
    for j in jobs:
        upsert_job(session, j)

    save_score(session, Score(
        job_hash=jobs[0].hash, embedding_similarity=0.65, llm_fit_score=85,
        missing_skills=["Docker"], risk_flags=[], decision=Decision.APPLY,
    ))
    session.commit()
    session.close()

    yield app


@pytest.fixture()
def client(test_app):
    with TestClient(test_app) as c:
        yield c


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class TestDashboard:
    def test_dashboard_page_loads(self, client: TestClient) -> None:
        r = client.get("/")
        assert r.status_code == 200
        assert "Dashboard" in r.text

    def test_dashboard_stats_api(self, client: TestClient) -> None:
        r = client.get("/api/stats/dashboard")
        assert r.status_code == 200
        data = r.json()
        assert data["total_jobs"] == 3
        assert data["applied_today"] >= 0


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

class TestJobs:
    def test_jobs_page_loads(self, client: TestClient) -> None:
        r = client.get("/jobs")
        assert r.status_code == 200
        assert "Python Dev" in r.text
        assert "Java Dev" in r.text

    def test_jobs_api_list(self, client: TestClient) -> None:
        r = client.get("/api/jobs")
        assert r.status_code == 200
        data = r.json()
        assert len(data["jobs"]) == 3

    def test_jobs_api_filter_by_status(self, client: TestClient) -> None:
        r = client.get("/api/jobs?status=queued")
        assert r.status_code == 200
        data = r.json()
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["title"] == "Java Dev"

    def test_jobs_api_filter_by_company(self, client: TestClient) -> None:
        r = client.get("/api/jobs?company=Acme")
        data = r.json()
        assert len(data["jobs"]) == 1

    def test_job_detail_page(self, client: TestClient) -> None:
        h = job_hash(external_id="w1", title="Python Dev", company="Acme")
        r = client.get(f"/api/jobs/{h}")
        assert r.status_code == 200
        assert "Python Dev" in r.text
        assert "85" in r.text  # fit score

    def test_job_detail_not_found(self, client: TestClient) -> None:
        r = client.get("/api/jobs/nonexistent")
        assert r.status_code == 404

    def test_patch_job_status(self, client: TestClient) -> None:
        h = job_hash(external_id="w3", title="ML Engineer", company="Initech")
        r = client.patch(f"/api/jobs/{h}/status", json={"status": "review"})
        assert r.status_code == 200
        assert r.json()["status"] == "review"

    def test_patch_job_invalid_status(self, client: TestClient) -> None:
        h = job_hash(external_id="w3", title="ML Engineer", company="Initech")
        r = client.patch(f"/api/jobs/{h}/status", json={"status": "invalid"})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class TestSettings:
    def test_settings_page_loads(self, client: TestClient) -> None:
        r = client.get("/settings")
        assert r.status_code == 200
        assert "Settings" in r.text

    def test_settings_api_get(self, client: TestClient) -> None:
        r = client.get("/api/settings")
        assert r.status_code == 200
        data = r.json()
        assert data["mock"] is True
        assert data["dry_run"] is True

    def test_settings_api_update(self, client: TestClient) -> None:
        r = client.put("/api/settings", json={"mock": False, "slowmo_ms": 500})
        assert r.status_code == 200
        # Verify
        r2 = client.get("/api/settings")
        data = r2.json()
        assert data["mock"] is False
        assert data["slowmo_ms"] == 500


# ---------------------------------------------------------------------------
# Run controls
# ---------------------------------------------------------------------------

class TestRunControls:
    def test_run_page_loads(self, client: TestClient) -> None:
        r = client.get("/run")
        assert r.status_code == 200
        assert "Run Controls" in r.text

    def test_task_status_api(self, client: TestClient) -> None:
        r = client.get("/api/run/task-status")
        assert r.status_code == 200
        assert r.json()["running"] is False


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

class TestReports:
    def test_reports_page_loads(self, client: TestClient) -> None:
        r = client.get("/reports")
        assert r.status_code == 200
        assert "Reports" in r.text

    def test_reports_api_empty(self, client: TestClient) -> None:
        r = client.get("/api/reports")
        assert r.status_code == 200
        assert r.json()["reports"] == []

    def test_report_not_found(self, client: TestClient) -> None:
        r = client.get("/api/reports/2099-01-01")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

class TestProfiles:
    def test_profiles_page_loads(self, client: TestClient) -> None:
        r = client.get("/profiles")
        assert r.status_code == 200
        assert "Profiles" in r.text

    def test_profiles_api_empty(self, client: TestClient) -> None:
        r = client.get("/api/profiles")
        assert r.status_code == 200
        data = r.json()
        assert data["profiles"] == []

    def test_user_profile_api_empty(self, client: TestClient) -> None:
        r = client.get("/api/user-profile")
        assert r.status_code == 200
        assert r.json()["user_profile"] is None

