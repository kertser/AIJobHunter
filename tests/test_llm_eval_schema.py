"""Tests for LLM evaluator schema contract."""

from __future__ import annotations

import pytest

from job_hunter.matching.llm_eval import FakeLLMEvaluator, LLMEvaluator


class TestFakeLLMEvaluatorSchema:
    def test_returns_required_keys(self) -> None:
        evaluator = FakeLLMEvaluator()
        result = evaluator.evaluate("resume", "job desc")
        required = {"fit_score", "missing_skills", "risk_flags", "decision"}
        assert required.issubset(result.keys())

    def test_fit_score_range(self) -> None:
        evaluator = FakeLLMEvaluator(fit_score=42)
        result = evaluator.evaluate("r", "j")
        assert 0 <= result["fit_score"] <= 100

    def test_decision_valid_values(self) -> None:
        for decision in ("apply", "skip", "review"):
            evaluator = FakeLLMEvaluator(decision=decision)
            result = evaluator.evaluate("r", "j")
            assert result["decision"] in ("apply", "skip", "review")

    def test_missing_skills_is_list(self) -> None:
        evaluator = FakeLLMEvaluator(missing_skills=["Go", "Rust"])
        result = evaluator.evaluate("r", "j")
        assert isinstance(result["missing_skills"], list)

    def test_risk_flags_is_list(self) -> None:
        evaluator = FakeLLMEvaluator(risk_flags=["relocation required"])
        result = evaluator.evaluate("r", "j")
        assert isinstance(result["risk_flags"], list)


class TestBaseLLMEvaluator:
    def test_base_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError):
            LLMEvaluator().evaluate("r", "j")

