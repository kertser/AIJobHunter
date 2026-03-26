"""Tests for reporting — Markdown + JSON daily report generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_hunter.db.models import Decision, Job, JobStatus, Score
from job_hunter.db.repo import (
    count_jobs_by_status,
    get_all_jobs,
    get_memory_engine,
    get_top_missing_skills,
    init_db,
    make_session,
    save_score,
    upsert_job,
)
from job_hunter.reporting.report import generate_report
from job_hunter.utils.hashing import job_hash


def _seed_db(session):
    """Insert sample jobs + scores for report testing."""
    jobs = [
        Job(
            external_id="r1", url="/j/r1", title="Python Dev", company="Acme",
            hash=job_hash(external_id="r1", title="Python Dev", company="Acme"),
            easy_apply=True, status=JobStatus.APPLIED, location="Remote",
            description_text="Python backend",
        ),
        Job(
            external_id="r2", url="/j/r2", title="Java Dev", company="Globex",
            hash=job_hash(external_id="r2", title="Java Dev", company="Globex"),
            easy_apply=True, status=JobStatus.SKIPPED, location="NYC",
            description_text="Java enterprise",
        ),
        Job(
            external_id="r3", url="/j/r3", title="ML Engineer", company="Initech",
            hash=job_hash(external_id="r3", title="ML Engineer", company="Initech"),
            easy_apply=False, status=JobStatus.REVIEW, location="SF",
            description_text="ML pipeline",
        ),
    ]
    for j in jobs:
        session.add(j)
    session.flush()

    scores = [
        Score(
            job_hash=jobs[0].hash, embedding_similarity=0.65, llm_fit_score=85,
            missing_skills=[], risk_flags=[], decision=Decision.APPLY,
        ),
        Score(
            job_hash=jobs[1].hash, embedding_similarity=0.2, llm_fit_score=40,
            missing_skills=["Java", "Spring", "Hibernate"],
            risk_flags=["wrong stack"], decision=Decision.SKIP,
        ),
        Score(
            job_hash=jobs[2].hash, embedding_similarity=0.45, llm_fit_score=70,
            missing_skills=["PyTorch", "MLOps", "Java"],
            risk_flags=[], decision=Decision.REVIEW,
        ),
    ]
    for s in scores:
        save_score(session, s)
    session.commit()
    return jobs


class TestCountJobsByStatus:
    def test_counts(self) -> None:
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)
        _seed_db(session)

        counts = count_jobs_by_status(session)
        assert counts.get("applied") == 1
        assert counts.get("skipped") == 1
        assert counts.get("review") == 1


class TestTopMissingSkills:
    def test_returns_most_common(self) -> None:
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)
        _seed_db(session)

        top = get_top_missing_skills(session, limit=5)
        skills = [s for s, _ in top]
        # Java appears in both r2 and r3 scores
        assert skills[0] == "Java"

    def test_empty_db(self) -> None:
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)
        assert get_top_missing_skills(session) == []


class TestGenerateReport:
    def test_generates_md_and_json(self, tmp_path: Path) -> None:
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)
        _seed_db(session)

        summary = generate_report(session=session, data_dir=tmp_path, date="2026-03-05")

        assert summary["date"] == "2026-03-05"
        assert summary["total_jobs"] == 3

        # Check files were written
        md_path = tmp_path / "reports" / "2026-03-05.md"
        json_path = tmp_path / "reports" / "2026-03-05.json"
        assert md_path.exists()
        assert json_path.exists()

        # Validate JSON content
        with open(json_path) as f:
            data = json.load(f)
        assert data["total_jobs"] == 3
        assert len(data["jobs"]) == 3

        # Validate Markdown content
        md_content = md_path.read_text()
        assert "Daily Report" in md_content
        assert "Python Dev" in md_content
        assert "Java Dev" in md_content
        assert "ML Engineer" in md_content

    def test_default_date_is_today(self, tmp_path: Path) -> None:
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)

        summary = generate_report(session=session, data_dir=tmp_path)
        # Should use today's date
        assert len(summary["date"]) == 10  # YYYY-MM-DD format

    def test_empty_db_report(self, tmp_path: Path) -> None:
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)

        summary = generate_report(session=session, data_dir=tmp_path, date="2026-03-05")
        assert summary["total_jobs"] == 0
        assert summary["jobs"] == []

    def test_report_includes_scores(self, tmp_path: Path) -> None:
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)
        _seed_db(session)

        summary = generate_report(session=session, data_dir=tmp_path, date="2026-03-05")
        applied_job = next(j for j in summary["jobs"] if j["title"] == "Python Dev")
        assert applied_job["fit_score"] == 85
        assert applied_job["similarity"] == 0.65

    def test_markdown_has_table(self, tmp_path: Path) -> None:
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)
        _seed_db(session)

        generate_report(session=session, data_dir=tmp_path, date="2026-03-05")
        md = (tmp_path / "reports" / "2026-03-05.md").read_text()
        assert "| Title |" in md
        assert "| Python Dev |" in md


class TestReportWithMarketData:
    """Reports should include market intelligence when market data exists."""

    def _seed_market(self, session):
        """Run the full market pipeline on seeded jobs."""
        from job_hunter.market.events import ingest_jobs
        from job_hunter.market.extract import HeuristicExtractor, run_extraction
        from job_hunter.market.graph.builder import build_graph
        from job_hunter.market.trends.compute import compute_trends
        from job_hunter.market.role_model import build_role_archetypes

        ingest_jobs(session)
        session.commit()
        run_extraction(session, HeuristicExtractor())
        session.commit()
        build_graph(session)
        session.commit()
        compute_trends(session)
        session.commit()
        build_role_archetypes(session, min_group_size=1)
        session.commit()

    def test_report_includes_market_section(self, tmp_path: Path) -> None:
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)
        _seed_db(session)
        self._seed_market(session)

        summary = generate_report(session=session, data_dir=tmp_path, date="2026-03-06")
        assert "market" in summary
        assert summary["market"]["entity_count"] > 0
        assert len(summary["market"]["top_skills"]) > 0

    def test_report_market_section_in_markdown(self, tmp_path: Path) -> None:
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)
        _seed_db(session)
        self._seed_market(session)

        generate_report(session=session, data_dir=tmp_path, date="2026-03-06")
        md = (tmp_path / "reports" / "2026-03-06.md").read_text()
        assert "Market Intelligence" in md
        assert "Top Skills" in md

    def test_report_market_section_in_json(self, tmp_path: Path) -> None:
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)
        _seed_db(session)
        self._seed_market(session)

        generate_report(session=session, data_dir=tmp_path, date="2026-03-06")
        data = json.loads((tmp_path / "reports" / "2026-03-06.json").read_text())
        assert "market" in data
        assert data["market"]["entity_count"] > 0

    def test_report_without_market_data(self, tmp_path: Path) -> None:
        """Report without market pipeline run should not have market section."""
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)
        _seed_db(session)

        summary = generate_report(session=session, data_dir=tmp_path, date="2026-03-07")
        assert "market" not in summary

        md = (tmp_path / "reports" / "2026-03-07.md").read_text()
        assert "Market Intelligence" not in md


