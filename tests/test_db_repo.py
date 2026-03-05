"""Tests for database initialisation and CRUD helpers."""

from __future__ import annotations

from job_hunter.db.models import Base, Job, JobStatus, Score, ApplicationAttempt, Decision, ApplicationResult
from job_hunter.db.repo import get_memory_engine, init_db, make_session, upsert_job, get_jobs_by_status, save_score, save_attempt
from job_hunter.utils.hashing import job_hash


class TestInitDb:
    def test_creates_all_tables(self) -> None:
        engine = get_memory_engine()
        init_db(engine)
        table_names = Base.metadata.tables.keys()
        assert "jobs" in table_names
        assert "scores" in table_names
        assert "application_attempts" in table_names

    def test_idempotent_init(self) -> None:
        engine = get_memory_engine()
        init_db(engine)
        init_db(engine)  # should not raise


class TestUpsertJob:
    def _make_job(self, **overrides) -> Job:
        defaults = dict(
            external_id="ext-001",
            url="https://linkedin.com/jobs/view/ext-001",
            title="Python Developer",
            company="Acme Corp",
            location="Remote",
            hash=job_hash(external_id="ext-001", title="Python Developer", company="Acme Corp"),
        )
        defaults.update(overrides)
        return Job(**defaults)

    def test_insert_new_job(self) -> None:
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)
        job = self._make_job()
        result = upsert_job(session, job)
        session.commit()
        assert result.id is not None
        assert result.title == "Python Developer"

    def test_upsert_updates_existing(self) -> None:
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)

        job1 = self._make_job()
        upsert_job(session, job1)
        session.commit()

        job2 = self._make_job(title="Senior Python Developer")
        updated = upsert_job(session, job2)
        session.commit()

        assert updated.title == "Senior Python Developer"
        # Should still be just one row
        all_jobs = get_jobs_by_status(session, JobStatus.NEW)
        assert len(all_jobs) == 1


class TestGetJobsByStatus:
    def test_filters_by_status(self) -> None:
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)

        j1 = Job(
            external_id="e1", url="u1", title="T1", company="C1",
            hash=job_hash(external_id="e1", title="T1", company="C1"),
            status=JobStatus.NEW,
        )
        j2 = Job(
            external_id="e2", url="u2", title="T2", company="C2",
            hash=job_hash(external_id="e2", title="T2", company="C2"),
            status=JobStatus.SCORED,
        )
        session.add_all([j1, j2])
        session.commit()

        new_jobs = get_jobs_by_status(session, JobStatus.NEW)
        assert len(new_jobs) == 1
        assert new_jobs[0].external_id == "e1"

        scored_jobs = get_jobs_by_status(session, JobStatus.SCORED)
        assert len(scored_jobs) == 1
        assert scored_jobs[0].external_id == "e2"


class TestSaveScore:
    def test_save_and_read_back(self) -> None:
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)

        score = Score(
            job_hash="abc123",
            resume_id="default",
            embedding_similarity=0.42,
            llm_fit_score=85,
            missing_skills=["Kubernetes"],
            risk_flags=[],
            decision=Decision.APPLY,
        )
        saved = save_score(session, score)
        session.commit()
        assert saved.id is not None
        assert saved.llm_fit_score == 85


class TestSaveAttempt:
    def test_save_and_read_back(self) -> None:
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)

        attempt = ApplicationAttempt(
            job_hash="abc123",
            result=ApplicationResult.DRY_RUN,
            form_answers_json={"years_experience": "8"},
        )
        saved = save_attempt(session, attempt)
        session.commit()
        assert saved.id is not None
        assert saved.result == ApplicationResult.DRY_RUN

