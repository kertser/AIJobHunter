"""SQLAlchemy ORM models for the Market Intelligence subsystem.

All tables live alongside the existing application tables (jobs, scores,
application_attempts) and share the same ``Base`` declarative class.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from job_hunter.db.models import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class MarketEventType(str, enum.Enum):
    JOB_POSTING = "job_posting"


class ExtractionStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETE = "complete"
    FAILED = "failed"


class EntityType(str, enum.Enum):
    SKILL = "skill"
    TASK = "task"
    PROBLEM = "problem"
    TOOL = "tool"
    ROLE = "role"
    COMPANY = "company"


class SubjectType(str, enum.Enum):
    CANDIDATE = "candidate"
    ROLE = "role"
    COMPANY = "company"
    JOB = "job"


class EvidenceType(str, enum.Enum):
    EXPLICIT_MENTION = "explicit_mention"
    INFERRED = "inferred"
    CO_OCCURRENCE = "co_occurrence"
    DIALOGUE = "dialogue"


class Polarity(str, enum.Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class EdgeType(str, enum.Enum):
    CO_OCCURS_WITH = "co_occurs_with"
    SUPPORTS = "supports"
    REQUIRES = "requires"
    USED_WITH = "used_with"
    TRANSITION_TO = "transition_to"


class SessionType(str, enum.Enum):
    CANDIDATE_INTERVIEW = "candidate_interview"
    MANAGER_INTERVIEW = "manager_interview"
    ROLE_CLARIFICATION = "role_clarification"
    DIAGNOSTIC_QNA = "diagnostic_qna"


class AssessmentType(str, enum.Enum):
    PROBLEM_DECOMPOSITION = "problem_decomposition"
    LEARNING_VELOCITY = "learning_velocity"
    AMBIGUITY_TOLERANCE = "ambiguity_tolerance"
    ADAPTATION_SPEED = "adaptation_speed"
    REASONING_CONSISTENCY = "reasoning_consistency"


# ---------------------------------------------------------------------------
# Stage 1: Market graph foundation
# ---------------------------------------------------------------------------

class MarketEvent(Base):
    __tablename__ = "market_events"
    __table_args__ = (
        UniqueConstraint("event_type", "job_hash", name="uq_event_type_job_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4,
    )
    event_type: Mapped[MarketEventType] = mapped_column(
        Enum(MarketEventType, native_enum=False), nullable=False,
    )
    source_type: Mapped[str] = mapped_column(String(50), default="linkedin")
    job_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True,
    )
    company: Mapped[str] = mapped_column(String(500), default="")
    title: Mapped[str] = mapped_column(String(500), default="")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )


class MarketExtraction(Base):
    __tablename__ = "market_extractions"
    __table_args__ = (
        UniqueConstraint(
            "event_id", "extractor_version", name="uq_event_extractor",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("market_events.id"), nullable=False, index=True,
    )
    extractor_version: Mapped[str] = mapped_column(
        String(100), nullable=False,
    )
    status: Mapped[ExtractionStatus] = mapped_column(
        Enum(ExtractionStatus, native_enum=False),
        default=ExtractionStatus.PENDING,
    )
    explicit_skills: Mapped[list] = mapped_column(JSON, default=list)
    inferred_skills: Mapped[list] = mapped_column(JSON, default=list)
    tasks: Mapped[list] = mapped_column(JSON, default=list)
    problems: Mapped[list] = mapped_column(JSON, default=list)
    tools: Mapped[list] = mapped_column(JSON, default=list)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )
    graphed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )


class MarketEntity(Base):
    __tablename__ = "market_entities"
    __table_args__ = (
        UniqueConstraint(
            "entity_type", "canonical_name", name="uq_entity_type_name",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )
    entity_type: Mapped[EntityType] = mapped_column(
        Enum(EntityType, native_enum=False), nullable=False,
    )
    canonical_name: Mapped[str] = mapped_column(String(500), nullable=False)
    display_name: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )


class MarketAlias(Base):
    __tablename__ = "market_aliases"
    __table_args__ = (
        UniqueConstraint("alias_text", name="uq_alias_text"),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )
    alias_text: Mapped[str] = mapped_column(String(500), nullable=False)
    entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("market_entities.id"), nullable=False, index=True,
    )


class MarketEvidence(Base):
    __tablename__ = "market_evidence"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )
    extraction_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("market_extractions.id"),
        nullable=True, index=True,
    )
    subject_type: Mapped[SubjectType] = mapped_column(
        Enum(SubjectType, native_enum=False), nullable=False,
    )
    subject_key: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True,
    )
    entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("market_entities.id"), nullable=False, index=True,
    )
    evidence_type: Mapped[EvidenceType] = mapped_column(
        Enum(EvidenceType, native_enum=False), nullable=False,
    )
    polarity: Mapped[Polarity] = mapped_column(
        Enum(Polarity, native_enum=False), default=Polarity.POSITIVE,
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    source_excerpt: Mapped[str] = mapped_column(Text, default="")
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )


class MarketEdge(Base):
    __tablename__ = "market_edges"
    __table_args__ = (
        UniqueConstraint(
            "src_entity_id", "dst_entity_id", "edge_type", name="uq_edge",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )
    src_entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("market_entities.id"), nullable=False, index=True,
    )
    dst_entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("market_entities.id"), nullable=False, index=True,
    )
    edge_type: Mapped[EdgeType] = mapped_column(
        Enum(EdgeType, native_enum=False), nullable=False,
    )
    weight: Mapped[float] = mapped_column(Float, default=0.0)
    count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )
    bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True,
    )
    entity_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("market_entities.id"), nullable=True, index=True,
    )
    edge_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("market_edges.id"), nullable=True,
    )
    frequency: Mapped[float] = mapped_column(Float, default=0.0)
    momentum: Mapped[float] = mapped_column(Float, default=0.0)
    novelty: Mapped[float] = mapped_column(Float, default=0.0)
    burst: Mapped[float] = mapped_column(Float, default=0.0)


# ---------------------------------------------------------------------------
# Stage 2: Dialogue tables (schema created now, logic deferred)
# ---------------------------------------------------------------------------

class DialogueSession(Base):
    __tablename__ = "dialogue_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4,
    )
    subject_type: Mapped[SubjectType] = mapped_column(
        Enum(SubjectType, native_enum=False), nullable=False,
    )
    subject_key: Mapped[str] = mapped_column(String(255), nullable=False)
    session_type: Mapped[SessionType] = mapped_column(
        Enum(SessionType, native_enum=False), nullable=False,
    )
    source: Mapped[str] = mapped_column(String(255), default="")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )


class DialogueTurn(Base):
    __tablename__ = "dialogue_turns"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("dialogue_sessions.id"), nullable=False, index=True,
    )
    speaker: Mapped[str] = mapped_column(String(100), nullable=False)
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, default="")
    response_text: Mapped[str] = mapped_column(Text, default="")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )


class DialogueAssessment(Base):
    __tablename__ = "dialogue_assessments"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("dialogue_sessions.id"), nullable=False, index=True,
    )
    assessment_type: Mapped[AssessmentType] = mapped_column(
        Enum(AssessmentType, native_enum=False), nullable=False,
    )
    score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_span: Mapped[str] = mapped_column(Text, default="")
    assessor_version: Mapped[str] = mapped_column(String(100), default="")


# ---------------------------------------------------------------------------
# Stage 3: Candidate model, role model, matching (schema created now)
# ---------------------------------------------------------------------------

class CandidateCapability(Base):
    __tablename__ = "candidate_capabilities"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )
    candidate_key: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True,
    )
    entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("market_entities.id"), nullable=False, index=True,
    )
    proficiency_estimate: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    recency: Mapped[float] = mapped_column(Float, default=0.0)
    transferability: Mapped[float] = mapped_column(Float, default=0.0)
    supporting_evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    contradicting_evidence_count: Mapped[int] = mapped_column(
        Integer, default=0,
    )


class RoleRequirement(Base):
    __tablename__ = "role_requirements"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )
    role_key: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True,
    )
    entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("market_entities.id"), nullable=False, index=True,
    )
    importance: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    learnability: Mapped[float] = mapped_column(Float, default=0.0)
    supporting_evidence_count: Mapped[int] = mapped_column(Integer, default=0)


class MatchExplanation(Base):
    __tablename__ = "match_explanations"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )
    candidate_key: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True,
    )
    role_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    job_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True,
    )
    success_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    learning_upside: Mapped[float] = mapped_column(Float, default=0.0)
    mismatch_risk: Mapped[float] = mapped_column(Float, default=0.0)
    hard_gaps: Mapped[list] = mapped_column(JSON, default=list)
    soft_gaps: Mapped[list] = mapped_column(JSON, default=list)
    learnable_gaps: Mapped[list] = mapped_column(JSON, default=list)
    explanation_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )

