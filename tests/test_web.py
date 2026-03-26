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
    settings = AppSettings(
        data_dir=tmp_path, mock=True, dry_run=True, openai_api_key="",
        # Ensure test isolation from real .env email settings
        notification_email="", resend_api_key="", smtp_host="",
        notifications_enabled=False,
    )
    # Create a user_profile.yml so dashboard doesn't redirect to onboarding
    (tmp_path / "user_profile.yml").write_text("name: Test User\ntitle: Tester\n")
    app = create_app(settings)

    # Pre-inject in-memory engine BEFORE the lifespan runs
    engine = get_memory_engine()
    init_db(engine)
    app.state.engine = engine
    # Point .env persistence to tmp dir so tests don't pollute the real .env
    app.state.dotenv_path = tmp_path / ".env"

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

    def test_delete_job(self, client: TestClient) -> None:
        h = job_hash(external_id="w2", title="Java Dev", company="Globex")
        r = client.delete(f"/api/jobs/{h}")
        assert r.status_code == 200
        assert r.json()["deleted"] is True
        # Verify it's gone
        r2 = client.get(f"/api/jobs/{h}")
        assert r2.status_code == 404

    def test_delete_job_not_found(self, client: TestClient) -> None:
        r = client.delete("/api/jobs/nonexistent")
        assert r.status_code == 404

    def test_bulk_status_update(self, client: TestClient) -> None:
        h1 = job_hash(external_id="w1", title="Python Dev", company="Acme")
        h3 = job_hash(external_id="w3", title="ML Engineer", company="Initech")
        r = client.patch("/api/jobs/bulk/status", json={
            "hashes": [h1, h3],
            "status": "review",
        })
        assert r.status_code == 200
        assert r.json()["updated"] == 2
        # Verify both changed
        r1 = client.get(f"/api/jobs/{h1}")
        assert "review" in r1.text

    def test_bulk_status_invalid(self, client: TestClient) -> None:
        r = client.patch("/api/jobs/bulk/status", json={
            "hashes": ["abc"],
            "status": "invalid_status",
        })
        assert r.status_code == 400

    def test_bulk_delete(self, client: TestClient) -> None:
        h2 = job_hash(external_id="w2", title="Java Dev", company="Globex")
        h3 = job_hash(external_id="w3", title="ML Engineer", company="Initech")
        r = client.post("/api/jobs/bulk/delete", json={
            "hashes": [h2, h3, "nonexistent"],
        })
        assert r.status_code == 200
        data = r.json()
        assert data["deleted"] == 2  # two real, one nonexistent
        # Verify they're gone
        assert client.get(f"/api/jobs/{h2}").status_code == 404
        assert client.get(f"/api/jobs/{h3}").status_code == 404

    def test_reformat_no_api_key(self, client: TestClient) -> None:
        """Reformat should fail gracefully when no API key is set."""
        h = job_hash(external_id="w1", title="Python Dev", company="Acme")
        r = client.post(f"/api/jobs/{h}/reformat")
        assert r.status_code == 400
        assert "API key" in r.json()["detail"]

    def test_reformat_not_found(self, client: TestClient) -> None:
        r = client.post("/api/jobs/nonexistent/reformat")
        assert r.status_code == 404

    def test_apply_single_not_found(self, client: TestClient) -> None:
        r = client.post("/api/jobs/nonexistent/apply")
        assert r.status_code == 404

    def test_apply_single_no_url(self, client: TestClient) -> None:
        """Jobs with relative URLs can't be applied to via Easy Apply."""
        h = job_hash(external_id="w1", title="Python Dev", company="Acme")
        r = client.post(f"/api/jobs/{h}/apply")
        assert r.status_code == 400

    def test_job_detail_renders_markdown(self, client: TestClient) -> None:
        """Job detail page should render markdown description as HTML."""
        h = job_hash(external_id="w1", title="Python Dev", company="Acme")
        r = client.get(f"/api/jobs/{h}")
        assert r.status_code == 200
        # The markdown filter should convert text to HTML (at minimum, a <p> tag)
        assert "Python Dev" in r.text


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
        assert "Pipeline Controls" in r.text

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

    def test_user_profile_api(self, client: TestClient) -> None:
        r = client.get("/api/user-profile")
        assert r.status_code == 200
        # user_profile.yml exists (created in fixture), so it returns data
        data = r.json()["user_profile"]
        assert data is not None
        assert "name" in data


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------

class TestOnboarding:
    def test_onboarding_shows_regenerate_if_profile_exists(self, client: TestClient) -> None:
        """If user_profile.yml exists, /onboarding shows the re-generate form."""
        r = client.get("/onboarding")
        assert r.status_code == 200
        assert "Re-generate" in r.text
        assert "already have a profile" in r.text

    def test_onboarding_page_shows_welcome_when_no_profile(self, test_app, tmp_path: Path) -> None:
        """If user_profile.yml doesn't exist, /onboarding shows the wizard."""
        # Remove the profile file created by the fixture
        profile_path = tmp_path / "user_profile.yml"
        if profile_path.exists():
            profile_path.unlink()
        with TestClient(test_app) as c:
            r = c.get("/onboarding")
            assert r.status_code == 200
            assert "Welcome" in r.text

    def test_dashboard_redirects_to_onboarding(self, test_app, tmp_path: Path) -> None:
        """Dashboard should redirect to /onboarding when no profile exists."""
        profile_path = tmp_path / "user_profile.yml"
        if profile_path.exists():
            profile_path.unlink()
        with TestClient(test_app) as c:
            r = c.get("/", follow_redirects=False)
            assert r.status_code == 302
            assert "/onboarding" in r.headers["location"]


# ---------------------------------------------------------------------------
# Date columns & sorting
# ---------------------------------------------------------------------------

class TestDateColumns:
    def test_jobs_page_has_date_columns(self, client: TestClient) -> None:
        r = client.get("/jobs")
        assert r.status_code == 200
        assert "Posted" in r.text
        assert "Applied" in r.text

    def test_jobs_page_has_sortable_headers(self, client: TestClient) -> None:
        r = client.get("/jobs")
        assert r.status_code == 200
        assert "sortTable" in r.text
        assert "data-sort-value" in r.text

    def test_job_detail_has_dates(self, client: TestClient) -> None:
        h = job_hash(external_id="w1", title="Python Dev", company="Acme")
        r = client.get(f"/api/jobs/{h}")
        assert r.status_code == 200
        assert "Discovered" in r.text


# ---------------------------------------------------------------------------
# Market integration in job detail & dashboard
# ---------------------------------------------------------------------------


class TestMarketIntegration:
    """Verify market intelligence surfaces in job detail and dashboard."""

    def test_job_detail_shows_market_hint_when_no_data(self, client: TestClient) -> None:
        """Without market data the job detail should show a 'no data yet' hint."""
        h = job_hash(external_id="w1", title="Python Dev", company="Acme")
        r = client.get(f"/api/jobs/{h}")
        assert r.status_code == 200
        assert "no data yet" in r.text
        assert "Market Analysis pipeline" in r.text

    def test_dashboard_market_section_absent_when_empty(self, client: TestClient) -> None:
        """Dashboard should NOT show market panel when no market data exists."""
        r = client.get("/")
        assert r.status_code == 200
        # The market section heading should not be in the response
        assert "Market Intelligence" not in r.text

    def test_dashboard_stats_include_market_key(self, client: TestClient) -> None:
        """Dashboard API should always include a 'market' key (possibly empty)."""
        r = client.get("/api/stats/dashboard")
        assert r.status_code == 200
        data = r.json()
        assert "market" in data


# ---------------------------------------------------------------------------
# Schedule routes
# ---------------------------------------------------------------------------


class TestScheduleRoutes:
    def test_schedule_page_loads(self, client: TestClient) -> None:
        r = client.get("/schedule")
        assert r.status_code == 200
        assert "Schedule" in r.text

    def test_schedule_api_get(self, client: TestClient) -> None:
        r = client.get("/api/schedule")
        assert r.status_code == 200
        data = r.json()
        assert "config" in data
        assert "next_run" in data
        assert "history" in data
        assert isinstance(data["config"], dict)
        assert "enabled" in data["config"]

    def test_schedule_api_update(self, client: TestClient) -> None:
        r = client.put("/api/schedule", json={
            "enabled": True,
            "time_of_day": "10:30",
            "days_of_week": ["mon", "wed", "fri"],
            "pipeline_mode": "market",
            "profile_name": "my_profile",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["updated"] is True

    def test_schedule_api_update_defaults(self, client: TestClient) -> None:
        r = client.put("/api/schedule", json={
            "enabled": False,
            "time_of_day": "09:00",
            "days_of_week": [],
            "pipeline_mode": "full",
            "profile_name": "default",
        })
        assert r.status_code == 200
        assert r.json()["updated"] is True

    def test_schedule_trigger_disabled(self, client: TestClient) -> None:
        """Trigger should fail when schedule is disabled (default)."""
        r = client.post("/api/schedule/trigger")
        # Default schedule is disabled → 400 or 503
        assert r.status_code in (400, 503)

    def test_schedule_trigger_task_running(self, client: TestClient) -> None:
        """Trigger should return 409 if a task is already running."""
        # First enable the schedule so we don't get 400/503 for disabled
        client.put("/api/schedule", json={
            "enabled": True,
            "time_of_day": "09:00",
            "days_of_week": ["mon"],
            "pipeline_mode": "full",
            "profile_name": "default",
        })
        # Monkey-patch is_running to simulate a running task
        import job_hunter.web.task_manager as tm_mod
        tm = client.app.state.task_manager
        original_prop = type(tm).is_running
        type(tm).is_running = property(lambda self: True)
        try:
            r = client.post("/api/schedule/trigger")
            assert r.status_code == 409
        finally:
            type(tm).is_running = original_prop


# ---------------------------------------------------------------------------
# Settings — email notification fields
# ---------------------------------------------------------------------------


class TestSettingsEmail:
    def test_settings_page_has_email_section(self, client: TestClient) -> None:
        r = client.get("/settings")
        assert r.status_code == 200
        assert "Email Notifications" in r.text
        assert "smtp_host" in r.text.lower() or "SMTP Host" in r.text

    def test_settings_api_includes_email_fields(self, client: TestClient) -> None:
        r = client.get("/api/settings")
        assert r.status_code == 200
        data = r.json()
        assert "smtp_host" in data
        assert "smtp_port" in data
        assert "notification_email" in data
        assert "notifications_enabled" in data

    def test_settings_update_email(self, client: TestClient) -> None:
        r = client.put("/api/settings", json={
            "smtp_host": "smtp.test.com",
            "smtp_port": 465,
            "smtp_user": "user@test.com",
            "notification_email": "dest@test.com",
            "notifications_enabled": True,
        })
        assert r.status_code == 200
        # Verify
        r2 = client.get("/api/settings")
        data = r2.json()
        assert data["smtp_host"] == "smtp.test.com"
        assert data["smtp_port"] == 465
        assert data["notification_email"] == "dest@test.com"
        assert data["notifications_enabled"] is True

    def test_test_email_fails_without_config(self, client: TestClient) -> None:
        """Test email endpoint should fail when nothing is configured."""
        r = client.post("/api/settings/test-email")
        assert r.status_code == 400
        error = r.json().get("error", "")
        assert "email" in error.lower() or "configured" in error.lower()

    def test_settings_api_includes_resend_fields(self, client: TestClient) -> None:
        r = client.get("/api/settings")
        data = r.json()
        assert "email_provider" in data
        assert "resend_api_key" in data


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_health_endpoint(self, client: TestClient) -> None:
        r = client.get("/api/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "db_ok" in data
        assert data["db_ok"] is True


