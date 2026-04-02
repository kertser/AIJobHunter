"""LLM-based job evaluation returning structured scoring."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("job_hunter.matching.llm_eval")

# ---------------------------------------------------------------------------
# System prompt for job evaluation
# ---------------------------------------------------------------------------

EVAL_SYSTEM_PROMPT = """\
You are a job-fit evaluator. You will receive a candidate's resume and a job
description. Analyse how well the candidate matches the job.

The candidate may also provide industry preferences:
- Preferred industries: industries they want to work in (boost score)
- Disliked industries: industries they want to avoid (penalise score, add risk flag)

IMPORTANT industry matching rules:
- Treat related terms as equivalent (e.g. "medical" = "healthcare", "tech" = "technology")
- ONLY flag "disliked industry" if the job's industry clearly matches one of the EXPLICITLY listed disliked industries
- NEVER flag an industry as disliked if it matches or is closely related to a PREFERRED industry
- When in doubt, do NOT add "disliked industry" — prefer false negatives over false positives

Return ONLY a JSON object with these exact keys:

{
  "fit_score": <int 0-100>,
  "missing_skills": [<list of skills the candidate lacks for this role>],
  "risk_flags": [<list of potential concerns, e.g. "relocation required", "overqualified", "underqualified", "disliked industry">],
  "decision": "<apply|skip|review>"
}

Scoring guidelines:
- 85-100: Excellent match — candidate meets nearly all requirements
- 70-84:  Good match — candidate meets most requirements, minor gaps
- 50-69:  Partial match — significant skill gaps but transferable experience
- 0-49:   Poor match — major misalignment
- If the job is in a DISLIKED industry (explicitly listed), reduce the score by 15-25 points and add "disliked industry" to risk_flags
- If the job is in a PREFERRED industry (or a closely related field), add 5-10 bonus points (capped at 100)

Decision guidelines:
- "apply": fit_score >= 70 and no critical risk flags
- "skip": fit_score < 50 or critical risk flags (e.g. wrong country, wrong seniority)
- "review": everything else — needs human judgment

Return ONLY valid JSON. No markdown, no commentary.
"""


# ---------------------------------------------------------------------------
# Base class + implementations
# ---------------------------------------------------------------------------

class LLMEvaluator:
    """Base interface for LLM evaluation providers."""

    def evaluate(self, resume: str, job_description: str, user_preferences: dict[str, Any] | None = None) -> dict[str, Any]:
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


class OpenAILLMEvaluator(LLMEvaluator):
    """Evaluate job fit using the OpenAI Chat Completions API."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        *,
        base_url: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens

    def evaluate(self, resume: str, job_description: str, user_preferences: dict[str, Any] | None = None) -> dict[str, Any]:
        from openai import OpenAI
        from job_hunter.llm_client import safe_json_parse

        kwargs: dict = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url

        client = OpenAI(**kwargs)

        user_message = (
            f"=== RESUME ===\n{resume[:4000]}\n\n"
            f"=== JOB DESCRIPTION ===\n{job_description[:4000]}"
        )

        if user_preferences:
            pref = user_preferences
            pref_lines = []
            if pref.get("preferred_industries"):
                pref_lines.append(f"Preferred industries (BOOST score, NEVER flag as disliked): {', '.join(pref['preferred_industries'])}")
            if pref.get("disliked_industries"):
                pref_lines.append(f"Disliked industries (ONLY these should be penalised): {', '.join(pref['disliked_industries'])}")
            else:
                pref_lines.append("Disliked industries: NONE — do not add 'disliked industry' to risk_flags")
            if pref_lines:
                user_message += f"\n\n=== CANDIDATE PREFERENCES ===\n" + "\n".join(pref_lines)

        logger.info("Requesting LLM job evaluation via %s", self.model)

        # Local models may not support response_format=json_object reliably
        create_kwargs: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": EVAL_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "temperature": self.temperature if self.temperature is not None else 0.2,
        }
        if self.max_tokens:
            create_kwargs["max_tokens"] = self.max_tokens
        if not self.base_url:
            create_kwargs["response_format"] = {"type": "json_object"}

        response = client.chat.completions.create(**create_kwargs)

        raw = response.choices[0].message.content
        if raw is None:
            raise RuntimeError("LLM returned an empty response")

        logger.debug("Raw LLM eval response:\n%s", raw)
        data: dict[str, Any] = safe_json_parse(raw)

        # Validate and normalise
        data.setdefault("fit_score", 0)
        data.setdefault("missing_skills", [])
        data.setdefault("risk_flags", [])
        data.setdefault("decision", "review")

        # Clamp fit_score
        data["fit_score"] = max(0, min(100, int(data["fit_score"])))

        # Normalise decision
        if data["decision"] not in ("apply", "skip", "review"):
            data["decision"] = "review"

        return data


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

    def evaluate(self, resume: str, job_description: str, user_preferences: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "fit_score": self._fit_score,
            "missing_skills": self._missing_skills,
            "risk_flags": self._risk_flags,
            "decision": self._decision,
        }

