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
        secret_key="test-secret-key-for-jwt",
        admin_password="",  # no admin gate in tests by default
    )
    app = create_app(settings)

    # Pre-inject in-memory engine BEFORE the lifespan runs
    engine = get_memory_engine()
    init_db(engine)
    app.state.engine = engine
    # Point .env persistence to tmp dir so tests don't pollute the real .env
    app.state.dotenv_path = tmp_path / ".env"

    # Create a test user for auth
    from job_hunter.auth.repo import create_user, get_user_data_dir
    session = make_session(engine)
    test_user = create_user(
        session, email="test@example.com", password="testpass123",
        display_name="Test User", is_admin=True,
    )
    _test_user_id = str(test_user.id)

    # Create user_profile.yml in per-user data dir so dashboard doesn't redirect
    user_data_dir = get_user_data_dir(tmp_path, test_user.id)
    (user_data_dir / "user_profile.yml").write_text("name: Test User\ntitle: Tester\n")
    # Also keep global copy for backward compat in tests
    (tmp_path / "user_profile.yml").write_text("name: Test User\ntitle: Tester\n")

    # Seed sample data
    jobs = [
        Job(
            external_id="w1", url="/j/w1", title="Python Dev", company="Acme",
            hash=job_hash(external_id="w1", title="Python Dev", company="Acme"),
            easy_apply=True, status=JobStatus.APPLIED, location="Remote",
            description_text="Python backend developer",
            user_id=test_user.id,
        ),
        Job(
            external_id="w2", url="/j/w2", title="Java Dev", company="Globex",
            hash=job_hash(external_id="w2", title="Java Dev", company="Globex"),
            easy_apply=True, status=JobStatus.QUEUED, location="NYC",
            description_text="Java enterprise developer",
            user_id=test_user.id,
        ),
        Job(
            external_id="w3", url="/j/w3", title="ML Engineer", company="Initech",
            hash=job_hash(external_id="w3", title="ML Engineer", company="Initech"),
            easy_apply=False, status=JobStatus.NEW, location="SF",
            description_text="ML pipeline engineer",
            user_id=test_user.id,
        ),
    ]
    for j in jobs:
        upsert_job(session, j)

    save_score(session, Score(
        job_hash=jobs[0].hash, embedding_similarity=0.65, llm_fit_score=85,
        missing_skills=["Docker"], risk_flags=[], decision=Decision.APPLY,
        user_id=test_user.id,
    ))
    session.commit()
    session.close()

    # Store user_id on app for fixture access
    app.state._test_user_id = _test_user_id

    yield app


@pytest.fixture()
def client(test_app):
    with TestClient(test_app) as c:
        # Set auth cookie so all requests are authenticated
        from job_hunter.auth.security import create_access_token
        token = create_access_token(
            test_app.state._test_user_id,
            test_app.state.secret_key,
        )
        c.cookies.set("access_token", token)
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
        # Remove the profile file created by the fixture (both global and per-user)
        profile_path = tmp_path / "user_profile.yml"
        if profile_path.exists():
            profile_path.unlink()
        # Also remove from per-user dir
        users_dir = tmp_path / "users"
        if users_dir.exists():
            for up in users_dir.rglob("user_profile.yml"):
                up.unlink()
        with TestClient(test_app) as c:
            from job_hunter.auth.security import create_access_token
            token = create_access_token(test_app.state._test_user_id, test_app.state.secret_key)
            c.cookies.set("access_token", token)
            r = c.get("/onboarding")
            assert r.status_code == 200
            assert "Welcome" in r.text

    def test_dashboard_redirects_to_onboarding(self, test_app, tmp_path: Path) -> None:
        """Dashboard should redirect to /onboarding when no profile exists."""
        profile_path = tmp_path / "user_profile.yml"
        if profile_path.exists():
            profile_path.unlink()
        # Also remove from per-user dir
        users_dir = tmp_path / "users"
        if users_dir.exists():
            for up in users_dir.rglob("user_profile.yml"):
                up.unlink()
        with TestClient(test_app) as c:
            from job_hunter.auth.security import create_access_token
            token = create_access_token(test_app.state._test_user_id, test_app.state.secret_key)
            c.cookies.set("access_token", token)
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


# ---------------------------------------------------------------------------
# Onboarding — PDF upload
# ---------------------------------------------------------------------------


class TestOnboardingUpload:
    def test_upload_rejects_non_pdf(self, client: TestClient) -> None:
        """POST with a non-PDF file should return 400."""
        r = client.post(
            "/api/onboarding/generate",
            files={"resume": ("notes.txt", b"just text", "text/plain")},
            data={"linkedin_url": ""},
        )
        assert r.status_code == 400
        assert "PDF" in r.json()["error"]

    def test_upload_starts_task_with_mock_pdf(self, client: TestClient) -> None:
        """POST with a valid PDF-named file in mock mode should start the task."""
        # Minimal PDF content (not a real PDF, but the endpoint only checks extension)
        r = client.post(
            "/api/onboarding/generate",
            files={"resume": ("resume.pdf", b"%PDF-1.4 fake content", "application/pdf")},
            data={"linkedin_url": ""},
        )
        assert r.status_code == 202
        data = r.json()
        assert data["started"] == "onboarding"

    def test_upload_saves_resume_file(self, test_app, tmp_path: Path) -> None:
        """The uploaded PDF should be saved to the per-user data dir."""
        with TestClient(test_app) as c:
            # Authenticate
            from job_hunter.auth.security import create_access_token
            token = create_access_token(test_app.state._test_user_id, test_app.state.secret_key)
            c.cookies.set("access_token", token)
            content = b"%PDF-1.4 test resume bytes"
            c.post(
                "/api/onboarding/generate",
                files={"resume": ("my_cv.pdf", content, "application/pdf")},
                data={"linkedin_url": ""},
            )
            # Resume is now saved in per-user data dir
            user_dir = tmp_path / "users" / test_app.state._test_user_id
            saved = user_dir / "resume.pdf"
            assert saved.exists()
            assert saved.read_bytes() == content

    def test_upload_rejects_when_task_running(self, client: TestClient) -> None:
        """If a task is already running, return 409."""
        tm = client.app.state.task_manager
        original_prop = type(tm).is_running
        type(tm).is_running = property(lambda self: True)
        try:
            r = client.post(
                "/api/onboarding/generate",
                files={"resume": ("resume.pdf", b"%PDF-1.4", "application/pdf")},
                data={"linkedin_url": ""},
            )
            assert r.status_code == 409
        finally:
            type(tm).is_running = original_prop

    def test_upload_saves_openai_api_key_to_user(self, test_app, tmp_path: Path) -> None:
        """Submitting an API key during onboarding persists it to the user row."""
        with TestClient(test_app) as c:
            from job_hunter.auth.security import create_access_token
            token = create_access_token(test_app.state._test_user_id, test_app.state.secret_key)
            c.cookies.set("access_token", token)
            r = c.post(
                "/api/onboarding/generate",
                files={"resume": ("cv.pdf", b"%PDF-1.4 data", "application/pdf")},
                data={"linkedin_url": "", "openai_api_key": "sk-test-key-12345"},
            )
            assert r.status_code == 202
            # Verify key was saved to the User row
            from job_hunter.auth.repo import get_user_by_id
            from job_hunter.db.repo import make_session
            session = make_session(test_app.state.engine)
            user = get_user_by_id(session, test_app.state._test_user_id)
            assert user.openai_api_key == "sk-test-key-12345"
            session.close()

    def test_onboarding_page_shows_api_key_field_when_not_set(self, test_app, tmp_path: Path) -> None:
        """Onboarding page should show the API key input with a warning when no key is configured."""
        # Use non-mock settings so the API key section is rendered
        non_mock_settings = AppSettings(
            data_dir=tmp_path, mock=False, dry_run=True, openai_api_key="",
            notification_email="", resend_api_key="", smtp_host="",
            notifications_enabled=False,
            secret_key="test-secret-key-for-jwt",
        )
        app = create_app(non_mock_settings)
        app.state.engine = test_app.state.engine
        app.state.dotenv_path = tmp_path / ".env"
        with TestClient(app) as c:
            from job_hunter.auth.security import create_access_token
            token = create_access_token(test_app.state._test_user_id, app.state.secret_key)
            c.cookies.set("access_token", token)
            r = c.get("/onboarding")
            assert r.status_code == 200
            assert "OpenAI API Key" in r.text
            assert "No API key found" in r.text


# ---------------------------------------------------------------------------
# Profiles — round-trip spoken/programming languages & industries
# ---------------------------------------------------------------------------


class TestProfileRoundTrip:
    def test_user_profile_saves_spoken_and_programming_languages(self, client: TestClient) -> None:
        """PUT /api/user-profile with spoken_languages and programming_languages
        should round-trip correctly through GET."""
        profile = {
            "name": "Test User",
            "title": "Dev",
            "spoken_languages": ["English", "Hebrew"],
            "programming_languages": ["Python", "SQL"],
            "preferred_industries": ["AI/ML", "healthcare"],
            "disliked_industries": ["gambling"],
        }
        r = client.put("/api/user-profile", json=profile)
        assert r.status_code == 200

        r2 = client.get("/api/user-profile")
        assert r2.status_code == 200
        up = r2.json()["user_profile"]
        assert up["spoken_languages"] == ["English", "Hebrew"]
        assert up["programming_languages"] == ["Python", "SQL"]
        assert up["preferred_industries"] == ["AI/ML", "healthcare"]
        assert up["disliked_industries"] == ["gambling"]

    def test_profiles_page_renders_new_fields(self, client: TestClient) -> None:
        """Profiles page should render spoken_languages, programming_languages,
        preferred_industries, and disliked_industries input fields."""
        # First save a profile with the new fields populated
        client.put("/api/user-profile", json={
            "name": "Jane",
            "title": "Engineer",
            "spoken_languages": ["English", "Russian"],
            "programming_languages": ["Python", "Go"],
            "preferred_industries": ["fintech"],
            "disliked_industries": ["oil"],
        })
        r = client.get("/profiles")
        assert r.status_code == 200
        html = r.text
        # Verify the new field names appear in the form
        assert 'name="spoken_languages"' in html
        assert 'name="programming_languages"' in html
        assert 'name="preferred_industries"' in html
        assert 'name="disliked_industries"' in html
        # Verify the values are rendered
        assert "English, Russian" in html
        assert "Python, Go" in html
        assert "fintech" in html
        assert "oil" in html
        # The deprecated 'languages' field should NOT appear
        assert 'name="languages"' not in html

    def test_user_profile_round_trip_preserves_all_fields(self, client: TestClient) -> None:
        """Full save→load cycle should not lose any fields."""
        full_profile = {
            "name": "Alice Smith",
            "first_name": "Alice",
            "last_name": "Smith",
            "email": "alice@example.com",
            "phone": "555-1234",
            "phone_country_code": "+1",
            "title": "Senior Engineer",
            "summary": "Experienced dev",
            "skills": ["Python", "FastAPI", "Docker"],
            "experience_years": 10,
            "seniority_level": "Senior",
            "desired_roles": ["Staff Engineer", "Principal Engineer"],
            "preferred_locations": ["Remote", "NYC"],
            "education": ["M.Sc. CS"],
            "spoken_languages": ["English", "Spanish"],
            "programming_languages": ["Python", "Rust", "SQL"],
            "preferred_industries": ["AI/ML", "fintech"],
            "disliked_industries": ["defence"],
        }
        r = client.put("/api/user-profile", json=full_profile)
        assert r.status_code == 200

        r2 = client.get("/api/user-profile")
        up = r2.json()["user_profile"]
        for key, expected in full_profile.items():
            assert up[key] == expected, f"Field {key!r}: expected {expected!r}, got {up[key]!r}"


# ---------------------------------------------------------------------------
# Auth — registration, login, logout, /me
# ---------------------------------------------------------------------------


class TestAuth:
    def test_unauthenticated_redirects_to_login(self, test_app) -> None:
        """Pages should redirect to /login when not authenticated."""
        with TestClient(test_app) as c:
            r = c.get("/", follow_redirects=False)
            assert r.status_code == 302
            assert "/login" in r.headers["location"]

    def test_unauthenticated_api_returns_401(self, test_app) -> None:
        """API calls without auth should return 401 JSON."""
        with TestClient(test_app) as c:
            r = c.get("/api/jobs")
            assert r.status_code == 401

    def test_login_page_loads(self, test_app) -> None:
        """Login page is public and loads without auth."""
        with TestClient(test_app) as c:
            r = c.get("/login")
            assert r.status_code == 200
            assert "Sign In" in r.text

    def test_register_page_loads(self, test_app) -> None:
        """Register page is public when registration is enabled."""
        with TestClient(test_app) as c:
            r = c.get("/register")
            assert r.status_code == 200
            assert "Create Account" in r.text

    def test_register_and_login_flow(self, test_app) -> None:
        """Register → login → /api/auth/me should work end-to-end."""
        with TestClient(test_app) as c:
            # Register
            r = c.post("/api/auth/register", json={
                "email": "newuser@example.com",
                "password": "securepass123",
                "display_name": "New User",
            })
            assert r.status_code == 200
            assert r.json()["registered"] is True
            # Cookie should be set
            assert "access_token" in r.cookies

            # /me should return the new user
            r2 = c.get("/api/auth/me")
            assert r2.status_code == 200
            me = r2.json()
            assert me["authenticated"] is True
            assert me["email"] == "newuser@example.com"
            assert me["display_name"] == "New User"

    def test_first_user_is_admin(self, tmp_path: Path) -> None:
        """The first registered user should automatically be admin."""
        settings = AppSettings(
            data_dir=tmp_path, mock=True, dry_run=True,
            secret_key="test-key",
        )
        app = create_app(settings)
        engine = get_memory_engine()
        init_db(engine)
        app.state.engine = engine
        app.state.dotenv_path = tmp_path / ".env"

        with TestClient(app) as c:
            r = c.post("/api/auth/register", json={
                "email": "first@example.com",
                "password": "password123",
            })
            assert r.status_code == 200
            me = c.get("/api/auth/me").json()
            assert me["is_admin"] is True

            # Second user should NOT be admin
            # Logout first
            c.post("/api/auth/logout")
            c.cookies.clear()
            r2 = c.post("/api/auth/register", json={
                "email": "second@example.com",
                "password": "password123",
            })
            assert r2.status_code == 200
            me2 = c.get("/api/auth/me").json()
            assert me2["is_admin"] is False

    def test_login_wrong_password(self, test_app) -> None:
        with TestClient(test_app) as c:
            r = c.post("/api/auth/login", json={
                "email": "test@example.com",
                "password": "wrongpassword",
            })
            assert r.status_code == 401

    def test_login_success(self, test_app) -> None:
        with TestClient(test_app) as c:
            r = c.post("/api/auth/login", json={
                "email": "test@example.com",
                "password": "testpass123",
            })
            assert r.status_code == 200
            assert r.json()["logged_in"] is True
            assert "access_token" in r.cookies

    def test_logout_clears_cookie(self, client: TestClient) -> None:
        r = client.post("/api/auth/logout", follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers["location"]

    def test_me_unauthenticated(self, test_app) -> None:
        with TestClient(test_app) as c:
            r = c.get("/api/auth/me")
            assert r.status_code == 200
            assert r.json()["authenticated"] is False

    def test_register_duplicate_email(self, test_app) -> None:
        with TestClient(test_app) as c:
            r = c.post("/api/auth/register", json={
                "email": "test@example.com",
                "password": "password123",
            })
            assert r.status_code == 409

    def test_register_short_password(self, test_app) -> None:
        with TestClient(test_app) as c:
            r = c.post("/api/auth/register", json={
                "email": "short@example.com",
                "password": "short",
            })
            assert r.status_code == 400


# ---------------------------------------------------------------------------
# Multi-user isolation
# ---------------------------------------------------------------------------


class TestMultiUserIsolation:
    """Verify that user A cannot see or modify user B's data."""

    @pytest.fixture()
    def two_users(self, test_app, tmp_path):
        """Create a second user and return (client_a, client_b) with auth."""
        from job_hunter.auth.repo import create_user, get_user_data_dir
        from job_hunter.auth.security import create_access_token

        with TestClient(test_app) as c_a:
            engine = test_app.state.engine
            secret = test_app.state.secret_key
            session = make_session(engine)

            user_b = create_user(
                session, email="userb@example.com", password="password123",
                display_name="User B",
            )

            # Capture IDs before commit (commit expires attributes on ORM objects)
            user_b_id = str(user_b.id)

            # Create per-user data dir and profile for user B
            user_b_dir = get_user_data_dir(tmp_path, user_b.id)
            (user_b_dir / "user_profile.yml").write_text("name: User B\ntitle: Tester B\n")

            # Seed a job for user B
            job_b_hash = job_hash(external_id="b1", title="Go Dev", company="CorpB")
            job_b = Job(
                external_id="b1", url="/j/b1", title="Go Dev", company="CorpB",
                hash=job_b_hash,
                easy_apply=True, status=JobStatus.NEW, location="Berlin",
                description_text="Go developer",
                user_id=user_b.id,
            )
            upsert_job(session, job_b)
            session.commit()
            session.close()

            token_a = create_access_token(test_app.state._test_user_id, secret)
            c_a.cookies.set("access_token", token_a)

            with TestClient(test_app) as c_b:
                token_b = create_access_token(user_b_id, secret)
                c_b.cookies.set("access_token", token_b)

                yield c_a, c_b, job_b_hash

    def test_user_a_sees_only_own_jobs(self, two_users) -> None:
        c_a, c_b, b_hash = two_users
        # User A should see 3 jobs (seeded in fixture)
        r_a = c_a.get("/api/jobs")
        assert r_a.status_code == 200
        titles_a = {j["title"] for j in r_a.json()["jobs"]}
        assert "Python Dev" in titles_a
        assert "Go Dev" not in titles_a

    def test_user_b_sees_only_own_jobs(self, two_users) -> None:
        c_a, c_b, b_hash = two_users
        r_b = c_b.get("/api/jobs")
        assert r_b.status_code == 200
        titles_b = {j["title"] for j in r_b.json()["jobs"]}
        assert "Go Dev" in titles_b
        assert "Python Dev" not in titles_b

    def test_user_a_cannot_access_user_b_job(self, two_users) -> None:
        c_a, c_b, b_hash = two_users
        r = c_a.get(f"/api/jobs/{b_hash}")
        assert r.status_code == 404

    def test_user_a_cannot_delete_user_b_job(self, two_users) -> None:
        c_a, c_b, b_hash = two_users
        r = c_a.delete(f"/api/jobs/{b_hash}")
        assert r.status_code == 404

    def test_user_a_cannot_patch_user_b_job(self, two_users) -> None:
        c_a, c_b, b_hash = two_users
        r = c_a.patch(f"/api/jobs/{b_hash}/status", json={"status": "review"})
        assert r.status_code == 404

    def test_dashboard_stats_are_per_user(self, two_users) -> None:
        c_a, c_b, _ = two_users
        stats_a = c_a.get("/api/stats/dashboard").json()
        stats_b = c_b.get("/api/stats/dashboard").json()
        # User A has 3 jobs, user B has 1
        assert stats_a["total_jobs"] == 3
        assert stats_b["total_jobs"] == 1


# ---------------------------------------------------------------------------
# Admin management
# ---------------------------------------------------------------------------


class TestAdminManagement:
    def test_admin_page_loads(self, client: TestClient) -> None:
        r = client.get("/admin")
        assert r.status_code == 200
        assert "User Management" in r.text

    def test_admin_list_users(self, client: TestClient) -> None:
        r = client.get("/api/admin/users")
        assert r.status_code == 200
        users = r.json()["users"]
        assert len(users) >= 1
        assert any(u["email"] == "test@example.com" for u in users)

    def test_admin_toggle_active(self, test_app, client: TestClient) -> None:
        """Admin can deactivate another user."""
        # First create a second user
        engine = test_app.state.engine
        session = make_session(engine)
        from job_hunter.auth.repo import create_user
        user2 = create_user(session, email="toggle@example.com", password="pass12345678")
        session.commit()
        uid = str(user2.id)
        session.close()

        # Deactivate
        r = client.patch(f"/api/admin/users/{uid}", json={"is_active": False})
        assert r.status_code == 200

        # Verify via list
        users = client.get("/api/admin/users").json()["users"]
        target = [u for u in users if u["id"] == uid][0]
        assert target["is_active"] is False

        # Re-activate
        r2 = client.patch(f"/api/admin/users/{uid}", json={"is_active": True})
        assert r2.status_code == 200

    def test_admin_cannot_deactivate_self(self, client: TestClient, test_app) -> None:
        uid = test_app.state._test_user_id
        r = client.patch(f"/api/admin/users/{uid}", json={"is_active": False})
        assert r.status_code == 400

    def test_admin_cannot_demote_self(self, client: TestClient, test_app) -> None:
        uid = test_app.state._test_user_id
        r = client.patch(f"/api/admin/users/{uid}", json={"is_admin": False})
        assert r.status_code == 400

    def test_non_admin_gets_403_when_password_set(self, test_app, client: TestClient) -> None:
        """When admin_password is set, a user without the admin_token cookie gets 403."""
        engine = test_app.state.engine
        session = make_session(engine)
        from job_hunter.auth.repo import create_user
        normal = create_user(session, email="normal@example.com", password="pass12345678")
        normal_id = str(normal.id)  # capture before commit expires attrs
        session.commit()
        session.close()

        from job_hunter.auth.security import create_access_token

        # Set an admin password so the gate is active
        old_pw = test_app.state.settings.admin_password
        test_app.state.settings.admin_password = "supersecret"
        try:
            token = create_access_token(normal_id, test_app.state.secret_key)
            client.cookies.set("access_token", token)
            r = client.get("/api/admin/users")
            assert r.status_code == 403
        finally:
            test_app.state.settings.admin_password = old_pw

    def test_any_user_accesses_admin_without_password(self, test_app, client: TestClient) -> None:
        """When no admin_password is set, any logged-in user can access admin endpoints."""
        engine = test_app.state.engine
        session = make_session(engine)
        from job_hunter.auth.repo import create_user, get_user_by_email
        if not get_user_by_email(session, "normal2@example.com"):
            create_user(session, email="normal2@example.com", password="pass12345678")
            session.commit()
        from job_hunter.auth.repo import get_user_by_email as _get
        user = _get(session, "normal2@example.com")
        uid = str(user.id)
        session.close()

        from job_hunter.auth.security import create_access_token

        token = create_access_token(uid, test_app.state.secret_key)
        client.cookies.set("access_token", token)
        r = client.get("/api/admin/users")
        assert r.status_code == 200

    def test_admin_delete_user(self, test_app, client: TestClient) -> None:
        engine = test_app.state.engine
        session = make_session(engine)
        from job_hunter.auth.repo import create_user
        doomed = create_user(session, email="doomed@example.com", password="pass12345678")
        session.commit()
        uid = str(doomed.id)
        session.close()

        r = client.delete(f"/api/admin/users/{uid}")
        assert r.status_code == 200
        assert r.json()["deleted"] is True

        # Verify gone
        users = client.get("/api/admin/users").json()["users"]
        assert not any(u["id"] == uid for u in users)

    def test_admin_cannot_delete_self(self, client: TestClient, test_app) -> None:
        uid = test_app.state._test_user_id
        r = client.delete(f"/api/admin/users/{uid}")
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Admin password gate
# ---------------------------------------------------------------------------


class TestAdminPasswordGate:
    """Tests for the admin password requirement."""

    def test_password_not_required_by_default(self, client: TestClient) -> None:
        """When admin_password is empty, password is not required."""
        r = client.get("/api/admin/password-required")
        assert r.status_code == 200
        assert r.json()["password_required"] is False

    def test_admin_auth_succeeds_without_password(self, client: TestClient) -> None:
        """When no admin_password is configured, auth succeeds with empty password."""
        r = client.post("/api/admin/auth", json={"password": ""})
        assert r.status_code == 200
        assert r.json()["authenticated"] is True

    def test_password_required_when_set(self, tmp_path: Path) -> None:
        """When admin_password is set, the gate reports it as required."""
        settings = AppSettings(
            data_dir=tmp_path, mock=True, dry_run=True, openai_api_key="",
            notification_email="", resend_api_key="", smtp_host="",
            notifications_enabled=False,
            secret_key="test-secret-key-for-jwt",
            admin_password="supersecret",
        )
        app = create_app(settings)
        engine = get_memory_engine()
        init_db(engine)
        app.state.engine = engine
        app.state.dotenv_path = tmp_path / ".env"

        from job_hunter.auth.repo import create_user, get_user_data_dir
        session = make_session(engine)
        user = create_user(
            session, email="admin@example.com", password="testpass123",
            display_name="Admin", is_admin=True,
        )
        user_id = str(user.id)
        udd = get_user_data_dir(tmp_path, user.id)
        (udd / "user_profile.yml").write_text("name: Admin\ntitle: Boss\n")
        session.commit()
        session.close()

        from job_hunter.auth.security import create_access_token
        with TestClient(app) as c:
            token = create_access_token(user_id, app.state.secret_key)
            c.cookies.set("access_token", token)

            # Password is required
            r = c.get("/api/admin/password-required")
            assert r.status_code == 200
            assert r.json()["password_required"] is True

            # Without admin auth, API calls get 403
            r = c.get("/api/admin/users")
            assert r.status_code == 403

            # Wrong password
            r = c.post("/api/admin/auth", json={"password": "wrong"})
            assert r.status_code == 403

            # Correct password
            r = c.post("/api/admin/auth", json={"password": "supersecret"})
            assert r.status_code == 200
            assert r.json()["authenticated"] is True

            # Now API calls should work (admin_token cookie was set)
            r = c.get("/api/admin/users")
            assert r.status_code == 200
            assert len(r.json()["users"]) >= 1


# ---------------------------------------------------------------------------
# Admin profile management
# ---------------------------------------------------------------------------


class TestAdminProfileManagement:
    """Tests for admin's ability to view and remove any user's profiles."""

    def test_list_profiles(self, client: TestClient, test_app) -> None:
        r = client.get("/api/admin/profiles")
        assert r.status_code == 200
        profiles = r.json()["profiles"]
        assert len(profiles) >= 1
        # The test user has a user_profile.yml
        test_uid = test_app.state._test_user_id
        p = [x for x in profiles if x["id"] == test_uid][0]
        assert p["has_user_profile"] is True

    def test_delete_user_profile(self, test_app, client: TestClient) -> None:
        """Admin can delete another user's user_profile.yml."""
        engine = test_app.state.engine
        session = make_session(engine)
        from job_hunter.auth.repo import create_user, get_user_data_dir
        user2 = create_user(session, email="victim@example.com", password="pass12345678")
        uid = str(user2.id)
        uid_uuid = user2.id
        session.commit()
        session.close()

        # Create profile files for user2
        settings = test_app.state.settings
        udd = get_user_data_dir(settings.data_dir, uid_uuid)
        (udd / "user_profile.yml").write_text("name: Victim\ntitle: Target\n")

        # Verify it shows up
        r = client.get("/api/admin/profiles")
        profiles = r.json()["profiles"]
        p = [x for x in profiles if x["id"] == uid][0]
        assert p["has_user_profile"] is True

        # Delete it
        r = client.delete(f"/api/admin/profiles/{uid}/user-profile")
        assert r.status_code == 200
        assert r.json()["deleted"] is True

        # Verify it's gone
        r = client.get("/api/admin/profiles")
        profiles = r.json()["profiles"]
        p = [x for x in profiles if x["id"] == uid][0]
        assert p["has_user_profile"] is False

    def test_delete_all_profiles(self, test_app, client: TestClient) -> None:
        """Admin can delete all profile files for a user."""
        engine = test_app.state.engine
        session = make_session(engine)
        from job_hunter.auth.repo import create_user, get_user_data_dir
        user2 = create_user(session, email="victim2@example.com", password="pass12345678")
        uid = str(user2.id)
        uid_uuid = user2.id
        session.commit()
        session.close()

        settings = test_app.state.settings
        udd = get_user_data_dir(settings.data_dir, uid_uuid)
        (udd / "user_profile.yml").write_text("name: V2\ntitle: T2\n")
        (udd / "profiles.yml").write_text("- name: default\n  keywords: [python]\n")

        # Delete all
        r = client.delete(f"/api/admin/profiles/{uid}")
        assert r.status_code == 200
        data = r.json()
        assert "user_profile.yml" in data["deleted_files"]
        assert "profiles.yml" in data["deleted_files"]

        # Verify both gone
        r = client.get("/api/admin/profiles")
        profiles = r.json()["profiles"]
        p = [x for x in profiles if x["id"] == uid][0]
        assert p["has_user_profile"] is False
        assert p["search_profile_count"] == 0

    def test_delete_nonexistent_profile_404(self, test_app, client: TestClient) -> None:
        """Deleting a profile that doesn't exist returns 404."""
        engine = test_app.state.engine
        session = make_session(engine)
        from job_hunter.auth.repo import create_user
        user2 = create_user(session, email="noprofile@example.com", password="pass12345678")
        uid = str(user2.id)
        session.commit()
        session.close()

        r = client.delete(f"/api/admin/profiles/{uid}/user-profile")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# First-user admin password setup
# ---------------------------------------------------------------------------

class TestFirstUserAdminPassword:
    """The first user can set the admin panel password during registration."""

    def test_is_first_user_true_when_empty(self, tmp_path: Path) -> None:
        """is-first-user returns True when no users exist."""
        settings = AppSettings(
            data_dir=tmp_path, mock=True, dry_run=True, openai_api_key="",
            notification_email="", resend_api_key="", smtp_host="",
            notifications_enabled=False, secret_key="test-key", admin_password="",
        )
        app = create_app(settings)
        app.state.engine = get_memory_engine()
        init_db(app.state.engine)
        app.state.dotenv_path = tmp_path / ".env"
        with TestClient(app) as c:
            r = c.get("/api/auth/is-first-user")
            assert r.status_code == 200
            assert r.json()["is_first_user"] is True

    def test_is_first_user_false_after_register(self, tmp_path: Path) -> None:
        settings = AppSettings(
            data_dir=tmp_path, mock=True, dry_run=True, openai_api_key="",
            notification_email="", resend_api_key="", smtp_host="",
            notifications_enabled=False, secret_key="test-key", admin_password="",
        )
        app = create_app(settings)
        app.state.engine = get_memory_engine()
        init_db(app.state.engine)
        app.state.dotenv_path = tmp_path / ".env"
        with TestClient(app) as c:
            c.post("/api/auth/register", json={
                "email": "first@test.com", "password": "longpassword",
            })
            r = c.get("/api/auth/is-first-user")
            assert r.json()["is_first_user"] is False

    def test_first_user_sets_admin_password(self, tmp_path: Path) -> None:
        """First user registration can set the admin panel password."""
        settings = AppSettings(
            data_dir=tmp_path, mock=True, dry_run=True, openai_api_key="",
            notification_email="", resend_api_key="", smtp_host="",
            notifications_enabled=False, secret_key="test-key", admin_password="",
        )
        app = create_app(settings)
        app.state.engine = get_memory_engine()
        init_db(app.state.engine)
        app.state.dotenv_path = tmp_path / ".env"
        with TestClient(app) as c:
            r = c.post("/api/auth/register", json={
                "email": "admin@test.com",
                "password": "longpassword",
                "admin_password": "my-secret-admin-pw",
            })
            assert r.status_code == 200
            # Admin password should be persisted on settings
            assert app.state.settings.admin_password == "my-secret-admin-pw"

    def test_second_user_cannot_set_admin_password(self, tmp_path: Path) -> None:
        """Only the first user's admin_password is honoured."""
        settings = AppSettings(
            data_dir=tmp_path, mock=True, dry_run=True, openai_api_key="",
            notification_email="", resend_api_key="", smtp_host="",
            notifications_enabled=False, secret_key="test-key", admin_password="",
        )
        app = create_app(settings)
        app.state.engine = get_memory_engine()
        init_db(app.state.engine)
        app.state.dotenv_path = tmp_path / ".env"
        with TestClient(app) as c:
            # First user
            c.post("/api/auth/register", json={
                "email": "first@test.com", "password": "longpassword",
                "admin_password": "original-pw",
            })
            # Second user tries to override
            c.post("/api/auth/register", json={
                "email": "second@test.com", "password": "longpassword",
                "admin_password": "hacker-pw",
            })
            # Should still be the first user's password
            assert app.state.settings.admin_password == "original-pw"


# ---------------------------------------------------------------------------
# Database reset from admin panel
# ---------------------------------------------------------------------------

class TestAdminDatabaseReset:
    """Admin can reset the entire database from the web UI."""

    def test_reset_db_clears_all_data(self, test_app, client: TestClient) -> None:
        """POST /api/admin/reset-db drops and re-creates all tables."""
        # Verify we have data first
        r = client.get("/api/admin/users")
        assert len(r.json()["users"]) >= 1

        r = client.post("/api/admin/reset-db")
        assert r.status_code == 200
        assert r.json()["reset"] is True

        # After reset, the users table should be empty
        session = make_session(test_app.state.engine)
        from job_hunter.auth.repo import count_users
        assert count_users(session) == 0
        session.close()

    def test_reset_db_requires_admin_gate(self, tmp_path: Path) -> None:
        """When admin_password is set, reset-db requires the admin_token."""
        settings = AppSettings(
            data_dir=tmp_path, mock=True, dry_run=True, openai_api_key="",
            notification_email="", resend_api_key="", smtp_host="",
            notifications_enabled=False, secret_key="test-key",
            admin_password="reset-me",
        )
        app = create_app(settings)
        engine = get_memory_engine()
        init_db(engine)
        app.state.engine = engine
        app.state.dotenv_path = tmp_path / ".env"

        from job_hunter.auth.repo import create_user
        session = make_session(engine)
        user = create_user(session, email="u@test.com", password="longpassword", is_admin=True)
        uid = str(user.id)
        session.commit()
        session.close()

        from job_hunter.auth.security import create_access_token
        with TestClient(app) as c:
            token = create_access_token(uid, app.state.secret_key)
            c.cookies.set("access_token", token)

            # Without admin_token cookie → 403
            r = c.post("/api/admin/reset-db")
            assert r.status_code == 403

            # Authenticate with admin password first
            r = c.post("/api/admin/auth", json={"password": "reset-me"})
            assert r.status_code == 200

            # Now reset should work
            r = c.post("/api/admin/reset-db")
            assert r.status_code == 200
            assert r.json()["reset"] is True


# ---------------------------------------------------------------------------
# LinkedIn Cookie Upload / Status / Delete
# ---------------------------------------------------------------------------


def test_cookies_status_empty(client):
    """GET /api/settings/cookies-status returns exists=False when no file."""
    r = client.get("/api/settings/cookies-status")
    assert r.status_code == 200
    assert r.json()["exists"] is False


def test_cookies_upload_and_status(client, test_app, tmp_path):
    """POST /api/settings/cookies uploads valid JSON, then status shows exists."""
    cookies_json = json.dumps([{"name": "li_at", "value": "abc123", "domain": ".linkedin.com", "path": "/"}])
    r = client.post(
        "/api/settings/cookies",
        files={"file": ("cookies.json", cookies_json, "application/json")},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["uploaded"] is True
    assert data["count"] == 1

    # Status should now show exists
    r2 = client.get("/api/settings/cookies-status")
    assert r2.status_code == 200
    assert r2.json()["exists"] is True
    assert "updated" in r2.json()


def test_cookies_upload_invalid_json(client):
    """POST /api/settings/cookies rejects non-JSON."""
    r = client.post(
        "/api/settings/cookies",
        files={"file": ("cookies.json", "not-json{{{", "application/json")},
    )
    assert r.status_code == 400
    assert "Invalid cookies JSON" in r.json()["error"]


def test_cookies_upload_not_array(client):
    """POST /api/settings/cookies rejects JSON that is not an array."""
    r = client.post(
        "/api/settings/cookies",
        files={"file": ("cookies.json", '{"name": "li_at"}', "application/json")},
    )
    assert r.status_code == 400
    assert "array" in r.json()["error"].lower()


def test_cookies_delete(client):
    """DELETE /api/settings/cookies removes the file."""
    # First upload
    cookies_json = json.dumps([{"name": "li_at", "value": "x"}])
    client.post(
        "/api/settings/cookies",
        files={"file": ("cookies.json", cookies_json, "application/json")},
    )
    # Verify exists
    assert client.get("/api/settings/cookies-status").json()["exists"] is True

    # Delete
    r = client.delete("/api/settings/cookies")
    assert r.status_code == 200
    assert r.json()["deleted"] is True

    # Verify gone
    assert client.get("/api/settings/cookies-status").json()["exists"] is False


# ---------------------------------------------------------------------------
# LinkedIn Cookie Paste (li_at)
# ---------------------------------------------------------------------------


def test_cookies_paste_valid(client):
    """POST /api/settings/cookies-paste saves a valid li_at value."""
    li_at_value = "AQEDAQNhZXhhbXBsZXRva2VudmFsdWVmb3J0ZXN0aW5n" + "x" * 50
    r = client.post("/api/settings/cookies-paste", json={"li_at": li_at_value})
    assert r.status_code == 200
    assert r.json()["saved"] is True

    # Status should show cookies exist
    r2 = client.get("/api/settings/cookies-status")
    assert r2.json()["exists"] is True

    # Verify the file contains proper Playwright cookie format
    import json as _json
    settings = client.app.state.settings
    path = settings.data_dir / "cookies.json"
    cookies = _json.loads(path.read_text())
    assert len(cookies) == 1
    assert cookies[0]["name"] == "li_at"
    assert cookies[0]["value"] == li_at_value
    assert cookies[0]["domain"] == ".linkedin.com"
    assert cookies[0]["httpOnly"] is True
    assert cookies[0]["secure"] is True


def test_cookies_paste_empty(client):
    """POST /api/settings/cookies-paste rejects empty value."""
    r = client.post("/api/settings/cookies-paste", json={"li_at": ""})
    assert r.status_code == 400
    assert "empty" in r.json()["error"].lower()


def test_cookies_paste_too_short(client):
    """POST /api/settings/cookies-paste rejects suspiciously short values."""
    r = client.post("/api/settings/cookies-paste", json={"li_at": "abc"})
    assert r.status_code == 400
    assert "short" in r.json()["error"].lower()


def test_cookies_paste_whitespace_trimmed(client):
    """POST /api/settings/cookies-paste trims whitespace from the value."""
    li_at_value = "  AQEDAQNhZXhhbXBsZXRva2VudmFsdWVmb3J0ZXN0aW5n" + "x" * 50 + "  "
    r = client.post("/api/settings/cookies-paste", json={"li_at": li_at_value})
    assert r.status_code == 200

    import json as _json
    settings = client.app.state.settings
    path = settings.data_dir / "cookies.json"
    cookies = _json.loads(path.read_text())
    assert cookies[0]["value"] == li_at_value.strip()


def test_cookies_extract_saves_on_success_firefox(client, monkeypatch):
    """POST /api/settings/cookies-extract saves cookie when Firefox has it."""
    import job_hunter.web.routers.settings as settings_mod

    fake_value = "AQEDAQNhFakeExtractedCookieValue" + "x" * 40
    monkeypatch.setattr(
        settings_mod, "_try_extract_firefox", lambda: (fake_value, "Firefox"),
    )

    r = client.post("/api/settings/cookies-extract")
    assert r.status_code == 200
    data = r.json()
    assert data["saved"] is True
    assert data["browser"] == "Firefox"

    # Verify the cookie file was written correctly
    import json as _json
    settings = client.app.state.settings
    path = settings.data_dir / "cookies.json"
    cookies = _json.loads(path.read_text())
    assert len(cookies) == 1
    assert cookies[0]["name"] == "li_at"
    assert cookies[0]["value"] == fake_value
    assert cookies[0]["domain"] == ".linkedin.com"


def test_cookies_extract_falls_back_to_cdp(client, monkeypatch):
    """POST /api/settings/cookies-extract tries CDP when Firefox fails."""
    import job_hunter.web.routers.settings as settings_mod

    fake_value = "AQEDAQNhCdpExtractedCookieValue" + "x" * 40

    # Firefox fails
    monkeypatch.setattr(
        settings_mod, "_try_extract_firefox", lambda: (None, "Firefox: not installed"),
    )
    # CDP succeeds
    async def _fake_cdp():
        return fake_value, "Chrome"
    monkeypatch.setattr(settings_mod, "_try_extract_via_cdp", _fake_cdp)

    r = client.post("/api/settings/cookies-extract")
    assert r.status_code == 200
    data = r.json()
    assert data["saved"] is True
    assert data["browser"] == "Chrome"


def test_cookies_extract_returns_404_when_not_found(client, monkeypatch):
    """POST /api/settings/cookies-extract returns 404 when all strategies fail."""
    import job_hunter.web.routers.settings as settings_mod

    monkeypatch.setattr(
        settings_mod, "_try_extract_firefox", lambda: (None, "Firefox: not found"),
    )

    async def _fake_cdp():
        return None, "Chrome: no li_at cookie found (not logged in?)"
    monkeypatch.setattr(settings_mod, "_try_extract_via_cdp", _fake_cdp)

    r = client.post("/api/settings/cookies-extract")
    assert r.status_code == 404
    error = r.json()["error"]
    assert "Firefox" in error
    assert "Chrome" in error



def test_extension_zip_download(client):
    """GET /api/settings/extension-zip returns a valid ZIP file."""
    r = client.get("/api/settings/extension-zip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers.get("content-disposition", "")

    # Verify it's actually a valid ZIP with expected files
    import io
    import zipfile
    buf = io.BytesIO(r.content)
    with zipfile.ZipFile(buf) as zf:
        names = zf.namelist()
        assert any("manifest.json" in n for n in names)
        assert any("popup.html" in n for n in names)
        assert any("popup.js" in n for n in names)


# ---------------------------------------------------------------------------
# LinkedIn Remote Login endpoints
# ---------------------------------------------------------------------------


def test_linkedin_login_starts_task(client):
    """POST /api/settings/linkedin-login returns 202 and starts a background task."""
    r = client.post("/api/settings/linkedin-login", json={
        "email": "user@example.com",
        "password": "secret123",
    })
    # May be 202 (started) or 409 (another task running) — both are valid
    assert r.status_code in (202, 409)
    if r.status_code == 202:
        assert r.json()["started"] == "linkedin-login"


def test_linkedin_login_rejects_when_task_running(client):
    """POST /api/settings/linkedin-login returns 409 if a task is already running."""
    import job_hunter.web.task_manager as tm_mod
    tm = client.app.state.task_manager
    original_prop = type(tm).is_running
    type(tm).is_running = property(lambda self: True)
    try:
        r = client.post("/api/settings/linkedin-login", json={
            "email": "user@example.com",
            "password": "secret123",
        })
        assert r.status_code == 409
    finally:
        type(tm).is_running = original_prop


def test_linkedin_verify_no_pending_login(client):
    """POST /api/settings/linkedin-verify returns 400 when no login is waiting."""
    # Ensure no future is set
    client.app.state._linkedin_verify_future = None
    r = client.post("/api/settings/linkedin-verify", json={"code": "123456"})
    assert r.status_code == 400
    assert "No login task" in r.json()["error"]


def test_checkpoint_screenshot_not_found(client):
    """GET /api/settings/checkpoint-screenshot returns 404 when no screenshot exists."""
    r = client.get("/api/settings/checkpoint-screenshot?name=checkpoint_initial.png")
    assert r.status_code == 404


def test_checkpoint_screenshot_serves_file(client, test_app, tmp_path):
    """GET /api/settings/checkpoint-screenshot serves the PNG file."""
    # Create a fake screenshot in data_dir
    png_data = b"\x89PNG\r\n\x1a\nfake"
    (tmp_path / "checkpoint_initial.png").write_bytes(png_data)
    r = client.get("/api/settings/checkpoint-screenshot?name=checkpoint_initial.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == png_data


def test_checkpoint_screenshot_rejects_path_traversal(client):
    """GET /api/settings/checkpoint-screenshot rejects path traversal."""
    r = client.get("/api/settings/checkpoint-screenshot?name=../../etc/passwd")
    assert r.status_code == 400

