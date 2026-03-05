"""LLM-based job evaluation returning structured scoring."""

from __future__ import annotations

from typing import Any


class LLMEvaluator:
    """Base interface for LLM evaluation providers."""

    def evaluate(self, resume: str, job_description: str) -> dict[str, Any]:
        """Return structured evaluation.

        Expected schema::

            {
                "fit_score": 0-100,
                "missing_skills": [],
                "risk_flags": [],
                "decision": "apply|skip|review"
            }
        """
        raise NotImplementedError


class FakeLLMEvaluator(LLMEvaluator):
    """Deterministic evaluator for testing."""

    def __init__(
        self,
        fit_score: int = 80,
        missing_skills: list[str] | None = None,
        risk_flags: list[str] | None = None,
        decision: str = "apply",
    ) -> None:
        self._fit_score = fit_score
        self._missing_skills = missing_skills or []
        self._risk_flags = risk_flags or []
        self._decision = decision

    def evaluate(self, resume: str, job_description: str) -> dict[str, Any]:
        return {
            "fit_score": self._fit_score,
            "missing_skills": self._missing_skills,
            "risk_flags": self._risk_flags,
            "decision": self._decision,
        }

