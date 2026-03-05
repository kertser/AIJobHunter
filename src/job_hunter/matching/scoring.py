"""Scoring logic — combine embedding similarity + LLM evaluation into a decision."""

from __future__ import annotations

import logging

from job_hunter.db.models import Decision, JobStatus
from job_hunter.matching.embeddings import Embedder
from job_hunter.matching.llm_eval import LLMEvaluator

logger = logging.getLogger("job_hunter.matching.scoring")

# Map LLM decision string → DB enum values
_DECISION_MAP: dict[str, Decision] = {
    "apply": Decision.APPLY,
    "skip": Decision.SKIP,
    "review": Decision.REVIEW,
}


def compute_score(
    *,
    resume_text: str,
    job_description: str,
    embedder: Embedder,
    llm_evaluator: LLMEvaluator,
) -> dict:
    """Compute a combined score for a job against a resume.

    Returns a dict matching the Score entity fields:
    embedding_similarity, llm_fit_score, missing_skills, risk_flags, decision.
    """
    resume_emb = embedder.embed(resume_text)
    job_emb = embedder.embed(job_description)
    similarity = embedder.similarity(resume_emb, job_emb)

    llm_result = llm_evaluator.evaluate(resume_text, job_description)

    return {
        "embedding_similarity": similarity,
        "llm_fit_score": llm_result["fit_score"],
        "missing_skills": llm_result["missing_skills"],
        "risk_flags": llm_result["risk_flags"],
        "decision": llm_result["decision"],
    }


def should_apply(
    *,
    easy_apply: bool,
    fit_score: int,
    similarity: float,
    min_fit_score: int = 75,
    min_similarity: float = 0.35,
) -> bool:
    """Decide whether to auto-apply based on thresholds."""
    return easy_apply and fit_score >= min_fit_score and similarity >= min_similarity


def decision_to_db(decision_str: str) -> Decision:
    """Convert a raw decision string to the DB Decision enum."""
    return _DECISION_MAP.get(decision_str, Decision.REVIEW)


def decide_job_status(
    *,
    easy_apply: bool,
    fit_score: int,
    similarity: float,
    decision_str: str,
    min_fit_score: int = 75,
    min_similarity: float = 0.35,
) -> JobStatus:
    """Determine the next Job status after scoring.

    Returns:
    - QUEUED  if the job should be auto-applied
    - SKIPPED if the LLM said skip or thresholds aren't met
    - REVIEW  if the LLM said review or it's borderline
    - SCORED  as a fallback (shouldn't normally happen)
    """
    if decision_str == "skip":
        return JobStatus.SKIPPED

    if decision_str == "review":
        return JobStatus.REVIEW

    # decision == "apply" — check thresholds
    if should_apply(
        easy_apply=easy_apply,
        fit_score=fit_score,
        similarity=similarity,
        min_fit_score=min_fit_score,
        min_similarity=min_similarity,
    ):
        return JobStatus.QUEUED

    # LLM said apply but thresholds not met
    return JobStatus.REVIEW


