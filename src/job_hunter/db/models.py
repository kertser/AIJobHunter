"""SQLAlchemy ORM models for Job, Score, and ApplicationAttempt."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, Integer, String, Text, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class JobStatus(str, enum.Enum):
    NEW = "new"
    SCORED = "scored"
    QUEUED = "queued"
    APPLIED = "applied"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    REVIEW = "review"
    FAILED = "failed"


class Decision(str, enum.Enum):
    APPLY = "apply"
    SKIP = "skip"
    REVIEW = "review"


class ApplicationResult(str, enum.Enum):
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"
    DRY_RUN = "dry_run"
    ALREADY_APPLIED = "already_applied"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(50), default="linkedin")
    external_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    company: Mapped[str] = mapped_column(String(500), nullable=False)
    location: Mapped[str] = mapped_column(String(500), default="")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    description_text: Mapped[str] = mapped_column(Text, default="")
    easy_apply: Mapped[bool] = mapped_column(Boolean, default=False)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False), default=JobStatus.NEW
    )
    notes: Mapped[str] = mapped_column(Text, default="")


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resume_id: Mapped[str] = mapped_column(String(255), default="default")
    embedding_similarity: Mapped[float] = mapped_column(Float, default=0.0)
    llm_fit_score: Mapped[int] = mapped_column(Integer, default=0)
    missing_skills: Mapped[list] = mapped_column(JSON, default=list)
    risk_flags: Mapped[list] = mapped_column(JSON, default=list)
    decision: Mapped[Decision] = mapped_column(
        Enum(Decision, native_enum=False), default=Decision.REVIEW
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ApplicationAttempt(Base):
    __tablename__ = "application_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[ApplicationResult] = mapped_column(
        Enum(ApplicationResult, native_enum=False), nullable=True
    )
    failure_stage: Mapped[str | None] = mapped_column(String(255), nullable=True)
    screenshot_paths: Mapped[list] = mapped_column(JSON, default=list)
    form_answers_json: Mapped[dict] = mapped_column(JSON, default=dict)

