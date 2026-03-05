"""Tests for matching/scoring logic."""

from __future__ import annotations

import pytest

from job_hunter.db.models import Decision, Job, JobStatus, Score
from job_hunter.db.repo import get_memory_engine, init_db, make_session, save_score, upsert_job, get_jobs_by_status
from job_hunter.matching.embeddings import FakeEmbedder, cosine_similarity
from job_hunter.matching.llm_eval import FakeLLMEvaluator
from job_hunter.matching.scoring import compute_score, decide_job_status, decision_to_db, should_apply
from job_hunter.utils.hashing import job_hash


# ---------------------------------------------------------------------------
# cosine_similarity
# ---------------------------------------------------------------------------

class TestCosineSimilarity:
    def test_identical_vectors(self) -> None:
        v = [1.0, 2.0, 3.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self) -> None:
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self) -> None:
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_zero_vector_returns_zero(self) -> None:
        assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0

    def test_mismatched_lengths_raises(self) -> None:
        with pytest.raises(ValueError, match="length mismatch"):
            cosine_similarity([1.0], [1.0, 2.0])


# ---------------------------------------------------------------------------
# compute_score
# ---------------------------------------------------------------------------

class TestComputeScore:
    def test_returns_expected_keys(self) -> None:
        result = compute_score(
            resume_text="python developer",
            job_description="senior python developer",
            embedder=FakeEmbedder(fixed_similarity=0.6),
            llm_evaluator=FakeLLMEvaluator(fit_score=85, decision="apply"),
        )
        assert set(result.keys()) == {
            "embedding_similarity",
            "llm_fit_score",
            "missing_skills",
            "risk_flags",
            "decision",
        }
        assert result["embedding_similarity"] == 0.6
        assert result["llm_fit_score"] == 85
        assert result["decision"] == "apply"

    def test_skip_decision(self) -> None:
        result = compute_score(
            resume_text="python developer",
            job_description="java developer",
            embedder=FakeEmbedder(fixed_similarity=0.1),
            llm_evaluator=FakeLLMEvaluator(fit_score=30, decision="skip",
                                           missing_skills=["Java", "Spring"]),
        )
        assert result["decision"] == "skip"
        assert result["llm_fit_score"] == 30
        assert "Java" in result["missing_skills"]


# ---------------------------------------------------------------------------
# should_apply
# ---------------------------------------------------------------------------

class TestShouldApply:
    def test_apply_when_all_thresholds_met(self) -> None:
        assert should_apply(easy_apply=True, fit_score=80, similarity=0.5) is True

    def test_skip_when_no_easy_apply(self) -> None:
        assert should_apply(easy_apply=False, fit_score=90, similarity=0.9) is False

    def test_skip_when_score_too_low(self) -> None:
        assert should_apply(easy_apply=True, fit_score=50, similarity=0.5) is False

    def test_skip_when_similarity_too_low(self) -> None:
        assert should_apply(easy_apply=True, fit_score=80, similarity=0.1) is False

    def test_custom_thresholds(self) -> None:
        assert should_apply(
            easy_apply=True, fit_score=60, similarity=0.3,
            min_fit_score=60, min_similarity=0.3,
        ) is True

    def test_boundary_values(self) -> None:
        # Exactly at thresholds should pass
        assert should_apply(easy_apply=True, fit_score=75, similarity=0.35) is True
        # Just below should fail
        assert should_apply(easy_apply=True, fit_score=74, similarity=0.35) is False
        assert should_apply(easy_apply=True, fit_score=75, similarity=0.34) is False


# ---------------------------------------------------------------------------
# decision_to_db
# ---------------------------------------------------------------------------

class TestDecisionToDb:
    def test_apply(self) -> None:
        assert decision_to_db("apply") == Decision.APPLY

    def test_skip(self) -> None:
        assert decision_to_db("skip") == Decision.SKIP

    def test_review(self) -> None:
        assert decision_to_db("review") == Decision.REVIEW

    def test_unknown_defaults_to_review(self) -> None:
        assert decision_to_db("unknown") == Decision.REVIEW


# ---------------------------------------------------------------------------
# decide_job_status
# ---------------------------------------------------------------------------

class TestDecideJobStatus:
    def test_skip_decision_returns_skipped(self) -> None:
        status = decide_job_status(
            easy_apply=True, fit_score=90, similarity=0.9,
            decision_str="skip",
        )
        assert status == JobStatus.SKIPPED

    def test_review_decision_returns_review(self) -> None:
        status = decide_job_status(
            easy_apply=True, fit_score=80, similarity=0.5,
            decision_str="review",
        )
        assert status == JobStatus.REVIEW

    def test_apply_with_thresholds_met_returns_queued(self) -> None:
        status = decide_job_status(
            easy_apply=True, fit_score=80, similarity=0.5,
            decision_str="apply",
        )
        assert status == JobStatus.QUEUED

    def test_apply_without_easy_apply_returns_review(self) -> None:
        status = decide_job_status(
            easy_apply=False, fit_score=80, similarity=0.5,
            decision_str="apply",
        )
        assert status == JobStatus.REVIEW

    def test_apply_below_fit_threshold_returns_review(self) -> None:
        status = decide_job_status(
            easy_apply=True, fit_score=50, similarity=0.5,
            decision_str="apply",
        )
        assert status == JobStatus.REVIEW

    def test_apply_below_similarity_threshold_returns_review(self) -> None:
        status = decide_job_status(
            easy_apply=True, fit_score=80, similarity=0.1,
            decision_str="apply",
        )
        assert status == JobStatus.REVIEW

    def test_custom_thresholds(self) -> None:
        status = decide_job_status(
            easy_apply=True, fit_score=60, similarity=0.3,
            decision_str="apply",
            min_fit_score=60, min_similarity=0.3,
        )
        assert status == JobStatus.QUEUED


# ---------------------------------------------------------------------------
# Integration: full scoring pipeline with fake providers + DB
# ---------------------------------------------------------------------------

class TestScoringPipelineIntegration:
    def _setup_db_with_jobs(self):
        """Create an in-memory DB with 3 mock jobs."""
        engine = get_memory_engine()
        init_db(engine)
        session = make_session(engine)

        jobs = [
            Job(
                external_id="e1", url="u1", title="Python Dev", company="Co1",
                hash=job_hash(external_id="e1", title="Python Dev", company="Co1"),
                description_text="Python backend role, 5+ years experience",
                easy_apply=True, status=JobStatus.NEW,
            ),
            Job(
                external_id="e2", url="u2", title="Java Dev", company="Co2",
                hash=job_hash(external_id="e2", title="Java Dev", company="Co2"),
                description_text="Java enterprise developer",
                easy_apply=True, status=JobStatus.NEW,
            ),
            Job(
                external_id="e3", url="u3", title="Data Analyst", company="Co3",
                hash=job_hash(external_id="e3", title="Data Analyst", company="Co3"),
                description_text="SQL and dashboarding",
                easy_apply=False, status=JobStatus.NEW,
            ),
        ]
        for j in jobs:
            session.add(j)
        session.commit()
        return engine, session

    def test_score_and_queue_jobs(self) -> None:
        engine, session = self._setup_db_with_jobs()

        new_jobs = get_jobs_by_status(session, JobStatus.NEW)
        assert len(new_jobs) == 3

        embedder = FakeEmbedder(fixed_similarity=0.5)
        evaluator = FakeLLMEvaluator(fit_score=80, decision="apply")

        for job in new_jobs:
            result = compute_score(
                resume_text="python developer resume",
                job_description=job.description_text,
                embedder=embedder,
                llm_evaluator=evaluator,
            )
            score_row = Score(
                job_hash=job.hash,
                embedding_similarity=result["embedding_similarity"],
                llm_fit_score=result["llm_fit_score"],
                missing_skills=result["missing_skills"],
                risk_flags=result["risk_flags"],
                decision=decision_to_db(result["decision"]),
            )
            save_score(session, score_row)

            new_status = decide_job_status(
                easy_apply=job.easy_apply,
                fit_score=result["llm_fit_score"],
                similarity=result["embedding_similarity"],
                decision_str=result["decision"],
            )
            job.status = new_status

        session.commit()

        # No more NEW jobs
        assert len(get_jobs_by_status(session, JobStatus.NEW)) == 0

        # e1 and e2 have easy_apply=True → QUEUED
        queued = get_jobs_by_status(session, JobStatus.QUEUED)
        assert len(queued) == 2
        assert {j.external_id for j in queued} == {"e1", "e2"}

        # e3 has easy_apply=False → REVIEW (LLM said apply but threshold not met)
        review = get_jobs_by_status(session, JobStatus.REVIEW)
        assert len(review) == 1
        assert review[0].external_id == "e3"

    def test_skip_decision_sets_skipped(self) -> None:
        engine, session = self._setup_db_with_jobs()
        new_jobs = get_jobs_by_status(session, JobStatus.NEW)

        evaluator = FakeLLMEvaluator(fit_score=30, decision="skip")
        embedder = FakeEmbedder(fixed_similarity=0.1)

        for job in new_jobs:
            result = compute_score(
                resume_text="resume",
                job_description=job.description_text,
                embedder=embedder,
                llm_evaluator=evaluator,
            )
            job.status = decide_job_status(
                easy_apply=job.easy_apply,
                fit_score=result["llm_fit_score"],
                similarity=result["embedding_similarity"],
                decision_str=result["decision"],
            )

        session.commit()

        skipped = get_jobs_by_status(session, JobStatus.SKIPPED)
        assert len(skipped) == 3


