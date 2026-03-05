"""Tests for matching/scoring logic."""

from __future__ import annotations

from job_hunter.matching.embeddings import FakeEmbedder
from job_hunter.matching.llm_eval import FakeLLMEvaluator
from job_hunter.matching.scoring import compute_score, should_apply


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

