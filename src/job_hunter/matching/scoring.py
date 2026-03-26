"""Scoring logic — combine embedding similarity + LLM evaluation into a decision."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

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
    user_preferences: dict | None = None,
) -> dict:
    """Compute a combined score for a job against a resume.

    Returns a dict matching the Score entity fields:
    embedding_similarity, llm_fit_score, missing_skills, risk_flags, decision.
    """
    resume_emb = embedder.embed(resume_text)
    job_emb = embedder.embed(job_description)
    similarity = embedder.similarity(resume_emb, job_emb)

    llm_result = llm_evaluator.evaluate(resume_text, job_description, user_preferences=user_preferences)

    return {
        "embedding_similarity": similarity,
        "llm_fit_score": llm_result["fit_score"],
        "missing_skills": llm_result["missing_skills"],
        "risk_flags": llm_result["risk_flags"],
        "decision": llm_result["decision"],
    }


def compute_market_boost(
    session: Session,
    *,
    job_title: str,
    candidate_key: str = "default",
) -> dict[str, Any]:
    """Compute a market-intelligence boost for a specific job.

    Looks up the best-matching role archetype for *job_title* and returns
    opportunity signals that can enrich a traditional score.

    Returns a dict with:
      - ``market_score``: 0.0–1.0 (0.0 when no market data)
      - ``trend_boost``: float  (positive = hot market)
      - ``role_key``: matched role archetype or ``""``
      - ``hard_gaps``: list of critical missing skills
      - ``learnable_gaps``: list of skills that could be learned

    Safe to call when market tables are empty — returns neutral values.
    """
    neutral: dict[str, Any] = {
        "market_score": 0.0,
        "trend_boost": 0.0,
        "role_key": "",
        "hard_gaps": [],
        "learnable_gaps": [],
    }

    try:
        from job_hunter.market.normalize import canonicalize
        from job_hunter.market.repo import get_match_explanations
        from job_hunter.market.opportunity import score_opportunities
    except Exception:
        return neutral

    explanations = get_match_explanations(session, candidate_key)
    if not explanations:
        return neutral

    # Find the role archetype whose key best matches the job title
    normalised_title = canonicalize(job_title)
    best_expl = None
    best_overlap = 0.0

    for expl in explanations:
        role_tokens = set(expl.role_key.split("_"))
        title_tokens = set(normalised_title.split("_"))
        if not role_tokens or not title_tokens:
            continue
        overlap = len(role_tokens & title_tokens) / max(len(role_tokens | title_tokens), 1)
        if overlap > best_overlap:
            best_overlap = overlap
            best_expl = expl

    if best_expl is None or best_overlap < 0.1:
        return neutral

    # Compute opportunity score for the matched role
    opps = score_opportunities(session, candidate_key)
    opp = next((o for o in opps if o["role_key"] == best_expl.role_key), None)
    if opp is None:
        return neutral

    return {
        "market_score": opp["opportunity_score"],
        "trend_boost": opp["trend_boost"],
        "role_key": opp["role_key"],
        "hard_gaps": opp.get("hard_gaps", []),
        "learnable_gaps": opp.get("learnable_gaps", []),
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


