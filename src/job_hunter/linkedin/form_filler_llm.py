"""LLM-based form field answering for the Easy Apply wizard.

Uses the user profile and job context to intelligently answer arbitrary
form questions via OpenAI.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("job_hunter.linkedin.form_filler_llm")

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

FORM_FILLER_SYSTEM_PROMPT = """\
You are an AI assistant helping a job applicant fill out a job application form.

You will receive:
1. The applicant's profile (name, skills, experience, education, etc.)
2. A list of form fields with their labels, types, and any available options.

For each field, provide the best answer based on the applicant's profile.

Rules:
- For Yes/No questions about skills or experience: answer "Yes" if the applicant
  has relevant experience, "No" otherwise. When in doubt, say "Yes".
- For numeric fields (years of experience, etc.): provide a realistic number
  based on the profile.
- For text fields asking about experience: give a concise, truthful answer.
- For dropdown/select fields: choose the BEST matching option from the
  available options list. Return the exact option text.
- For contact fields (name, email, phone): use the exact values from the profile.
- For radio buttons: return the label text of the best option.
- NEVER fabricate information not supported by the profile.
- Keep answers concise — form fields often have character limits.

Return ONLY a JSON object mapping field labels to answers. Example:
{
  "How many years of Python experience?": "10",
  "Do you have a Master's degree?": "Yes",
  "Preferred work arrangement": "Remote"
}

Return ONLY valid JSON. No markdown, no commentary.
"""


# ---------------------------------------------------------------------------
# LLM form filler
# ---------------------------------------------------------------------------

class LLMFormFiller:
    """Answers application form questions using an LLM and user profile data."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        self.api_key = api_key
        self.model = model
        self._cache: dict[str, str] = {}  # label → answer cache

    def answer_fields(
        self,
        fields: list[dict[str, Any]],
        profile_context: str,
        job_context: str = "",
    ) -> dict[str, str]:
        """Answer a batch of form fields using the LLM.

        *fields* is a list of dicts like::

            {
                "label": "How many years of ML experience?",
                "type": "number",          # text, number, select, radio
                "options": ["Yes", "No"],  # for select/radio only
                "required": true
            }

        Returns a mapping of label → answer value.
        """
        if not fields:
            return {}

        # Check cache first — return cached answers for known labels
        uncached_fields = []
        cached_answers: dict[str, str] = {}
        for f in fields:
            label = f.get("label", "")
            if label in self._cache:
                cached_answers[label] = self._cache[label]
            else:
                uncached_fields.append(f)

        if not uncached_fields:
            logger.debug("All %d fields answered from cache", len(cached_answers))
            return cached_answers

        # Build the prompt
        fields_desc = json.dumps(uncached_fields, indent=2, ensure_ascii=False)
        user_message = (
            f"=== APPLICANT PROFILE ===\n{profile_context}\n\n"
            f"=== JOB CONTEXT ===\n{job_context[:2000]}\n\n"
            f"=== FORM FIELDS TO FILL ===\n{fields_desc}"
        )

        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key)

            logger.info("Asking LLM to fill %d form fields via %s", len(uncached_fields), self.model)
            response = client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": FORM_FILLER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.1,
                max_tokens=1000,
            )

            raw = response.choices[0].message.content
            if not raw:
                logger.warning("LLM returned empty response for form fields")
                return cached_answers

            logger.debug("LLM form filler response:\n%s", raw)
            answers: dict[str, str] = json.loads(raw)

            # Ensure all values are strings
            for k, v in answers.items():
                answers[k] = str(v)

            # Cache the answers
            self._cache.update(answers)

            # Merge with cached
            answers.update(cached_answers)
            return answers

        except Exception as exc:
            logger.warning("LLM form filler failed: %s", exc)
            return cached_answers


def build_profile_context(profile: dict[str, Any] | None = None) -> str:
    """Build a text summary of the user profile for the LLM prompt."""
    if not profile:
        return "No profile data available."

    parts = []
    if profile.get("name"):
        parts.append(f"Name: {profile['name']}")
    if profile.get("first_name"):
        parts.append(f"First name: {profile['first_name']}")
    if profile.get("last_name"):
        parts.append(f"Last name: {profile['last_name']}")
    if profile.get("email"):
        parts.append(f"Email: {profile['email']}")
    if profile.get("phone"):
        parts.append(f"Phone: {profile['phone']}")
    if profile.get("phone_country_code"):
        parts.append(f"Phone country code: {profile['phone_country_code']}")
    if profile.get("title"):
        parts.append(f"Current title: {profile['title']}")
    if profile.get("summary"):
        parts.append(f"Summary: {profile['summary']}")
    if profile.get("skills"):
        parts.append(f"Skills: {', '.join(profile['skills'])}")
    if profile.get("experience_years"):
        parts.append(f"Total years of experience: {profile['experience_years']}")
    if profile.get("education"):
        parts.append(f"Education: {'; '.join(profile['education'])}")
    if profile.get("languages"):
        parts.append(f"Languages: {', '.join(profile['languages'])}")
    if profile.get("preferred_locations"):
        parts.append(f"Preferred locations: {', '.join(profile['preferred_locations'])}")
    if profile.get("desired_roles"):
        parts.append(f"Desired roles: {', '.join(profile['desired_roles'])}")
    if profile.get("seniority_level"):
        parts.append(f"Seniority: {profile['seniority_level']}")

    return "\n".join(parts)

