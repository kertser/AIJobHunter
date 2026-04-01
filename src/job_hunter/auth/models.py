"""User ORM model and per-user settings stored alongside the user row."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from job_hunter.db.models import Base


class User(Base):
    """Application user with credentials and per-user settings."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )
    last_login: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # ── Per-user settings (secrets stored as plaintext for now; ──
    # ── encrypt at rest via Fernet when SECRET_KEY is set)       ──
    # NULL means "inherit from global AppSettings". A non-null value
    # overrides the global default for this user.
    openai_api_key: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    llm_provider: Mapped[str | None] = mapped_column(String(50), nullable=True, default=None)
    local_llm_url: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    local_llm_model: Mapped[str | None] = mapped_column(String(200), nullable=True, default=None)
    llm_temperature: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    llm_max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    mock: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    dry_run: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    headless: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    slowmo_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    # Email / notification settings
    email_provider: Mapped[str | None] = mapped_column(String(50), nullable=True, default=None)
    resend_api_key: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    smtp_user: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    smtp_password: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    smtp_use_tls: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    notification_email: Mapped[str | None] = mapped_column(String(320), nullable=True, default=None)
    notifications_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)


