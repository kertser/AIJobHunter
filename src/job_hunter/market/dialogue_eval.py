"""Dialogue evaluation hooks — evaluator providers and prompt templates.

Provides a base class for dialogue evaluators, a rule-based implementation
that scores turns using keyword matching, and prompt templates for generating
uncertainty-reduction questions.

Provider pattern: ``DialogueEvaluator`` (base) → ``RuleBasedDialogueEvaluator``
(heuristic) → ``FakeDialogueEvaluator`` (tests).
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from job_hunter.market.db_models import AssessmentType

logger = logging.getLogger("job_hunter.market.dialogue_eval")


# ---------------------------------------------------------------------------
# Prompt templates for uncertainty-reduction questions
# ---------------------------------------------------------------------------

UNCERTAINTY_PROMPT_TEMPLATES: dict[AssessmentType, str] = {
    AssessmentType.PROBLEM_DECOMPOSITION: (
        "When faced with the following problem: '{context}', "
        "how would you break it down into smaller, manageable sub-problems? "
        "Walk me through your approach step by step."
    ),
    AssessmentType.LEARNING_VELOCITY: (
        "Describe a recent technology or skill you learned from scratch. "
        "How long did it take to become productive? "
        "What strategies did you use to accelerate your learning of '{context}'?"
    ),
    AssessmentType.AMBIGUITY_TOLERANCE: (
        "Imagine you receive a vague requirement: '{context}'. "
        "What questions would you ask to clarify it? "
        "How do you decide when you have enough information to start working?"
    ),
    AssessmentType.ADAPTATION_SPEED: (
        "Tell me about a time when project requirements changed significantly "
        "mid-stream, particularly around '{context}'. "
        "How did you adapt, and what was the outcome?"
    ),
    AssessmentType.REASONING_CONSISTENCY: (
        "Given two conflicting pieces of evidence about '{context}', "
        "how would you decide which to trust? "
        "What framework do you use for making decisions under uncertainty?"
    ),
}


def generate_probing_questions(
    capabilities: list[dict[str, Any]],
    existing_turns: list[dict[str, Any]] | None = None,
    low_confidence_threshold: float = 0.5,
    max_questions: int = 5,
) -> list[dict[str, Any]]:
    """Generate uncertainty-reduction questions for low-confidence capabilities.

    Picks capabilities below *low_confidence_threshold* and generates
    targeted follow-up questions using the prompt templates.

    Parameters
    ----------
    capabilities:
        List of capability dicts (from ``get_candidate_capabilities``).
    existing_turns:
        Optional list of existing dialogue turn dicts to avoid repetition.
    low_confidence_threshold:
        Capabilities below this confidence get probing questions.
    max_questions:
        Maximum number of questions to return.

    Returns
    -------
    List of dicts with keys: ``capability``, ``entity_type``,
    ``confidence``, ``assessment_type``, ``question``.
    """
    # Gather already-discussed topics to avoid repetition
    discussed: set[str] = set()
    if existing_turns:
        for turn in existing_turns:
            text = (turn.get("response_text") or turn.get("prompt_text") or "").lower()
            discussed.update(text.split())

    # Find low-confidence capabilities
    low_conf = sorted(
        [c for c in capabilities if c.get("confidence", 1.0) < low_confidence_threshold],
        key=lambda c: c.get("confidence", 1.0),
    )

    questions: list[dict[str, Any]] = []
    used_types: set[AssessmentType] = set()

    for cap in low_conf:
        if len(questions) >= max_questions:
            break

        name = cap.get("display_name", "")
        if not name:
            continue

        # Pick an assessment type not yet used
        for atype in AssessmentType:
            if atype in used_types:
                continue

            template = UNCERTAINTY_PROMPT_TEMPLATES.get(atype, "")
            if not template:
                continue

            question = template.format(context=name)
            questions.append({
                "capability": name,
                "entity_type": cap.get("entity_type", "skill"),
                "confidence": cap.get("confidence", 0.0),
                "assessment_type": atype.value,
                "question": question,
            })
            used_types.add(atype)
            break

    return questions


# ---------------------------------------------------------------------------
# Base evaluator
# ---------------------------------------------------------------------------

class DialogueEvaluator:
    """Base class for dialogue evaluators.

    Subclass and implement :meth:`evaluate` to provide custom evaluation
    logic for dialogue sessions.
    """

    version: str = "base-0.0"

    def evaluate(
        self,
        turns: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Evaluate dialogue turns and produce assessment results.

        Parameters
        ----------
        turns:
            Sequence of turn dicts with keys ``speaker``, ``turn_index``,
            ``prompt_text``, ``response_text``.

        Returns
        -------
        List of assessment dicts with keys: ``assessment_type`` (str),
        ``score`` (float 0–1), ``confidence`` (float 0–1),
        ``evidence_span`` (str).
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Rule-based evaluator
# ---------------------------------------------------------------------------

class RuleBasedDialogueEvaluator(DialogueEvaluator):
    """Heuristic evaluator that scores dialogue turns via keyword analysis.

    Produces assessments by analysing response text for:
    - Technical depth (skill/tool mentions → problem_decomposition)
    - Breadth of knowledge (distinct entities → learning_velocity)
    - Hedging language (ambiguity markers → ambiguity_tolerance)
    - Adaptation keywords (change/pivot/refactor → adaptation_speed)
    - Reasoning markers (because/therefore/however → reasoning_consistency)
    """

    version: str = "rule-based-1.0"

    # Keyword sets for each assessment dimension
    _DEPTH_KEYWORDS: set[str] = {
        "architecture", "design pattern", "trade-off", "scalab",
        "optimiz", "benchmark", "complex", "distribut", "algorithm",
        "performance", "latency", "throughput",
    }
    _ADAPTATION_KEYWORDS: set[str] = {
        "pivot", "refactor", "migrat", "adapt", "chang",
        "rewrit", "transition", "evolv", "iterati",
    }
    _REASONING_MARKERS: set[str] = {
        "because", "therefore", "however", "consequently",
        "on the other hand", "in contrast", "as a result",
        "evidence suggests", "trade-off",
    }
    _HEDGING_MARKERS: set[str] = {
        "it depends", "not sure", "might", "possibly",
        "unclear", "need more info", "ambiguous", "context",
    }

    def evaluate(
        self,
        turns: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not turns:
            return []

        # Collect all response text
        all_responses = " ".join(
            (t.get("response_text") or "").lower() for t in turns
        )
        word_count = max(len(all_responses.split()), 1)

        # Import skill/tool vocabularies for breadth scoring
        from job_hunter.market.extract import _SKILLS, _TOOLS
        mentioned_entities = set()
        for kw in _SKILLS | _TOOLS:
            if kw in all_responses:
                mentioned_entities.add(kw)

        assessments: list[dict[str, Any]] = []

        # Problem decomposition — technical depth
        depth_hits = sum(1 for k in self._DEPTH_KEYWORDS if k in all_responses)
        depth_score = min(depth_hits / max(len(self._DEPTH_KEYWORDS) * 0.3, 1), 1.0)
        assessments.append({
            "assessment_type": AssessmentType.PROBLEM_DECOMPOSITION.value,
            "score": round(depth_score, 3),
            "confidence": min(0.3 + 0.05 * len(turns), 0.8),
            "evidence_span": f"{depth_hits} depth keywords in {word_count} words",
        })

        # Learning velocity — breadth of entities mentioned
        breadth_score = min(len(mentioned_entities) / 10.0, 1.0)
        assessments.append({
            "assessment_type": AssessmentType.LEARNING_VELOCITY.value,
            "score": round(breadth_score, 3),
            "confidence": min(0.3 + 0.05 * len(turns), 0.8),
            "evidence_span": f"{len(mentioned_entities)} distinct entities mentioned",
        })

        # Ambiguity tolerance — hedging language (positive signal)
        hedge_hits = sum(1 for k in self._HEDGING_MARKERS if k in all_responses)
        ambiguity_score = min(hedge_hits / max(len(self._HEDGING_MARKERS) * 0.3, 1), 1.0)
        assessments.append({
            "assessment_type": AssessmentType.AMBIGUITY_TOLERANCE.value,
            "score": round(ambiguity_score, 3),
            "confidence": min(0.2 + 0.05 * len(turns), 0.7),
            "evidence_span": f"{hedge_hits} hedging markers detected",
        })

        # Adaptation speed — change-related keywords
        adapt_hits = sum(1 for k in self._ADAPTATION_KEYWORDS if k in all_responses)
        adapt_score = min(adapt_hits / max(len(self._ADAPTATION_KEYWORDS) * 0.3, 1), 1.0)
        assessments.append({
            "assessment_type": AssessmentType.ADAPTATION_SPEED.value,
            "score": round(adapt_score, 3),
            "confidence": min(0.2 + 0.05 * len(turns), 0.7),
            "evidence_span": f"{adapt_hits} adaptation keywords detected",
        })

        # Reasoning consistency — logical connectors
        reason_hits = sum(1 for k in self._REASONING_MARKERS if k in all_responses)
        reason_score = min(reason_hits / max(len(self._REASONING_MARKERS) * 0.3, 1), 1.0)
        assessments.append({
            "assessment_type": AssessmentType.REASONING_CONSISTENCY.value,
            "score": round(reason_score, 3),
            "confidence": min(0.3 + 0.05 * len(turns), 0.8),
            "evidence_span": f"{reason_hits} reasoning markers detected",
        })

        return assessments


# ---------------------------------------------------------------------------
# Fake evaluator (tests)
# ---------------------------------------------------------------------------

class FakeDialogueEvaluator(DialogueEvaluator):
    """Deterministic evaluator for offline tests."""

    version: str = "fake-1.0"

    def __init__(
        self,
        default_score: float = 0.6,
        default_confidence: float = 0.5,
    ) -> None:
        self.default_score = default_score
        self.default_confidence = default_confidence

    def evaluate(
        self,
        turns: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            {
                "assessment_type": atype.value,
                "score": self.default_score,
                "confidence": self.default_confidence,
                "evidence_span": f"fake evaluation of {len(turns)} turn(s)",
            }
            for atype in AssessmentType
        ]

