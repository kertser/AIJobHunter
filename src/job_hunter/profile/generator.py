"""Profile generation from extracted resume/LinkedIn text via LLM."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from job_hunter.config.models import SearchProfile, UserProfile

logger = logging.getLogger("job_hunter.profile.generator")


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

class ProfileResult(BaseModel):
    """Output of the profile-generation LLM call."""

    user_profile: UserProfile
    search_profiles: list[SearchProfile] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a career-analysis assistant.  The user will provide text extracted from
their resume and optionally their LinkedIn profile.

Your task is to produce a JSON object with **exactly** two keys:

1. "user_profile" — an object describing the person:
   - name (string)
   - title (string) — current or most recent job title
   - summary (string) — 2-3 sentence professional summary
   - skills (list[string]) — technical and soft skills
   - experience_years (int)
   - preferred_locations (list[string]) — inferred from current/past locations
   - desired_roles (list[string]) — job titles this person should target
   - seniority_level (string) — e.g. "Junior", "Mid", "Senior", "Lead", "Principal"
   - education (list[string]) — degrees, certifications
   - spoken_languages (list[string]) — human/spoken languages ONLY (e.g. "English", "Hebrew", "Russian"). Do NOT include programming languages here.
   - programming_languages (list[string]) — programming/scripting languages ONLY (e.g. "Python", "SQL", "MATLAB")
   - preferred_industries (list[string]) — industries/domains the person seems drawn to based on their experience (e.g. "defense", "healthcare", "startups", "AI/ML")
   - disliked_industries (list[string]) — leave empty unless explicitly stated

2. "search_profiles" — a list of 1–3 search-profile objects, each tailored to
   a distinct career track the person could pursue.  Each object has:
   - name (string) — short slug, e.g. "backend-python"
   - keywords (list[string]) — LinkedIn search terms
   - location (string) — preferred search location
   - remote (bool)
   - seniority (list[string]) — e.g. ["Senior", "Mid-Senior"]
   - blacklist_companies (list[string]) — leave empty unless obvious
   - blacklist_titles (list[string]) — titles to avoid, e.g. ["Intern", "Junior"] for senior folks
   - min_fit_score (int) — recommended threshold 70-85
   - min_similarity (float) — recommended 0.30-0.45
   - max_applications_per_day (int) — recommended 15-30

Return ONLY valid JSON.  No markdown, no commentary.
"""


# ---------------------------------------------------------------------------
# Base class + implementations
# ---------------------------------------------------------------------------

class ProfileGenerator:
    """Base interface for profile generators."""

    def generate(self, extracted_text: str) -> ProfileResult:
        raise NotImplementedError


class OpenAIProfileGenerator(ProfileGenerator):
    """Generate profiles using the OpenAI Chat Completions API."""

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

    def generate(self, extracted_text: str) -> ProfileResult:
        from openai import OpenAI
        from job_hunter.llm_client import safe_json_parse

        kwargs: dict = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url

        client = OpenAI(**kwargs)

        logger.info("Sending profile-generation request to %s …", self.model)

        create_kwargs: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": extracted_text},
            ],
            "temperature": self.temperature if self.temperature is not None else 0.3,
        }
        if self.max_tokens:
            create_kwargs["max_tokens"] = self.max_tokens
        if not self.base_url:
            create_kwargs["response_format"] = {"type": "json_object"}

        response = client.chat.completions.create(**create_kwargs)

        raw = response.choices[0].message.content
        if raw is None:
            raise RuntimeError("LLM returned an empty response")

        logger.debug("Raw LLM response:\n%s", raw)
        data: dict[str, Any] = safe_json_parse(raw)
        return ProfileResult(**data)


class FakeProfileGenerator(ProfileGenerator):
    """Deterministic generator for tests — returns canned data."""

    def __init__(self, result: ProfileResult | None = None) -> None:
        self._result = result or ProfileResult(
            user_profile=UserProfile(
                name="Jane Doe",
                title="Senior Python Developer",
                summary="Experienced backend engineer with 8 years in Python.",
                skills=["Python", "FastAPI", "AWS", "PostgreSQL", "Docker"],
                experience_years=8,
                preferred_locations=["Remote", "New York, NY"],
                desired_roles=["Senior Python Developer", "Backend Engineer", "Staff Engineer"],
                seniority_level="Senior",
                education=["M.Sc. Computer Science"],
                spoken_languages=["English"],
                programming_languages=["Python", "SQL"],
                preferred_industries=[],
                disliked_industries=[],
            ),
            search_profiles=[
                SearchProfile(
                    name="backend-python",
                    keywords=["Senior Python Developer", "Backend Engineer"],
                    location="Remote",
                    remote=True,
                    seniority=["Senior", "Mid-Senior"],
                    blacklist_companies=[],
                    blacklist_titles=["Intern", "Junior"],
                    min_fit_score=75,
                    min_similarity=0.35,
                    max_applications_per_day=25,
                ),
            ],
        )

    def generate(self, extracted_text: str) -> ProfileResult:
        return self._result

