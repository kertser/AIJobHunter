"""Scoring logic — combine embedding similarity + LLM evaluation into a decision."""

from __future__ import annotations

from job_hunter.matching.embeddings import Embedder
from job_hunter.matching.llm_eval import LLMEvaluator


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

