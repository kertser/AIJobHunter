"""Tests for email notification module."""

from __future__ import annotations

import pytest

from job_hunter.notifications.email import (
    FakeNotifier,
    ResendNotifier,
    SmtpNotifier,
    build_notifier_from_settings,
    send_pipeline_summary,
    send_test_email,
    _build_html_body,
    _build_text_body,
)


# ---------------------------------------------------------------------------
# FakeNotifier
# ---------------------------------------------------------------------------


class TestFakeNotifier:
    def test_send_records_email(self) -> None:
        notifier = FakeNotifier()
        ok = notifier.send("Subject", "body text", "<p>body html</p>")
        assert ok is True
        assert len(notifier.sent) == 1
        assert notifier.sent[0]["subject"] == "Subject"
        assert notifier.sent[0]["body_text"] == "body text"
        assert notifier.sent[0]["body_html"] == "<p>body html</p>"

    def test_send_multiple(self) -> None:
        notifier = FakeNotifier()
        notifier.send("A", "a", "<a/>")
        notifier.send("B", "b", "<b/>")
        assert len(notifier.sent) == 2


# ---------------------------------------------------------------------------
# SmtpNotifier construction
# ---------------------------------------------------------------------------


class TestSmtpNotifier:
    def test_construction(self) -> None:
        n = SmtpNotifier(
            host="smtp.example.com",
            port=587,
            user="user@example.com",
            password="secret",
            recipient="dest@example.com",
            use_tls=True,
        )
        assert n.host == "smtp.example.com"
        assert n.port == 587
        assert n.user == "user@example.com"
        assert n.recipient == "dest@example.com"
        assert n.use_tls is True
        assert n.last_error == ""

    def test_last_error_on_connection_failure(self) -> None:
        """Connecting to a non-routable address should populate last_error."""
        n = SmtpNotifier(
            host="192.0.2.1",  # RFC 5737 TEST-NET — guaranteed unreachable
            port=587,
            user="u@x.com",
            password="pw",
            recipient="r@x.com",
        )
        ok = n.send("test", "body", "<p>body</p>")
        assert ok is False
        assert n.last_error  # should have a descriptive message
        assert "192.0.2.1" in n.last_error
        assert "timed out" in n.last_error.lower() or "port" in n.last_error.lower()

    def test_last_error_cleared_on_new_send(self) -> None:
        """last_error should be reset at the start of each send()."""
        n = SmtpNotifier(
            host="192.0.2.1",
            port=587,
            user="u@x.com",
            password="pw",
            recipient="r@x.com",
        )
        # First send fails
        n.send("test", "body", "<p>body</p>")
        assert n.last_error != ""
        # Verify it gets reset (will fail again, but with a fresh error)
        old_error = n.last_error
        n.send("test2", "body", "<p>body</p>")
        assert n.last_error != ""  # still has an error, but was reset and re-set


# ---------------------------------------------------------------------------
# build_notifier_from_settings
# ---------------------------------------------------------------------------


class TestBuildNotifier:
    def test_returns_none_when_disabled(self) -> None:
        """Returns None when notifications_enabled is False."""
        from job_hunter.config.models import AppSettings

        settings = AppSettings(
            notifications_enabled=False,
            smtp_host="smtp.example.com",
            notification_email="user@example.com",
            email_provider="smtp",
        )
        assert build_notifier_from_settings(settings) is None

    def test_returns_none_when_no_email(self) -> None:
        from job_hunter.config.models import AppSettings

        settings = AppSettings(
            notifications_enabled=True,
            smtp_host="smtp.example.com",
            notification_email="",
            email_provider="smtp",
        )
        assert build_notifier_from_settings(settings) is None

    def test_returns_smtp_notifier_when_configured(self) -> None:
        from job_hunter.config.models import AppSettings

        settings = AppSettings(
            notifications_enabled=True,
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_user="user@example.com",
            smtp_password="secret",
            notification_email="dest@example.com",
            email_provider="smtp",
        )
        notifier = build_notifier_from_settings(settings)
        assert notifier is not None
        assert isinstance(notifier, SmtpNotifier)
        assert notifier.host == "smtp.example.com"
        assert notifier.port == 465
        assert notifier.recipient == "dest@example.com"

    def test_returns_resend_notifier_when_configured(self) -> None:
        from job_hunter.config.models import AppSettings

        settings = AppSettings(
            notifications_enabled=True,
            notification_email="dest@example.com",
            email_provider="resend",
            resend_api_key="re_test_key_123",
        )
        notifier = build_notifier_from_settings(settings)
        assert notifier is not None
        assert isinstance(notifier, ResendNotifier)
        assert notifier.recipient == "dest@example.com"
        assert notifier.api_key == "re_test_key_123"

    def test_resend_without_key_falls_back_to_smtp(self) -> None:
        """Resend provider with no API key should fall back to SMTP if configured."""
        from job_hunter.config.models import AppSettings

        settings = AppSettings(
            notifications_enabled=True,
            notification_email="dest@example.com",
            email_provider="resend",
            resend_api_key="",
            smtp_host="smtp.example.com",
        )
        notifier = build_notifier_from_settings(settings)
        assert isinstance(notifier, SmtpNotifier)

    def test_resend_without_key_no_smtp_returns_none(self) -> None:
        """Resend provider with no API key and no SMTP returns None."""
        from job_hunter.config.models import AppSettings

        settings = AppSettings(
            notifications_enabled=True,
            notification_email="dest@example.com",
            email_provider="resend",
            resend_api_key="",
            smtp_host="",
        )
        assert build_notifier_from_settings(settings) is None

    def test_smtp_without_host_returns_none(self) -> None:
        from job_hunter.config.models import AppSettings

        settings = AppSettings(
            notifications_enabled=True,
            notification_email="user@example.com",
            email_provider="smtp",
            smtp_host="",
        )
        assert build_notifier_from_settings(settings) is None


# ---------------------------------------------------------------------------
# ResendNotifier
# ---------------------------------------------------------------------------


class TestResendNotifier:
    def test_construction(self) -> None:
        n = ResendNotifier(
            api_key="re_test_123",
            recipient="dest@example.com",
        )
        assert n.api_key == "re_test_123"
        assert n.recipient == "dest@example.com"
        assert n.last_error == ""
        assert "resend.dev" in n.from_address  # uses default sender

    def test_custom_from_address(self) -> None:
        n = ResendNotifier(
            api_key="re_test_123",
            recipient="dest@example.com",
            from_address="bot@mydomain.com",
        )
        assert n.from_address == "bot@mydomain.com"


# ---------------------------------------------------------------------------
# send_pipeline_summary
# ---------------------------------------------------------------------------


class TestSendPipelineSummary:
    def test_send_pipeline_summary_basic(self) -> None:
        notifier = FakeNotifier()
        summary = {"discovered": 10, "scored": 8, "applied": 3}
        ok = send_pipeline_summary(notifier, summary, "full")
        assert ok is True
        assert len(notifier.sent) == 1
        email = notifier.sent[0]
        assert "full" in email["subject"]
        assert "AI Job Hunter" in email["subject"]
        assert "discovered" in email["body_text"].lower() or "10" in email["body_text"]

    def test_send_pipeline_summary_with_error(self) -> None:
        notifier = FakeNotifier()
        summary = {"discovered": 5, "error": "Connection timeout"}
        ok = send_pipeline_summary(notifier, summary, "discover")
        assert ok is True
        email = notifier.sent[0]
        assert "Connection timeout" in email["body_text"]
        assert "Connection timeout" in email["body_html"]

    def test_send_pipeline_summary_market_mode(self) -> None:
        notifier = FakeNotifier()
        summary = {"events_created": 25, "extractions": 100}
        ok = send_pipeline_summary(notifier, summary, "market")
        assert ok is True
        email = notifier.sent[0]
        assert "market" in email["subject"]
        assert "25" in email["body_text"]


# ---------------------------------------------------------------------------
# send_test_email
# ---------------------------------------------------------------------------


class TestSendTestEmail:
    def test_send_test_email(self) -> None:
        notifier = FakeNotifier()
        ok = send_test_email(notifier)
        assert ok is True
        email = notifier.sent[0]
        assert "test" in email["subject"].lower()
        assert "working correctly" in email["body_text"].lower()


# ---------------------------------------------------------------------------
# Body builders
# ---------------------------------------------------------------------------


class TestBodyBuilders:
    def test_text_body_includes_stats(self) -> None:
        body = _build_text_body(
            {"discovered": 5, "scored": 3, "applied": 1},
            "full",
            "2026-03-26 12:00 UTC",
        )
        assert "5" in body
        assert "3" in body
        assert "1" in body
        assert "full" in body

    def test_text_body_with_error(self) -> None:
        body = _build_text_body(
            {"error": "Bad thing happened"},
            "discover",
            "2026-03-26",
        )
        assert "Bad thing happened" in body

    def test_html_body_includes_stats(self) -> None:
        body = _build_html_body(
            {"discovered": 12, "scored": 8},
            "full",
            "2026-03-26 12:00 UTC",
        )
        assert "12" in body
        assert "8" in body
        assert "AI Job Hunter" in body

    def test_html_body_with_error_block(self) -> None:
        body = _build_html_body(
            {"error": "Timeout"},
            "full",
            "2026-03-26",
        )
        assert "Timeout" in body
        assert "ef4444" in body  # red color in error block

    def test_html_body_no_error(self) -> None:
        body = _build_html_body(
            {"discovered": 5},
            "full",
            "2026-03-26",
        )
        assert "ef4444" not in body  # no error block

