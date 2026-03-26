"""Email notifications — SMTP and Resend-based pipeline summary emails."""

from __future__ import annotations

import logging
import smtplib
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

logger = logging.getLogger("job_hunter.notifications.email")


# ---------------------------------------------------------------------------
# Provider pattern: Base → Smtp → Fake
# ---------------------------------------------------------------------------


class BaseNotifier(ABC):
    """Abstract notifier interface."""

    @abstractmethod
    def send(self, subject: str, body_text: str, body_html: str) -> bool:
        """Send an email. Returns True on success."""
        ...


class SmtpNotifier(BaseNotifier):
    """Real SMTP notifier."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        recipient: str,
        use_tls: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.recipient = recipient
        self.use_tls = use_tls
        self.last_error: str = ""

    def send(self, subject: str, body_text: str, body_html: str) -> bool:
        self.last_error = ""
        sender = self.user or self.recipient  # fall back to recipient as From
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = self.recipient
        msg.attach(MIMEText(body_text, "plain"))
        msg.attach(MIMEText(body_html, "html"))

        try:
            if self.port == 465:
                server = smtplib.SMTP_SSL(self.host, self.port, timeout=15)
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=15)
                if self.use_tls:
                    server.starttls()
            # Only authenticate when credentials are provided
            if self.user and self.password:
                server.login(self.user, self.password)
            server.sendmail(sender, [self.recipient], msg.as_string())
            server.quit()
            logger.info("Notification email sent to %s", self.recipient)
            return True
        except smtplib.SMTPAuthenticationError as exc:
            self.last_error = (
                f"Authentication failed for {self.user}. "
                "Check username/password. Gmail requires an App Password."
            )
            logger.error("SMTP auth error: %s", exc)
            return False
        except smtplib.SMTPConnectError as exc:
            self.last_error = (
                f"Could not connect to {self.host}:{self.port}. "
                "Check host and port."
            )
            logger.error("SMTP connect error: %s", exc)
            return False
        except (TimeoutError, OSError) as exc:
            self.last_error = (
                f"Connection to {self.host}:{self.port} timed out. "
                "Verify the host/port are correct and not blocked by a firewall. "
                "Common ports: 587 (STARTTLS), 465 (SSL)."
            )
            logger.error("SMTP timeout: %s", exc)
            return False
        except Exception as exc:
            self.last_error = str(exc)
            logger.error("Failed to send email: %s", exc)
            return False


class FakeNotifier(BaseNotifier):
    """Test notifier — records emails instead of sending."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send(self, subject: str, body_text: str, body_html: str) -> bool:
        self.sent.append({
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
        })
        return True


class ResendNotifier(BaseNotifier):
    """Resend API notifier — no SMTP config needed, just an API key.

    Sign up at https://resend.com (free tier: 100 emails/day).
    """

    # Resend provides a shared sender for testing; users can verify their
    # own domain later for a custom From address.
    DEFAULT_FROM = "AI Job Hunter <onboarding@resend.dev>"

    def __init__(
        self,
        api_key: str,
        recipient: str,
        from_address: str = "",
    ) -> None:
        self.api_key = api_key
        self.recipient = recipient
        self.from_address = from_address or self.DEFAULT_FROM
        self.last_error: str = ""

    def send(self, subject: str, body_text: str, body_html: str) -> bool:
        self.last_error = ""
        try:
            import resend

            resend.api_key = self.api_key
            params: dict[str, Any] = {
                "from": self.from_address,
                "to": [self.recipient],
                "subject": subject,
                "html": body_html,
                "text": body_text,
            }
            result = resend.Emails.send(params)
            email_id = result.get("id", "?") if isinstance(result, dict) else getattr(result, "id", "?")
            logger.info("Resend email sent to %s (id=%s)", self.recipient, email_id)
            return True
        except ImportError:
            self.last_error = "The 'resend' package is not installed. Run: uv pip install resend"
            logger.error(self.last_error)
            return False
        except Exception as exc:
            self.last_error = str(exc)
            logger.error("Resend send failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Build notification from pipeline results
# ---------------------------------------------------------------------------


def build_notifier_from_settings(settings: Any) -> BaseNotifier | None:
    """Create a notifier from AppSettings based on the chosen email provider.

    Returns ``ResendNotifier`` when *email_provider* is ``"resend"`` and a key
    is configured, otherwise falls back to ``SmtpNotifier`` if SMTP is set up.
    Returns ``None`` when notifications are disabled or nothing is configured.
    """
    if not settings.notifications_enabled:
        return None
    if not settings.notification_email:
        return None

    provider = getattr(settings, "email_provider", "smtp")

    # Resend — just needs an API key
    if provider == "resend":
        api_key = getattr(settings, "resend_api_key", "")
        if api_key:
            return ResendNotifier(
                api_key=api_key,
                recipient=settings.notification_email,
            )

    # SMTP fallback
    if settings.smtp_host:
        return SmtpNotifier(
            host=settings.smtp_host,
            port=settings.smtp_port,
            user=settings.smtp_user,
            password=settings.smtp_password,
            recipient=settings.notification_email,
            use_tls=settings.smtp_use_tls,
        )

    return None


def send_pipeline_summary(
    notifier: BaseNotifier,
    summary: dict[str, Any],
    pipeline_mode: str = "pipeline",
) -> bool:
    """Format a pipeline summary dict into an email and send it."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subject = f"🤖 AI Job Hunter — {pipeline_mode} run completed ({now})"

    body_text = _build_text_body(summary, pipeline_mode, now)
    body_html = _build_html_body(summary, pipeline_mode, now)

    return notifier.send(subject, body_text, body_html)


def send_test_email(notifier: BaseNotifier) -> bool:
    """Send a test email to verify SMTP configuration."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return notifier.send(
        subject="✅ AI Job Hunter — Email test successful",
        body_text=f"Email notifications are working correctly.\nSent at: {now}",
        body_html=f"""
        <div style="font-family: sans-serif; max-width: 500px; margin: 0 auto; padding: 20px;">
          <h2 style="color: #22c55e;">✅ Email Test Successful</h2>
          <p>Email notifications are configured and working correctly.</p>
          <p style="color: #6b7280; font-size: 0.85rem;">Sent at: {now}</p>
        </div>
        """,
    )


# ---------------------------------------------------------------------------
# Email body builders
# ---------------------------------------------------------------------------


def _build_text_body(summary: dict[str, Any], mode: str, timestamp: str) -> str:
    lines = [
        f"AI Job Hunter — {mode} run completed",
        f"Time: {timestamp}",
        "=" * 40,
    ]

    if "discovered" in summary:
        lines.append(f"Jobs discovered: {summary['discovered']}")
    if "scored" in summary:
        lines.append(f"Jobs scored: {summary['scored']}")
    if "applied" in summary:
        lines.append(f"Jobs applied: {summary['applied']}")
    if "events_created" in summary:
        lines.append(f"Market events: {summary['events_created']}")
    if "error" in summary:
        lines.append(f"\n⚠ Error: {summary['error']}")

    return "\n".join(lines)


def _build_html_body(summary: dict[str, Any], mode: str, timestamp: str) -> str:
    stats_rows = ""
    stat_items = [
        ("discovered", "🔍 Discovered", "#3b82f6"),
        ("scored", "🎯 Scored", "#8b5cf6"),
        ("applied", "⚡ Applied", "#22c55e"),
        ("events_created", "📥 Market Events", "#6366f1"),
        ("extractions", "🔍 Extractions", "#6366f1"),
    ]
    for key, label, color in stat_items:
        if key in summary:
            stats_rows += f"""
            <tr>
              <td style="padding: 8px 12px; font-weight: 600;">{label}</td>
              <td style="padding: 8px 12px; color: {color}; font-size: 1.2rem; font-weight: 700;">{summary[key]}</td>
            </tr>"""

    error_block = ""
    if "error" in summary:
        error_block = f"""
        <div style="background: #fef2f2; border-left: 4px solid #ef4444; padding: 12px; margin-top: 16px; border-radius: 4px;">
          <strong style="color: #dc2626;">⚠ Error:</strong> {summary['error']}
        </div>"""

    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
      <div style="background: linear-gradient(135deg, #05172b, #2064c6); color: white; padding: 20px; border-radius: 8px 8px 0 0;">
        <h1 style="margin: 0; font-size: 1.3rem;">🤖 AI Job Hunter</h1>
        <p style="margin: 4px 0 0; opacity: 0.8; font-size: 0.85rem;">{mode} run completed · {timestamp}</p>
      </div>
      <div style="border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 8px 8px; padding: 20px;">
        <table style="width: 100%; border-collapse: collapse;">
          {stats_rows}
        </table>
        {error_block}
        <p style="margin-top: 20px; color: #6b7280; font-size: 0.78rem;">
          Sent automatically by AI Job Hunter.
        </p>
      </div>
    </div>
    """

