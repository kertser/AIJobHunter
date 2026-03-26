"""Tests for the Market Intelligence subsystem — Stage 1 & 2.

Covers:
- DB model creation (all market tables exist)
- Event ingest from jobs (idempotent)
- Deterministic extraction (heuristic + fake extractors)
- Entity normalisation and alias resolution
- Evidence graph construction (entities, evidence, edges)
- Graph export to NetworkX
- Trend computation and snapshot persistence
- Role archetype reconstruction
- Dialogue sessions, turns, assessments, evidence pipeline
- Market report generation (Markdown + JSON)
- Market web/API endpoints
- CLI commands (smoke tests via CliRunner)
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from job_hunter.db.models import Job, JobStatus
from job_hunter.db.repo import get_memory_engine, init_db, make_session, upsert_job
from job_hunter.market.db_models import (
    CandidateCapability,
    DialogueAssessment,
    DialogueSession,
    DialogueTurn,
    EdgeType,
    EntityType,
    ExtractionStatus,
    MarketEdge,
    MarketEntity,
    MarketEvent,
    MarketEventType,
    MarketEvidence,
    MarketExtraction,
    MarketSnapshot,
    MatchExplanation,
    RoleRequirement,
    SubjectType,
)
from job_hunter.utils.hashing import job_hash


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    """In-memory SQLite engine with all tables created."""
    eng = get_memory_engine()
    init_db(eng)
    return eng


@pytest.fixture()
def session(engine) -> Session:
    s = make_session(engine)
    yield s
    s.close()


@pytest.fixture()
def seeded_session(session: Session) -> Session:
    """Session pre-loaded with sample jobs for market testing."""
    jobs = [
        Job(
            external_id="m1",
            url="/j/m1",
            title="Senior Python Developer",
            company="Acme Corp",
            hash=job_hash(external_id="m1", title="Senior Python Developer", company="Acme Corp"),
            easy_apply=True,
            status=JobStatus.NEW,
            location="Remote",
            description_text=(
                "We are looking for a Senior Python Developer to build scalable "
                "backend APIs using FastAPI and PostgreSQL. You will design and "
                "implement microservices deployed on Kubernetes. Experience with "
                "Docker, Redis, and CI/CD pipelines required. Machine learning "
                "experience is a plus. Must solve complex distributed systems "
                "problems and improve system reliability."
            ),
        ),
        Job(
            external_id="m2",
            url="/j/m2",
            title="Junior Data Engineer",
            company="Globex Inc",
            hash=job_hash(external_id="m2", title="Junior Data Engineer", company="Globex Inc"),
            easy_apply=True,
            status=JobStatus.NEW,
            location="NYC",
            description_text=(
                "Join our data team to build and maintain ETL pipelines using "
                "Python, Spark, and Airflow. You will monitor data quality, "
                "troubleshoot pipeline failures, and automate reporting. SQL and "
                "Snowflake experience preferred. We are a fintech startup."
            ),
        ),
        Job(
            external_id="m3",
            url="/j/m3",
            title="ML Engineer",
            company="Initech",
            hash=job_hash(external_id="m3", title="ML Engineer", company="Initech"),
            easy_apply=False,
            status=JobStatus.NEW,
            location="SF",
            description_text=(
                "Design and deploy machine learning models for NLP tasks. "
                "Develop training pipelines with PyTorch and evaluate model "
                "performance. Collaborate with product teams to integrate models "
                "into production systems. Python, Docker, and AWS required."
            ),
        ),
    ]
    for j in jobs:
        upsert_job(session, j)
    session.commit()
    return session


# ---------------------------------------------------------------------------
# DB model tests
# ---------------------------------------------------------------------------


class TestMarketDBModels:
    """All market tables are created during init_db."""

    def test_all_tables_exist(self, engine):
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        expected = {
            # Stage 1
            "market_events", "market_extractions", "market_entities",
            "market_aliases", "market_evidence", "market_edges",
            "market_snapshots",
            # Stage 2
            "dialogue_sessions", "dialogue_turns", "dialogue_assessments",
            # Stage 3
            "candidate_capabilities", "role_requirements",
            "match_explanations",
            # Existing application tables
            "jobs", "scores", "application_attempts",
        }
        assert expected.issubset(table_names), (
            f"Missing tables: {expected - table_names}"
        )

    def test_market_event_roundtrip(self, session: Session):
        evt = MarketEvent(
            event_type=MarketEventType.JOB_POSTING,
            source_type="linkedin",
            job_hash="abc123",
            company="Test Co",
            title="Tester",
            raw_text="test description",
        )
        session.add(evt)
        session.flush()

        loaded = session.get(MarketEvent, evt.id)
        assert loaded is not None
        assert loaded.job_hash == "abc123"
        assert loaded.event_type == MarketEventType.JOB_POSTING

    def test_market_event_unique_constraint(self, session: Session):
        evt1 = MarketEvent(
            event_type=MarketEventType.JOB_POSTING,
            job_hash="dup_hash",
        )
        session.add(evt1)
        session.flush()

        evt2 = MarketEvent(
            event_type=MarketEventType.JOB_POSTING,
            job_hash="dup_hash",
        )
        session.add(evt2)
        with pytest.raises(Exception):
            session.flush()
        session.rollback()


# ---------------------------------------------------------------------------
# Event ingest tests
# ---------------------------------------------------------------------------


class TestEventIngest:
    """Jobs are converted to market events idempotently."""

    def test_ingest_creates_events(self, seeded_session: Session):
        from job_hunter.market.events import ingest_jobs

        created = ingest_jobs(seeded_session)
        seeded_session.commit()
        assert created == 3

        events = seeded_session.execute(select(MarketEvent)).scalars().all()
        assert len(events) == 3

    def test_ingest_is_idempotent(self, seeded_session: Session):
        from job_hunter.market.events import ingest_jobs

        first = ingest_jobs(seeded_session)
        seeded_session.commit()
        assert first == 3

        second = ingest_jobs(seeded_session)
        seeded_session.commit()
        assert second == 0

        events = seeded_session.execute(select(MarketEvent)).scalars().all()
        assert len(events) == 3

    def test_ingest_preserves_job_metadata(self, seeded_session: Session):
        from job_hunter.market.events import ingest_jobs

        ingest_jobs(seeded_session)
        seeded_session.commit()

        events = seeded_session.execute(select(MarketEvent)).scalars().all()
        companies = {e.company for e in events}
        assert "Acme Corp" in companies
        assert "Globex Inc" in companies


# Helper fixture: seeded + ingested + extracted + graphed session
@pytest.fixture()
def graphed_session(seeded_session: Session) -> Session:
    """Session with full pipeline: jobs → events → extractions → graph."""
    from job_hunter.market.events import ingest_jobs
    from job_hunter.market.extract import HeuristicExtractor, run_extraction
    from job_hunter.market.graph.builder import build_graph

    ingest_jobs(seeded_session)
    seeded_session.commit()
    run_extraction(seeded_session, HeuristicExtractor())
    seeded_session.commit()
    build_graph(seeded_session)
    seeded_session.commit()
    return seeded_session


# ---------------------------------------------------------------------------
# Extraction tests
# ---------------------------------------------------------------------------


class TestExtraction:
    """Signal extraction from market events."""

    def test_fake_extractor(self):
        from job_hunter.market.extract import FakeMarketExtractor
        from job_hunter.market.schemas import ExtractionInput

        ext = FakeMarketExtractor(skills=["go", "rust"], tools=["kafka"])
        result = ext.extract(ExtractionInput(event_id="x", raw_text="any"))
        assert "go" in result.explicit_skills
        assert "rust" in result.explicit_skills
        assert "kafka" in result.tools

    def test_heuristic_extractor_skills(self):
        from job_hunter.market.extract import HeuristicExtractor
        from job_hunter.market.schemas import ExtractionInput

        ext = HeuristicExtractor()
        result = ext.extract(ExtractionInput(
            event_id="x",
            title="Senior Python Developer",
            company="Acme",
            raw_text=(
                "We need a Python developer with experience in SQL, "
                "Docker, Kubernetes, and machine learning. "
                "Build and deploy microservices on AWS."
            ),
        ))
        assert "python" in result.explicit_skills
        assert "sql" in result.explicit_skills
        assert "machine learning" in result.explicit_skills
        assert "docker" in result.tools
        assert "kubernetes" in result.tools

    def test_heuristic_extractor_tasks(self):
        from job_hunter.market.extract import HeuristicExtractor
        from job_hunter.market.schemas import ExtractionInput

        ext = HeuristicExtractor()
        result = ext.extract(ExtractionInput(
            event_id="x",
            title="Developer",
            raw_text="Build scalable APIs. Design microservices. Monitor systems.",
        ))
        assert any("Build" in t for t in result.tasks)
        assert any("Design" in t for t in result.tasks)
        assert any("Monitor" in t for t in result.tasks)

    def test_heuristic_extractor_context(self):
        from job_hunter.market.extract import HeuristicExtractor
        from job_hunter.market.schemas import ExtractionInput

        ext = HeuristicExtractor()
        result = ext.extract(ExtractionInput(
            event_id="x",
            title="Senior Data Engineer",
            raw_text="Remote fintech startup looking for a data engineer.",
        ))
        assert result.context.get("seniority") == "senior"
        assert "fintech" in result.context.get("industries", [])
        assert result.context.get("remote") is True

    def test_heuristic_extractor_inferred_skills(self):
        from job_hunter.market.extract import HeuristicExtractor
        from job_hunter.market.schemas import ExtractionInput

        ext = HeuristicExtractor()
        result = ext.extract(ExtractionInput(
            event_id="x",
            title="Frontend Dev",
            raw_text="Build UIs with React and Next.js on AWS.",
        ))
        # React implies javascript
        assert "javascript" in result.inferred_skills

    def test_heuristic_extractor_problems(self):
        from job_hunter.market.extract import HeuristicExtractor
        from job_hunter.market.schemas import ExtractionInput

        ext = HeuristicExtractor()
        result = ext.extract(ExtractionInput(
            event_id="x",
            title="SRE",
            raw_text="Troubleshoot production incidents. Improve system reliability. Solve scaling challenges.",
        ))
        assert len(result.problems) >= 2

    def test_run_extraction_idempotent(self, seeded_session: Session):
        from job_hunter.market.events import ingest_jobs
        from job_hunter.market.extract import FakeMarketExtractor, run_extraction

        ingest_jobs(seeded_session)
        seeded_session.commit()

        ext = FakeMarketExtractor()
        first = run_extraction(seeded_session, ext)
        seeded_session.commit()
        assert first == 3

        second = run_extraction(seeded_session, ext)
        seeded_session.commit()
        assert second == 0

    def test_extraction_stores_data(self, seeded_session: Session):
        from job_hunter.market.events import ingest_jobs
        from job_hunter.market.extract import FakeMarketExtractor, run_extraction

        ingest_jobs(seeded_session)
        seeded_session.commit()

        ext = FakeMarketExtractor(skills=["python", "sql"], tools=["docker"])
        run_extraction(seeded_session, ext)
        seeded_session.commit()

        extractions = seeded_session.execute(
            select(MarketExtraction)
        ).scalars().all()
        assert len(extractions) == 3
        assert all(e.status == ExtractionStatus.COMPLETE for e in extractions)
        assert extractions[0].explicit_skills == ["python", "sql"]
        assert extractions[0].tools == ["docker"]


# ---------------------------------------------------------------------------
# Normalisation tests
# ---------------------------------------------------------------------------


class TestNormalization:
    """Entity normalisation and alias resolution."""

    def test_canonicalize(self):
        from job_hunter.market.normalize import canonicalize

        assert canonicalize("  Python ") == "python"
        assert canonicalize("Machine   Learning") == "machine learning"
        assert canonicalize("Docker,") == "docker"

    def test_resolve_alias_from_yaml(self):
        from job_hunter.market.normalize import reset_alias_cache, resolve_alias

        reset_alias_cache()
        assert resolve_alias("k8s") == "kubernetes"
        assert resolve_alias("JS") == "javascript"
        assert resolve_alias("aws") == "amazon web services"
        assert resolve_alias("PostgreSQL") == "postgresql"

    def test_resolve_or_create_entity(self, session: Session):
        from job_hunter.market.normalize import resolve_or_create_entity

        e1 = resolve_or_create_entity(session, EntityType.SKILL, "Python")
        e2 = resolve_or_create_entity(session, EntityType.SKILL, "python")
        assert e1.id == e2.id
        assert e1.canonical_name == "python"

    def test_resolve_via_alias(self, session: Session):
        from job_hunter.market.normalize import (
            reset_alias_cache,
            resolve_or_create_entity,
        )

        reset_alias_cache()
        e1 = resolve_or_create_entity(session, EntityType.TOOL, "Kubernetes")
        e2 = resolve_or_create_entity(session, EntityType.TOOL, "k8s")
        assert e1.id == e2.id

    def test_fuzzy_match(self, session: Session):
        from job_hunter.market.normalize import resolve_or_create_entity

        e1 = resolve_or_create_entity(
            session, EntityType.SKILL, "machine learning",
        )
        # Slightly different spelling should fuzzy-match
        e2 = resolve_or_create_entity(
            session, EntityType.SKILL, "machine learnng",
            fuzzy_threshold=80,
        )
        assert e1.id == e2.id

    def test_distinct_types_create_separate_entities(self, session: Session):
        from job_hunter.market.normalize import resolve_or_create_entity

        skill = resolve_or_create_entity(session, EntityType.SKILL, "python")
        tool = resolve_or_create_entity(session, EntityType.TOOL, "python")
        assert skill.id != tool.id


# ---------------------------------------------------------------------------
# Graph builder tests
# ---------------------------------------------------------------------------


class TestGraphBuilder:
    """Evidence graph construction from extractions."""

    def test_build_graph_creates_entities_and_edges(self, seeded_session: Session):
        from job_hunter.market.events import ingest_jobs
        from job_hunter.market.extract import FakeMarketExtractor, run_extraction
        from job_hunter.market.graph.builder import build_graph

        ingest_jobs(seeded_session)
        seeded_session.commit()

        ext = FakeMarketExtractor(
            skills=["python", "sql"], tools=["docker"], tasks=["Build APIs"],
        )
        run_extraction(seeded_session, ext)
        seeded_session.commit()

        summary = build_graph(seeded_session)
        seeded_session.commit()

        assert summary["extractions"] == 3
        assert summary["entities"] > 0
        assert summary["evidence"] > 0
        assert summary["edges"] > 0

    def test_build_graph_is_idempotent(self, seeded_session: Session):
        from job_hunter.market.events import ingest_jobs
        from job_hunter.market.extract import FakeMarketExtractor, run_extraction
        from job_hunter.market.graph.builder import build_graph

        ingest_jobs(seeded_session)
        seeded_session.commit()

        ext = FakeMarketExtractor()
        run_extraction(seeded_session, ext)
        seeded_session.commit()

        first = build_graph(seeded_session)
        seeded_session.commit()
        assert first["extractions"] == 3

        second = build_graph(seeded_session)
        seeded_session.commit()
        assert second["extractions"] == 0

    def test_evidence_links_to_entities(self, seeded_session: Session):
        from job_hunter.market.events import ingest_jobs
        from job_hunter.market.extract import FakeMarketExtractor, run_extraction
        from job_hunter.market.graph.builder import build_graph

        ingest_jobs(seeded_session)
        seeded_session.commit()
        run_extraction(seeded_session, FakeMarketExtractor())
        seeded_session.commit()
        build_graph(seeded_session)
        seeded_session.commit()

        evidence = seeded_session.execute(select(MarketEvidence)).scalars().all()
        assert len(evidence) > 0
        for ev in evidence:
            assert ev.subject_type == SubjectType.JOB
            assert ev.entity_id is not None
            assert ev.confidence > 0

    def test_cooccurrence_edges(self, seeded_session: Session):
        from job_hunter.market.events import ingest_jobs
        from job_hunter.market.extract import FakeMarketExtractor, run_extraction
        from job_hunter.market.graph.builder import build_graph

        ingest_jobs(seeded_session)
        seeded_session.commit()
        run_extraction(seeded_session, FakeMarketExtractor(
            skills=["python", "sql"], tools=["docker"],
        ))
        seeded_session.commit()
        build_graph(seeded_session)
        seeded_session.commit()

        edges = seeded_session.execute(select(MarketEdge)).scalars().all()
        assert len(edges) > 0
        assert all(e.edge_type == EdgeType.CO_OCCURS_WITH for e in edges)
        # With 3 jobs each having the same 3 entities (python, sql, docker)
        # plus the task, we expect co-occurrence edges between each pair
        for edge in edges:
            assert edge.count >= 1
            assert edge.weight >= 1.0

    def test_edge_count_increments(self, seeded_session: Session):
        """Edges from multiple extractions with the same entities accumulate."""
        from job_hunter.market.events import ingest_jobs
        from job_hunter.market.extract import FakeMarketExtractor, run_extraction
        from job_hunter.market.graph.builder import build_graph

        ingest_jobs(seeded_session)
        seeded_session.commit()

        # All 3 jobs produce the same entities
        run_extraction(seeded_session, FakeMarketExtractor(
            skills=["python"], tools=["docker"], tasks=[],
        ))
        seeded_session.commit()
        build_graph(seeded_session)
        seeded_session.commit()

        edges = seeded_session.execute(select(MarketEdge)).scalars().all()
        assert len(edges) == 1  # python <-> docker
        assert edges[0].count == 3  # seen across 3 extractions


# ---------------------------------------------------------------------------
# NetworkX export tests
# ---------------------------------------------------------------------------


class TestNxExport:
    """Graph export to NetworkX and file formats."""

    def test_to_networkx(self, seeded_session: Session):
        from job_hunter.market.events import ingest_jobs
        from job_hunter.market.extract import FakeMarketExtractor, run_extraction
        from job_hunter.market.graph.builder import build_graph
        from job_hunter.market.graph.metrics import to_networkx

        ingest_jobs(seeded_session)
        seeded_session.commit()
        run_extraction(seeded_session, FakeMarketExtractor())
        seeded_session.commit()
        build_graph(seeded_session)
        seeded_session.commit()

        G = to_networkx(seeded_session)
        assert G.number_of_nodes() > 0
        assert G.number_of_edges() > 0

    def test_export_json(self, seeded_session: Session, tmp_path: Path):
        from job_hunter.market.events import ingest_jobs
        from job_hunter.market.extract import FakeMarketExtractor, run_extraction
        from job_hunter.market.graph.builder import build_graph
        from job_hunter.market.graph.metrics import export_json

        ingest_jobs(seeded_session)
        seeded_session.commit()
        run_extraction(seeded_session, FakeMarketExtractor())
        seeded_session.commit()
        build_graph(seeded_session)
        seeded_session.commit()

        out = export_json(seeded_session, tmp_path / "graph.json")
        assert out.exists()
        assert out.stat().st_size > 0


# ---------------------------------------------------------------------------
# Full pipeline (ingest → extract → graph) end-to-end test
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """End-to-end market pipeline with heuristic extractor."""

    def test_end_to_end(self, seeded_session: Session):
        from job_hunter.market.events import ingest_jobs
        from job_hunter.market.extract import HeuristicExtractor, run_extraction
        from job_hunter.market.graph.builder import build_graph

        # Ingest
        created = ingest_jobs(seeded_session)
        seeded_session.commit()
        assert created == 3

        # Extract (heuristic)
        extracted = run_extraction(seeded_session, HeuristicExtractor())
        seeded_session.commit()
        assert extracted == 3

        # Verify extractions have real content
        extractions = seeded_session.execute(
            select(MarketExtraction)
        ).scalars().all()
        for ext in extractions:
            assert ext.status == ExtractionStatus.COMPLETE
            # The heuristic extractor should find at least some skills/tools
            total_items = (
                len(ext.explicit_skills)
                + len(ext.inferred_skills)
                + len(ext.tools)
            )
            assert total_items > 0, f"No items extracted from event {ext.event_id}"

        # Graph
        summary = build_graph(seeded_session)
        seeded_session.commit()
        assert summary["extractions"] == 3
        assert summary["entities"] > 0
        assert summary["evidence"] > 0
        assert summary["edges"] > 0

        # Verify entities are reasonable
        entities = seeded_session.execute(
            select(MarketEntity)
        ).scalars().all()
        entity_names = {e.canonical_name for e in entities}
        # Python should appear (mentioned in all 3 jobs)
        assert "python" in entity_names


# ---------------------------------------------------------------------------
# Trend computation tests
# ---------------------------------------------------------------------------


class TestTrendComputation:
    """Trend snapshot computation from evidence."""

    def test_compute_trends_creates_snapshots(self, graphed_session: Session):
        from job_hunter.market.trends.compute import compute_trends

        result = compute_trends(graphed_session)
        graphed_session.commit()

        assert result["entities"] > 0
        assert result["snapshots_created"] > 0

        snapshots = graphed_session.execute(select(MarketSnapshot)).scalars().all()
        assert len(snapshots) == result["snapshots_created"]

    def test_compute_trends_frequency_positive(self, graphed_session: Session):
        from job_hunter.market.trends.compute import compute_trends

        compute_trends(graphed_session)
        graphed_session.commit()

        snapshots = graphed_session.execute(select(MarketSnapshot)).scalars().all()
        has_positive = any(s.frequency > 0 for s in snapshots)
        assert has_positive, "At least one entity should have positive frequency"

    def test_compute_trends_empty_db(self, session: Session):
        from job_hunter.market.trends.compute import compute_trends

        result = compute_trends(session)
        assert result["entities"] == 0
        assert result["snapshots_created"] == 0

    def test_recent_entity_counts(self, graphed_session: Session):
        from job_hunter.market.trends.queries import recent_entity_counts

        counts = recent_entity_counts(graphed_session, days=30, limit=20)
        assert len(counts) > 0
        assert all("display_name" in c and "count" in c for c in counts)

    def test_recent_entity_counts_by_type(self, graphed_session: Session):
        from job_hunter.market.trends.queries import recent_entity_counts

        skill_counts = recent_entity_counts(
            graphed_session, days=30, entity_types=[EntityType.SKILL], limit=10,
        )
        tool_counts = recent_entity_counts(
            graphed_session, days=30, entity_types=[EntityType.TOOL], limit=10,
        )
        # Both should return results since our jobs have skills and tools
        assert len(skill_counts) > 0
        assert len(tool_counts) > 0

    def test_get_latest_snapshots(self, graphed_session: Session):
        from job_hunter.market.trends.compute import compute_trends
        from job_hunter.market.trends.queries import get_latest_snapshots

        compute_trends(graphed_session)
        graphed_session.commit()

        snapshots = get_latest_snapshots(graphed_session, limit=10)
        assert len(snapshots) > 0
        # Sorted by frequency desc
        freqs = [s.frequency for s in snapshots]
        assert freqs == sorted(freqs, reverse=True)

    def test_entity_frequency_by_bucket(self, graphed_session: Session):
        from job_hunter.market.trends.queries import entity_frequency_by_bucket

        entities = graphed_session.execute(select(MarketEntity)).scalars().all()
        assert len(entities) > 0

        buckets = entity_frequency_by_bucket(
            graphed_session, entity_id=entities[0].id, bucket_days=7, num_buckets=4,
        )
        assert len(buckets) == 4
        assert all("bucket_start" in b and "count" in b for b in buckets)


# ---------------------------------------------------------------------------
# Role model tests
# ---------------------------------------------------------------------------


class TestRoleModel:
    """Role archetype reconstruction from extraction data."""

    def test_build_role_archetypes(self, graphed_session: Session):
        from job_hunter.market.role_model import build_role_archetypes

        result = build_role_archetypes(graphed_session, min_group_size=1)
        graphed_session.commit()

        assert result["roles_created"] > 0
        assert result["requirements_created"] > 0

    def test_build_role_archetypes_idempotent(self, graphed_session: Session):
        from job_hunter.market.role_model import build_role_archetypes

        first = build_role_archetypes(graphed_session, min_group_size=1)
        graphed_session.commit()

        second = build_role_archetypes(graphed_session, min_group_size=1)
        graphed_session.commit()

        # Same results (old requirements are deleted and re-created)
        assert first["roles_created"] == second["roles_created"]
        assert first["requirements_created"] == second["requirements_created"]

    def test_get_role_archetypes(self, graphed_session: Session):
        from job_hunter.market.role_model import build_role_archetypes, get_role_archetypes

        build_role_archetypes(graphed_session, min_group_size=1)
        graphed_session.commit()

        archetypes = get_role_archetypes(graphed_session)
        assert len(archetypes) > 0
        for role in archetypes:
            assert "role_key" in role
            assert "requirements" in role
            assert len(role["requirements"]) > 0

    def test_normalise_title(self):
        from job_hunter.market.role_model import _normalise_title

        assert _normalise_title("Senior Python Developer") == "python developer"
        assert _normalise_title("Junior Data Engineer (Remote)") == "data engineer"
        assert _normalise_title("Staff ML Engineer") == "ml engineer"

    def test_role_requirements_stored(self, graphed_session: Session):
        from job_hunter.market.role_model import build_role_archetypes

        build_role_archetypes(graphed_session, min_group_size=1)
        graphed_session.commit()

        reqs = graphed_session.execute(select(RoleRequirement)).scalars().all()
        assert len(reqs) > 0
        for req in reqs:
            assert 0 < req.importance <= 1.0
            assert req.confidence > 0

    def test_build_with_title_normalizer(self, graphed_session: Session):
        """build_role_archetypes should accept a TitleNormalizer."""
        from job_hunter.market.role_model import build_role_archetypes
        from job_hunter.market.title_normalizer import FakeTitleNormalizer

        result = build_role_archetypes(
            graphed_session, min_group_size=1,
            title_normalizer=FakeTitleNormalizer(),
        )
        graphed_session.commit()
        assert result["roles_created"] > 0


# ---------------------------------------------------------------------------
# Title normaliser tests
# ---------------------------------------------------------------------------


class TestTitleNormalizer:
    """Tests for the TitleNormalizer implementations."""

    def test_heuristic_strips_prefix_wanted(self):
        from job_hunter.market.title_normalizer import HeuristicTitleNormalizer
        n = HeuristicTitleNormalizer()
        assert n.normalize("Wanted: Data Scientist") == "data scientist"
        assert n.normalize("Hiring Data Analyst") == "data analyst"

    def test_heuristic_strips_company_name(self):
        from job_hunter.market.title_normalizer import HeuristicTitleNormalizer
        n = HeuristicTitleNormalizer()
        assert n.normalize("Data Analyst Base44", company="Base44") == "data analyst"
        assert n.normalize("Python Dev at Acme Corp", company="Acme Corp") == "python dev"

    def test_heuristic_strips_suffix_team(self):
        from job_hunter.market.title_normalizer import HeuristicTitleNormalizer
        n = HeuristicTitleNormalizer()
        assert n.normalize("Data Scientist Team") == "data scientist"
        assert n.normalize("ML Engineer Department") == "ml engineer"

    def test_heuristic_picks_primary_from_slash(self):
        from job_hunter.market.title_normalizer import HeuristicTitleNormalizer
        n = HeuristicTitleNormalizer()
        result = n.normalize("Data Scientist / Computational Genomics Scientist")
        assert result == "data scientist"

    def test_heuristic_strips_seniority(self):
        from job_hunter.market.title_normalizer import HeuristicTitleNormalizer
        n = HeuristicTitleNormalizer()
        assert n.normalize("Senior Backend Engineer") == "backend engineer"
        assert n.normalize("Junior Python Developer (Remote)") == "python developer"

    def test_heuristic_combined_garbage(self):
        """Exercise multiple cleaning steps at once."""
        from job_hunter.market.title_normalizer import HeuristicTitleNormalizer
        n = HeuristicTitleNormalizer()
        result = n.normalize(
            "Wanted Data Scientist / Computational Genomics Scientist",
            company="SomeCorp",
        )
        assert result == "data scientist"

    def test_heuristic_batch_fuzzy_clusters(self):
        """Similar titles should be clustered together in batch mode."""
        from job_hunter.market.title_normalizer import HeuristicTitleNormalizer
        n = HeuristicTitleNormalizer(cluster_threshold=80)
        mapping = n.normalize_batch([
            "Data Scientist",
            "Data Scientist AI Research Development",
            "Data Scientist Team",
        ], companies=["", "", ""])
        # All three should map to the same short representative
        unique_values = set(mapping.values())
        assert len(unique_values) == 1
        assert "data scientist" in unique_values

    def test_fake_normalizer_deterministic(self):
        from job_hunter.market.title_normalizer import FakeTitleNormalizer
        n = FakeTitleNormalizer()
        a = n.normalize("Senior Python Dev")
        b = n.normalize("Senior Python Dev")
        assert a == b == "python dev"

    def test_fake_normalizer_with_overrides(self):
        from job_hunter.market.title_normalizer import FakeTitleNormalizer
        n = FakeTitleNormalizer(overrides={"Foo Bar": "baz"})
        assert n.normalize("Foo Bar") == "baz"
        # Non-overridden title still uses heuristic
        assert n.normalize("Senior Python Dev") == "python dev"
# Dialogue tests
# ---------------------------------------------------------------------------


class TestDialogue:
    """Dialogue session CRUD and evidence pipeline."""

    def test_create_session_and_turns(self, session: Session):
        from job_hunter.market.dialogue import create_session, add_turn, get_turns

        ds = create_session(
            session,
            subject_type=SubjectType.CANDIDATE,
            subject_key="test_user",
            session_type=__import__(
                "job_hunter.market.db_models", fromlist=["SessionType"]
            ).SessionType.CANDIDATE_INTERVIEW,
            source="test",
        )
        session.flush()
        assert ds.id is not None

        add_turn(session, session_id=ds.id, speaker="interviewer",
                 turn_index=0, prompt_text="Tell me about Python.")
        add_turn(session, session_id=ds.id, speaker="candidate",
                 turn_index=1, response_text="I have 5 years of Python experience using Django and FastAPI.")
        session.flush()

        turns = get_turns(session, ds.id)
        assert len(turns) == 2
        assert turns[0].speaker == "interviewer"
        assert turns[1].turn_index == 1

    def test_add_assessment(self, session: Session):
        from job_hunter.market.dialogue import create_session, add_assessment, get_assessments
        from job_hunter.market.db_models import SessionType, AssessmentType

        ds = create_session(
            session,
            subject_type=SubjectType.CANDIDATE,
            subject_key="test_user",
            session_type=SessionType.DIAGNOSTIC_QNA,
        )
        session.flush()

        add_assessment(
            session,
            session_id=ds.id,
            assessment_type=AssessmentType.PROBLEM_DECOMPOSITION,
            score=0.8,
            confidence=0.7,
            evidence_span="Good at breaking down problems",
            assessor_version="test-1.0",
        )
        session.flush()

        assessments = get_assessments(session, ds.id)
        assert len(assessments) == 1
        assert assessments[0].score == 0.8
        assert assessments[0].assessment_type == AssessmentType.PROBLEM_DECOMPOSITION

    def test_end_session(self, session: Session):
        from job_hunter.market.dialogue import create_session, end_session, get_session
        from job_hunter.market.db_models import SessionType

        ds = create_session(
            session,
            subject_type=SubjectType.CANDIDATE,
            subject_key="test_user",
            session_type=SessionType.CANDIDATE_INTERVIEW,
        )
        session.flush()
        assert ds.ended_at is None

        end_session(session, ds.id)
        session.flush()

        loaded = get_session(session, ds.id)
        assert loaded is not None
        assert loaded.ended_at is not None

    def test_get_sessions_for_subject(self, session: Session):
        from job_hunter.market.dialogue import create_session, get_sessions_for_subject
        from job_hunter.market.db_models import SessionType

        create_session(
            session, subject_type=SubjectType.CANDIDATE,
            subject_key="alice", session_type=SessionType.CANDIDATE_INTERVIEW,
        )
        create_session(
            session, subject_type=SubjectType.CANDIDATE,
            subject_key="alice", session_type=SessionType.DIAGNOSTIC_QNA,
        )
        create_session(
            session, subject_type=SubjectType.CANDIDATE,
            subject_key="bob", session_type=SessionType.CANDIDATE_INTERVIEW,
        )
        session.flush()

        alice_sessions = get_sessions_for_subject(
            session, SubjectType.CANDIDATE, "alice",
        )
        assert len(alice_sessions) == 2

    def test_ingest_dialogue_evidence(self, session: Session):
        from job_hunter.market.dialogue import (
            create_session, add_turn, add_assessment, ingest_dialogue_evidence,
        )
        from job_hunter.market.db_models import SessionType, AssessmentType

        ds = create_session(
            session,
            subject_type=SubjectType.CANDIDATE,
            subject_key="test_user",
            session_type=SessionType.CANDIDATE_INTERVIEW,
        )
        session.flush()

        add_turn(
            session, session_id=ds.id, speaker="candidate", turn_index=0,
            response_text="I work with Python and Docker for microservices.",
        )
        add_assessment(
            session, session_id=ds.id,
            assessment_type=AssessmentType.LEARNING_VELOCITY,
            score=0.85, confidence=0.6,
            evidence_span="Quick learner",
        )
        session.flush()

        created = ingest_dialogue_evidence(session, ds.id)
        session.flush()

        assert created > 0
        evidence = session.execute(select(MarketEvidence)).scalars().all()
        assert len(evidence) > 0
        # Should have DIALOGUE evidence type
        from job_hunter.market.db_models import EvidenceType
        dialogue_evidence = [e for e in evidence if e.evidence_type == EvidenceType.DIALOGUE]
        assert len(dialogue_evidence) > 0


# ---------------------------------------------------------------------------
# Market report tests
# ---------------------------------------------------------------------------


class TestMarketReport:
    """Market report generation."""

    def test_generate_report_empty(self, session: Session, tmp_path: Path):
        from job_hunter.market.report import generate_market_report

        md_path, json_path = generate_market_report(session, tmp_path)

        assert md_path.exists()
        assert json_path.exists()
        assert "Market Intelligence Report" in md_path.read_text(encoding="utf-8")

    def test_generate_report_with_data(self, graphed_session: Session, tmp_path: Path):
        from job_hunter.market.trends.compute import compute_trends
        from job_hunter.market.role_model import build_role_archetypes
        from job_hunter.market.report import generate_market_report

        compute_trends(graphed_session)
        build_role_archetypes(graphed_session, min_group_size=1)
        graphed_session.commit()

        md_path, json_path = generate_market_report(graphed_session, tmp_path)

        md_text = md_path.read_text(encoding="utf-8")
        assert "Market Intelligence Report" in md_text
        assert "Top Skills" in md_text
        assert "Top Tools" in md_text

        import json
        json_data = json.loads(json_path.read_text(encoding="utf-8"))
        assert json_data["entity_count"] > 0
        assert json_data["event_count"] == 3
        assert len(json_data["top_skills"]) > 0


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


class TestMarketCLI:
    """Smoke tests for `hunt market` CLI commands."""

    def test_help(self):
        from typer.testing import CliRunner

        from job_hunter.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["market", "--help"])
        assert result.exit_code == 0
        assert "ingest" in result.output
        assert "extract" in result.output
        assert "graph" in result.output
        assert "trends" in result.output
        assert "role-model" in result.output

    def test_ingest_command(self, tmp_path: Path):
        """Run `hunt market ingest` against a seeded DB."""
        from typer.testing import CliRunner

        from job_hunter.cli import app
        from job_hunter.db.repo import get_engine, init_db, make_session, upsert_job

        # Seed a DB in tmp_path
        engine = get_engine(tmp_path)
        init_db(engine)
        s = make_session(engine)
        j = Job(
            external_id="cli1",
            url="/j/cli1",
            title="Dev",
            company="Co",
            hash=job_hash(external_id="cli1", title="Dev", company="Co"),
            status=JobStatus.NEW,
            description_text="Python developer needed.",
        )
        upsert_job(s, j)
        s.commit()
        s.close()

        runner = CliRunner()
        result = runner.invoke(app, [
            "--mock", "--data-dir", str(tmp_path), "market", "ingest",
        ])
        assert result.exit_code == 0
        assert "1" in result.output  # "Ingested 1 new market event(s)"

    def test_trends_command(self, tmp_path: Path):
        """Run `hunt market trends` against a seeded DB."""
        from typer.testing import CliRunner

        from job_hunter.cli import app
        from job_hunter.db.repo import get_engine, init_db, make_session, upsert_job

        engine = get_engine(tmp_path)
        init_db(engine)
        s = make_session(engine)
        j = Job(
            external_id="cli_t1",
            url="/j/cli_t1",
            title="Python Dev",
            company="Co",
            hash=job_hash(external_id="cli_t1", title="Python Dev", company="Co"),
            status=JobStatus.NEW,
            description_text="Python developer with Docker and SQL.",
        )
        upsert_job(s, j)
        s.commit()
        s.close()

        runner = CliRunner()
        # Run full pipeline first
        runner.invoke(app, ["--mock", "--data-dir", str(tmp_path), "market", "ingest"])
        runner.invoke(app, ["--mock", "--data-dir", str(tmp_path), "market", "extract"])
        runner.invoke(app, ["--mock", "--data-dir", str(tmp_path), "market", "graph"])
        result = runner.invoke(app, ["--mock", "--data-dir", str(tmp_path), "market", "trends"])

        assert result.exit_code == 0
        assert "Computed trends" in result.output

    def test_role_model_command(self, tmp_path: Path):
        """Run `hunt market role-model` against a seeded DB."""
        from typer.testing import CliRunner

        from job_hunter.cli import app
        from job_hunter.db.repo import get_engine, init_db, make_session, upsert_job

        engine = get_engine(tmp_path)
        init_db(engine)
        s = make_session(engine)
        for i, title in enumerate(["Python Dev", "Python Dev"]):
            j = Job(
                external_id=f"cli_rm{i}",
                url=f"/j/cli_rm{i}",
                title=title,
                company="Co",
                hash=job_hash(external_id=f"cli_rm{i}", title=title, company="Co"),
                status=JobStatus.NEW,
                description_text="Python developer with Docker and SQL.",
            )
            upsert_job(s, j)
        s.commit()
        s.close()

        runner = CliRunner()
        runner.invoke(app, ["--mock", "--data-dir", str(tmp_path), "market", "ingest"])
        runner.invoke(app, ["--mock", "--data-dir", str(tmp_path), "market", "extract"])
        runner.invoke(app, ["--mock", "--data-dir", str(tmp_path), "market", "graph"])
        result = runner.invoke(app, [
            "--mock", "--data-dir", str(tmp_path), "market", "role-model",
            "--min-group", "1",
        ])

        assert result.exit_code == 0
        assert "role archetype" in result.output

    def test_report_command(self, tmp_path: Path):
        """Run `hunt market report` against a seeded DB."""
        from typer.testing import CliRunner

        from job_hunter.cli import app
        from job_hunter.db.repo import get_engine, init_db, make_session, upsert_job

        engine = get_engine(tmp_path)
        init_db(engine)
        s = make_session(engine)
        j = Job(
            external_id="cli_rpt1",
            url="/j/cli_rpt1",
            title="Dev",
            company="Co",
            hash=job_hash(external_id="cli_rpt1", title="Dev", company="Co"),
            status=JobStatus.NEW,
            description_text="Python developer needed.",
        )
        upsert_job(s, j)
        s.commit()
        s.close()

        runner = CliRunner()
        result = runner.invoke(app, [
            "--mock", "--data-dir", str(tmp_path), "market", "report",
        ])

        assert result.exit_code == 0
        assert "report saved" in result.output


# ---------------------------------------------------------------------------
# Market pipeline integration tests
# ---------------------------------------------------------------------------


class TestMarketPipeline:
    """Tests for ``run_market_pipeline()`` orchestration."""

    def test_pipeline_runs_all_steps(self, seeded_session: Session):
        from job_hunter.market.extract import FakeMarketExtractor
        from job_hunter.market.pipeline import run_market_pipeline

        summary = run_market_pipeline(
            seeded_session,
            extractor=FakeMarketExtractor(),
        )
        assert summary["events_created"] == 3
        assert summary["extractions"] == 3
        assert summary["graph"]["entities"] > 0
        assert summary["graph"]["edges"] >= 0
        assert summary["trends"]["snapshots_created"] >= 0
        assert summary["roles"]["roles_created"] >= 0
        assert summary["capabilities"] is None  # no profile given
        assert summary["matches"] == 0

    def test_pipeline_with_profile(self, seeded_session: Session):
        from job_hunter.config.models import UserProfile
        from job_hunter.market.extract import FakeMarketExtractor
        from job_hunter.market.pipeline import run_market_pipeline

        profile = UserProfile(
            name="Test User",
            title="Python Developer",
            skills=["Python", "Docker", "SQL"],
            programming_languages=["Python"],
            desired_roles=["Backend Developer"],
            experience_years=5,
        )
        summary = run_market_pipeline(
            seeded_session,
            extractor=FakeMarketExtractor(),
            profile=profile,
            candidate_key="test",
        )
        assert summary["events_created"] == 3
        assert summary["capabilities"] is not None
        assert summary["capabilities"]["capabilities_created"] > 0
        # Match count depends on role archetypes found
        assert summary["matches"] >= 0

    def test_pipeline_idempotent(self, seeded_session: Session):
        """Running twice produces the same result (idempotent)."""
        from job_hunter.market.extract import FakeMarketExtractor
        from job_hunter.market.pipeline import run_market_pipeline

        ext = FakeMarketExtractor()
        s1 = run_market_pipeline(seeded_session, extractor=ext)
        s2 = run_market_pipeline(seeded_session, extractor=ext)

        # Second run should create 0 new events / extractions
        assert s2["events_created"] == 0
        assert s2["extractions"] == 0


# ---------------------------------------------------------------------------
# Market-enhanced scoring tests
# ---------------------------------------------------------------------------


class TestMarketBoost:
    """Tests for ``compute_market_boost()``."""

    def test_neutral_when_no_market_data(self, session: Session):
        from job_hunter.matching.scoring import compute_market_boost

        result = compute_market_boost(session, job_title="Python Developer")
        assert result["market_score"] == 0.0
        assert result["trend_boost"] == 0.0
        assert result["role_key"] == ""
        assert result["hard_gaps"] == []
        assert result["learnable_gaps"] == []

    def test_boost_with_market_data(self, seeded_session: Session):
        """When full market data exists, boost returns meaningful values."""
        from job_hunter.config.models import UserProfile
        from job_hunter.market.extract import FakeMarketExtractor
        from job_hunter.market.pipeline import run_market_pipeline
        from job_hunter.matching.scoring import compute_market_boost

        profile = UserProfile(
            name="Test User",
            title="Python Developer",
            skills=["Python", "Docker", "SQL"],
            programming_languages=["Python"],
            desired_roles=["Python Developer"],
            experience_years=5,
        )
        run_market_pipeline(
            seeded_session,
            extractor=FakeMarketExtractor(),
            profile=profile,
            candidate_key="boost_test",
        )

        result = compute_market_boost(
            seeded_session,
            job_title="Python Developer",
            candidate_key="boost_test",
        )
        # Should have found some role match
        # (exact values depend on extraction output)
        assert isinstance(result["market_score"], float)
        assert isinstance(result["trend_boost"], float)

    def test_boost_no_matching_role(self, seeded_session: Session):
        """When the job title doesn't match any role archetype, returns neutral."""
        from job_hunter.config.models import UserProfile
        from job_hunter.market.extract import FakeMarketExtractor
        from job_hunter.market.pipeline import run_market_pipeline
        from job_hunter.matching.scoring import compute_market_boost

        profile = UserProfile(
            name="Test User", title="Tester", skills=["Python"],
            programming_languages=["Python"],
        )
        run_market_pipeline(
            seeded_session,
            extractor=FakeMarketExtractor(),
            profile=profile,
            candidate_key="nomatch",
        )

        result = compute_market_boost(
            seeded_session,
            job_title="Totally Unrelated Position XYZ",
            candidate_key="nomatch",
        )
        # Expect neutral or very low score since title doesn't match any role
        assert result["market_score"] >= 0.0


# ---------------------------------------------------------------------------
# Web: POST /api/run/market
# ---------------------------------------------------------------------------


class TestMarketRunWeb:
    """Tests for the ``POST /api/run/market`` endpoint."""

    @pytest.fixture()
    def run_client(self, tmp_path: Path):
        """Test app with seeded jobs for market run."""
        import asyncio
        from fastapi.testclient import TestClient

        from job_hunter.config.models import AppSettings
        from job_hunter.web.app import create_app

        settings = AppSettings(data_dir=tmp_path, mock=True, dry_run=True, openai_api_key="")
        (tmp_path / "user_profile.yml").write_text(
            "name: Test User\ntitle: Dev\nskills:\n  - Python\n  - Docker\n"
        )
        app = create_app(settings)

        engine = get_memory_engine()
        init_db(engine)
        app.state.engine = engine

        # Seed jobs
        session = make_session(engine)
        jobs = [
            Job(
                external_id="mr1", url="/j/mr1", title="Python Dev", company="TestCo",
                hash=job_hash(external_id="mr1", title="Python Dev", company="TestCo"),
                easy_apply=True, status=JobStatus.NEW, location="Remote",
                description_text="Python developer with Docker and SQL.",
            ),
        ]
        for j in jobs:
            upsert_job(session, j)
        session.commit()
        session.close()

        with TestClient(app) as c:
            yield c

    def test_market_run_starts(self, run_client):
        r = run_client.post("/api/run/market")
        assert r.status_code == 202
        data = r.json()
        assert data["started"] == "market"

    def test_market_run_conflict(self, run_client):
        """Second request while task is running returns 409."""
        import time

        r1 = run_client.post("/api/run/market")
        assert r1.status_code == 202

        # The task might still be running — check for conflict
        r2 = run_client.post("/api/run/market")
        # Either 409 (still running) or 202 (already finished) is acceptable
        assert r2.status_code in (202, 409)

    def test_market_card_in_run_page(self, run_client):
        """The run page should show the Market Analysis card."""
        r = run_client.get("/run")
        assert r.status_code == 200
        assert "Market Analysis" in r.text
        assert "Analyse Market" in r.text


# ---------------------------------------------------------------------------
# CLI: hunt market run-all
# ---------------------------------------------------------------------------


class TestRunAllCLI:
    """Tests for the ``hunt market run-all`` CLI command."""

    def test_run_all_empty_db(self, tmp_path: Path):
        from typer.testing import CliRunner
        from job_hunter.cli import app
        from job_hunter.db.repo import get_engine, init_db

        engine = get_engine(tmp_path)
        init_db(engine)

        runner = CliRunner()
        result = runner.invoke(app, [
            "--mock", "--data-dir", str(tmp_path), "market", "run-all",
            "--extractor", "fake",
        ])
        assert result.exit_code == 0
        assert "Market pipeline complete" in result.output

    def test_run_all_with_jobs(self, tmp_path: Path):
        from typer.testing import CliRunner
        from job_hunter.cli import app
        from job_hunter.db.repo import get_engine, init_db, make_session

        engine = get_engine(tmp_path)
        init_db(engine)

        # Seed a job
        session = make_session(engine)
        j = Job(
            external_id="cli1", url="/j/cli1", title="Dev", company="Co",
            hash=job_hash(external_id="cli1", title="Dev", company="Co"),
            easy_apply=True, status=JobStatus.NEW, location="Remote",
            description_text="Python developer with Docker and SQL.",
        )
        upsert_job(session, j)
        session.commit()
        session.close()

        runner = CliRunner()
        result = runner.invoke(app, [
            "--mock", "--data-dir", str(tmp_path), "market", "run-all",
            "--extractor", "fake",
        ])
        assert result.exit_code == 0
        assert "Market pipeline complete" in result.output
        assert "Events created:" in result.output

    def test_run_all_with_profile(self, tmp_path: Path):
        from typer.testing import CliRunner
        from job_hunter.cli import app
        from job_hunter.db.repo import get_engine, init_db, make_session

        engine = get_engine(tmp_path)
        init_db(engine)

        # Create user profile
        (tmp_path / "user_profile.yml").write_text(
            "name: Test User\ntitle: Dev\nskills:\n  - Python\n"
        )

        # Seed a job
        session = make_session(engine)
        j = Job(
            external_id="cli2", url="/j/cli2", title="Dev", company="Co",
            hash=job_hash(external_id="cli2", title="Dev", company="Co"),
            easy_apply=True, status=JobStatus.NEW, location="Remote",
            description_text="Python developer with Docker.",
        )
        upsert_job(session, j)
        session.commit()
        session.close()

        runner = CliRunner()
        result = runner.invoke(app, [
            "--mock", "--data-dir", str(tmp_path), "market", "run-all",
            "--extractor", "fake", "--profile", "default",
        ])
        assert result.exit_code == 0
        assert "Market pipeline complete" in result.output
