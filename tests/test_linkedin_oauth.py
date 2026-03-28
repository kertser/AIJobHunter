"""Tests for LinkedIn OAuth 2.0 BFF (Backend-for-Frontend) flow."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from job_hunter.config.models import AppSettings
from job_hunter.db.repo import get_memory_engine, init_db, make_session
from job_hunter.web.app import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_app(tmp_path: Path, *, linkedin_client_id: str = "", linkedin_client_secret: str = "", linkedin_redirect_uri: str = "") -> tuple:
    """Create a test app with optional LinkedIn OAuth config."""
    settings = AppSettings(
        data_dir=tmp_path,
        mock=True,
        dry_run=True,
        openai_api_key="",
        notification_email="",
        resend_api_key="",
        smtp_host="",
        notifications_enabled=False,
        secret_key="test-secret-key-for-jwt",
        admin_password="",
        linkedin_client_id=linkedin_client_id,
        linkedin_client_secret=linkedin_client_secret,
        linkedin_redirect_uri=linkedin_redirect_uri,
    )
    app = create_app(settings)
    engine = get_memory_engine()
    init_db(engine)
    app.state.engine = engine
    app.state.dotenv_path = tmp_path / ".env"

    from job_hunter.auth.repo import create_user
    session = make_session(engine)
    test_user = create_user(
        session, email="test@example.com", password="testpass123",
        display_name="Test User", is_admin=True,
    )
    user_id = str(test_user.id)
    session.commit()

    # Create user_profile.yml so pages don't redirect
    from job_hunter.auth.repo import get_user_data_dir
    user_data_dir = get_user_data_dir(tmp_path, test_user.id)
    (user_data_dir / "user_profile.yml").write_text("name: Test User\ntitle: Tester\n")
    (tmp_path / "user_profile.yml").write_text("name: Test User\ntitle: Tester\n")

    app.state._test_user_id = user_id
    return app, engine, user_id


@pytest.fixture()
def oauth_app(tmp_path):
    """App with LinkedIn OAuth configured."""
    app, engine, user_id = _make_app(
        tmp_path,
        linkedin_client_id="test-client-id",
        linkedin_client_secret="test-client-secret",
        linkedin_redirect_uri="http://localhost:8000/auth/linkedin/callback",
    )
    yield app


@pytest.fixture()
def oauth_client(oauth_app):
    with TestClient(oauth_app) as c:
        from job_hunter.auth.security import create_access_token
        token = create_access_token(oauth_app.state._test_user_id, oauth_app.state.secret_key)
        c.cookies.set("access_token", token)
        yield c


@pytest.fixture()
def no_oauth_app(tmp_path):
    """App without LinkedIn OAuth configured."""
    app, engine, user_id = _make_app(tmp_path)
    yield app


@pytest.fixture()
def no_oauth_client(no_oauth_app):
    with TestClient(no_oauth_app) as c:
        from job_hunter.auth.security import create_access_token
        token = create_access_token(no_oauth_app.state._test_user_id, no_oauth_app.state.secret_key)
        c.cookies.set("access_token", token)
        yield c


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------

class TestLinkedInStatus:
    def test_status_oauth_configured(self, oauth_client):
        r = oauth_client.get("/api/settings/linkedin-status")
        assert r.status_code == 200
        data = r.json()
        assert data["oauth_configured"] is True
        assert data["connected"] is False

    def test_status_oauth_not_configured(self, no_oauth_client):
        r = no_oauth_client.get("/api/settings/linkedin-status")
        assert r.status_code == 200
        data = r.json()
        assert data["oauth_configured"] is False
        assert data["connected"] is False

    def test_status_unauthenticated(self, oauth_app):
        """Unauthenticated callers should get oauth_configured but connected=False."""
        with TestClient(oauth_app) as c:
            r = c.get("/api/settings/linkedin-status")
            assert r.status_code == 200
            data = r.json()
            assert data["oauth_configured"] is True
            assert data["connected"] is False
            assert data["member_id"] == ""

    def test_status_connected_user(self, oauth_app):
        """Status shows connected when user has a LinkedIn token."""
        from job_hunter.auth.repo import get_user_by_id, update_linkedin_token
        import uuid

        session = make_session(oauth_app.state.engine)
        user = get_user_by_id(session, oauth_app.state._test_user_id)
        update_linkedin_token(
            session, user.id,
            access_token="fake-token",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            member_id="li-member-123",
        )
        session.commit()
        session.close()

        with TestClient(oauth_app) as c:
            from job_hunter.auth.security import create_access_token
            token = create_access_token(oauth_app.state._test_user_id, oauth_app.state.secret_key)
            c.cookies.set("access_token", token)
            r = c.get("/api/settings/linkedin-status")
            assert r.status_code == 200
            data = r.json()
            assert data["connected"] is True
            assert data["member_id"] == "li-member-123"

    def test_status_expired_token(self, oauth_app):
        """Status shows expired when token is past expiry."""
        from job_hunter.auth.repo import get_user_by_id, update_linkedin_token

        session = make_session(oauth_app.state.engine)
        user = get_user_by_id(session, oauth_app.state._test_user_id)
        update_linkedin_token(
            session, user.id,
            access_token="expired-token",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            member_id="li-member-expired",
        )
        session.commit()
        session.close()

        with TestClient(oauth_app) as c:
            from job_hunter.auth.security import create_access_token
            token = create_access_token(oauth_app.state._test_user_id, oauth_app.state.secret_key)
            c.cookies.set("access_token", token)
            r = c.get("/api/settings/linkedin-status")
            data = r.json()
            assert data["connected"] is False
            assert data["expired"] is True


# ---------------------------------------------------------------------------
# Authorize endpoint (redirect to LinkedIn)
# ---------------------------------------------------------------------------

class TestLinkedInAuthorize:
    def test_authorize_redirects_to_linkedin(self, oauth_client):
        r = oauth_client.get("/auth/linkedin", follow_redirects=False)
        assert r.status_code == 302
        location = r.headers["location"]
        assert "linkedin.com/oauth/v2/authorization" in location
        assert "client_id=test-client-id" in location
        assert "response_type=code" in location
        assert "scope=" in location
        # State cookie should be set
        assert "linkedin_oauth_state" in r.cookies

    def test_authorize_not_configured(self, no_oauth_client):
        r = no_oauth_client.get("/auth/linkedin")
        assert r.status_code == 400
        assert "not configured" in r.json()["error"]

    def test_authorize_unauthenticated(self, oauth_app):
        """OAuth initiation should work without existing session."""
        with TestClient(oauth_app) as c:
            r = c.get("/auth/linkedin", follow_redirects=False)
            assert r.status_code == 302
            assert "linkedin.com" in r.headers["location"]


# ---------------------------------------------------------------------------
# Callback endpoint (token exchange + login/register)
# ---------------------------------------------------------------------------

class TestLinkedInCallback:
    def _mock_token_response(self, access_token="test-access-token", expires_in=3600):
        """Create a mock httpx response for the token endpoint."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "access_token": access_token,
            "expires_in": expires_in,
            "token_type": "Bearer",
        }
        resp.text = json.dumps(resp.json.return_value)
        return resp

    def _mock_userinfo_response(self, sub="li-sub-123", email="linkedinuser@example.com", name="LinkedIn User"):
        """Create a mock httpx response for the userinfo endpoint."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "sub": sub,
            "email": email,
            "name": name,
            "email_verified": True,
        }
        resp.text = json.dumps(resp.json.return_value)
        return resp

    def _make_mock_client(self, token_resp, userinfo_resp=None):
        """Build a mock httpx.AsyncClient class that works with `async with`."""
        def _factory(*args, **kwargs):
            client = MagicMock()
            client.post = AsyncMock(return_value=token_resp)
            if userinfo_resp is not None:
                client.get = AsyncMock(return_value=userinfo_resp)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            return client
        return _factory

    def test_callback_missing_code(self, oauth_app):
        with TestClient(oauth_app) as c:
            r = c.get("/auth/linkedin/callback?state=abc", follow_redirects=False)
            assert r.status_code == 302
            assert "Missing" in r.headers["location"] or "/login" in r.headers["location"]

    def test_callback_error_from_linkedin(self, oauth_app):
        with TestClient(oauth_app) as c:
            r = c.get(
                "/auth/linkedin/callback?error=access_denied&error_description=User+denied",
                follow_redirects=False,
            )
            assert r.status_code == 302
            assert "/login" in r.headers["location"]

    def test_callback_state_mismatch(self, oauth_app):
        with TestClient(oauth_app) as c:
            c.cookies.set("linkedin_oauth_state", "correct-state")
            r = c.get(
                "/auth/linkedin/callback?code=test-code&state=wrong-state",
                follow_redirects=False,
            )
            assert r.status_code == 302
            assert "state" in r.headers["location"].lower() or "/login" in r.headers["location"]

    def test_callback_new_user_registration(self, oauth_app):
        """A new LinkedIn user should be auto-registered and logged in."""
        token_resp = self._mock_token_response()
        userinfo_resp = self._mock_userinfo_response(
            sub="new-li-user-456",
            email="newuser@linkedin.com",
            name="New LinkedIn User",
        )
        mock_factory = self._make_mock_client(token_resp, userinfo_resp)

        with TestClient(oauth_app) as c:
            state_value = "test-state-value-12345"
            c.cookies.set("linkedin_oauth_state", state_value)

            with patch("job_hunter.web.routers.linkedin_oauth.httpx.AsyncClient", side_effect=mock_factory):
                r = c.get(
                    f"/auth/linkedin/callback?code=test-auth-code&state={state_value}",
                    follow_redirects=False,
                )

            assert r.status_code == 302
            assert r.headers["location"] == "/"
            # JWT cookie should be set
            assert "access_token" in r.cookies

            # Verify user was created in DB
            session = make_session(oauth_app.state.engine)
            from job_hunter.auth.repo import get_user_by_email
            user = get_user_by_email(session, "newuser@linkedin.com")
            assert user is not None
            assert user.linkedin_member_id == "new-li-user-456"
            assert user.linkedin_access_token == "test-access-token"
            assert user.display_name == "New LinkedIn User"
            session.close()

    def test_callback_existing_user_by_email(self, oauth_app):
        """An existing user matched by email should be linked and logged in."""
        token_resp = self._mock_token_response()
        userinfo_resp = self._mock_userinfo_response(
            sub="existing-li-99",
            email="test@example.com",  # matches the test user
            name="Test User LinkedIn",
        )
        mock_factory = self._make_mock_client(token_resp, userinfo_resp)

        with TestClient(oauth_app) as c:
            state_value = "email-match-state"
            c.cookies.set("linkedin_oauth_state", state_value)

            with patch("job_hunter.web.routers.linkedin_oauth.httpx.AsyncClient", side_effect=mock_factory):
                r = c.get(
                    f"/auth/linkedin/callback?code=auth-code&state={state_value}",
                    follow_redirects=False,
                )

            assert r.status_code == 302
            assert r.headers["location"] == "/"

            # Verify the existing user was linked
            session = make_session(oauth_app.state.engine)
            from job_hunter.auth.repo import get_user_by_email
            user = get_user_by_email(session, "test@example.com")
            assert user is not None
            assert user.linkedin_member_id == "existing-li-99"
            assert user.linkedin_access_token == "test-access-token"
            session.close()

    def test_callback_returning_user_by_linkedin_id(self, oauth_app):
        """A returning user matched by LinkedIn member ID should be logged in."""
        # First, link the test user to a LinkedIn ID
        session = make_session(oauth_app.state.engine)
        from job_hunter.auth.repo import get_user_by_id, update_linkedin_token
        user = get_user_by_id(session, oauth_app.state._test_user_id)
        update_linkedin_token(
            session, user.id,
            access_token="old-token",
            member_id="returning-li-888",
        )
        session.commit()
        session.close()

        token_resp = self._mock_token_response(access_token="new-refreshed-token")
        userinfo_resp = self._mock_userinfo_response(
            sub="returning-li-888",
            email="test@example.com",
            name="Test User",
        )
        mock_factory = self._make_mock_client(token_resp, userinfo_resp)

        with TestClient(oauth_app) as c:
            state_value = "returning-state"
            c.cookies.set("linkedin_oauth_state", state_value)

            with patch("job_hunter.web.routers.linkedin_oauth.httpx.AsyncClient", side_effect=mock_factory):
                r = c.get(
                    f"/auth/linkedin/callback?code=auth-code&state={state_value}",
                    follow_redirects=False,
                )

            assert r.status_code == 302
            assert r.headers["location"] == "/"

            # Token should be updated
            session = make_session(oauth_app.state.engine)
            from job_hunter.auth.repo import get_user_by_linkedin_id
            user = get_user_by_linkedin_id(session, "returning-li-888")
            assert user is not None
            assert user.linkedin_access_token == "new-refreshed-token"
            session.close()

    def test_callback_token_exchange_failure(self, oauth_app):
        """Failed token exchange should redirect to login with error."""
        failed_resp = MagicMock()
        failed_resp.status_code = 400
        failed_resp.text = '{"error": "invalid_grant"}'
        mock_factory = self._make_mock_client(failed_resp)

        with TestClient(oauth_app) as c:
            state_value = "fail-state"
            c.cookies.set("linkedin_oauth_state", state_value)

            with patch("job_hunter.web.routers.linkedin_oauth.httpx.AsyncClient", side_effect=mock_factory):
                r = c.get(
                    f"/auth/linkedin/callback?code=bad-code&state={state_value}",
                    follow_redirects=False,
                )

            assert r.status_code == 302
            assert "/login" in r.headers["location"]
            assert "failed" in r.headers["location"].lower() or "error" in r.headers["location"].lower()


# ---------------------------------------------------------------------------
# Disconnect endpoint
# ---------------------------------------------------------------------------

class TestLinkedInDisconnect:
    def test_disconnect(self, oauth_app):
        """Disconnect should clear LinkedIn tokens."""
        # First, set a token
        session = make_session(oauth_app.state.engine)
        from job_hunter.auth.repo import get_user_by_id, update_linkedin_token
        user = get_user_by_id(session, oauth_app.state._test_user_id)
        update_linkedin_token(
            session, user.id,
            access_token="to-be-cleared",
            member_id="li-to-clear",
        )
        session.commit()
        session.close()

        with TestClient(oauth_app) as c:
            from job_hunter.auth.security import create_access_token
            token = create_access_token(oauth_app.state._test_user_id, oauth_app.state.secret_key)
            c.cookies.set("access_token", token)

            r = c.post("/api/settings/linkedin-disconnect")
            assert r.status_code == 200
            assert r.json()["disconnected"] is True

        # Verify tokens are cleared
        session = make_session(oauth_app.state.engine)
        user = get_user_by_id(session, oauth_app.state._test_user_id)
        assert user.linkedin_access_token is None
        assert user.linkedin_member_id is None
        session.close()

    def test_disconnect_unauthenticated(self, oauth_app):
        """Disconnect without auth should fail."""
        with TestClient(oauth_app) as c:
            r = c.post("/api/settings/linkedin-disconnect")
            assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Auth repo helpers
# ---------------------------------------------------------------------------

class TestAuthRepoLinkedIn:
    def test_get_user_by_linkedin_id(self, oauth_app):
        session = make_session(oauth_app.state.engine)
        from job_hunter.auth.repo import get_user_by_id, get_user_by_linkedin_id, update_linkedin_token

        user = get_user_by_id(session, oauth_app.state._test_user_id)
        update_linkedin_token(session, user.id, access_token="t", member_id="li-find-me")
        session.commit()

        found = get_user_by_linkedin_id(session, "li-find-me")
        assert found is not None
        assert str(found.id) == oauth_app.state._test_user_id

        not_found = get_user_by_linkedin_id(session, "nonexistent")
        assert not_found is None
        session.close()

    def test_clear_linkedin_token(self, oauth_app):
        session = make_session(oauth_app.state.engine)
        from job_hunter.auth.repo import get_user_by_id, update_linkedin_token, clear_linkedin_token

        user = get_user_by_id(session, oauth_app.state._test_user_id)
        update_linkedin_token(session, user.id, access_token="t", member_id="li-clear", expires_at=datetime.now(timezone.utc))
        session.flush()

        assert user.linkedin_access_token == "t"
        assert user.linkedin_member_id == "li-clear"
        assert user.linkedin_token_expires_at is not None

        clear_linkedin_token(session, user.id)
        assert user.linkedin_access_token is None
        assert user.linkedin_member_id is None
        assert user.linkedin_token_expires_at is None
        session.close()

    def test_update_linkedin_token_nonexistent_user(self, oauth_app):
        import uuid
        session = make_session(oauth_app.state.engine)
        from job_hunter.auth.repo import update_linkedin_token
        result = update_linkedin_token(session, uuid.uuid4(), access_token="t", member_id="x")
        assert result is None
        session.close()

    def test_clear_linkedin_token_nonexistent_user(self, oauth_app):
        import uuid
        session = make_session(oauth_app.state.engine)
        from job_hunter.auth.repo import clear_linkedin_token
        result = clear_linkedin_token(session, uuid.uuid4())
        assert result is None
        session.close()







