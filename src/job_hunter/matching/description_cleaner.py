"""Clean and format raw job descriptions extracted from LinkedIn.

Raw descriptions scraped from LinkedIn often include navigation elements,
sidebar text, company boilerplate, and language selectors. This module
provides both a rule-based cleaner and an optional LLM-based formatter.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("job_hunter.matching.description_cleaner")

# Markers that indicate the end of useful job description content
_END_MARKERS = [
    "Set alert for similar jobs",
    "Set alert for similar",
    "About the company",
    "People you can reach out to",
    "Meet the hiring team",
    "Show more jobs like this",
    "Similar jobs",
    "Interested in working with us",
    "Looking for talent?",
    "Post a job",
    "About\nAccessibility",
    "LinkedIn Corporation ©",
    "Select language",
    "Questions?\nVisit our Help Center",
    "Manage your account and privacy",
    "Recommendation transparency",
]

# Markers that indicate the start of useful content
_START_MARKERS = [
    "About the job",
    "About this job",
    "Job Description",
    "Description",
]

# Navigation / header noise patterns to strip
_NOISE_PATTERNS = [
    r"^\d+ notifications?\s*",
    r"^Skip to (?:main )?content\s*",
    r"^Home\s+My Network\s+Jobs\s+Messaging.*?(?=\n\n|\Z)",
    r"^.*?(?:Try Premium|Premium for).*?\n",
    r"Promoted by hirer.*?\n",
    r"Actively reviewing applicants?\s*",
    r"(?:Remote|On-site|Hybrid)\s+Full-time\s+(?:Easy Apply)?\s*",
    r"Save\s+Use AI to assess.*?\n",
    r"Get AI-powered advice.*?Premium\.\s*",
    r"Try Premium for .*?\n",
    r"Show match details\s*",
    r"Tailor my resume\s*",
    r"Help me stand out\s*",
    r"\.\.\. more\s*$",
    r"… more\s*$",
    r"Show more\s*$",
    r"Show less\s*$",
]


def clean_description_rules(raw: str) -> str:
    """Apply rule-based cleaning to a raw LinkedIn job description.

    This strips navigation elements, sidebar content, and boilerplate
    without requiring an LLM call.
    """
    if not raw or len(raw) < 20:
        return raw

    text = raw

    # Try to find the start of actual job content
    best_start = 0
    for marker in _START_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            # Skip the marker itself
            best_start = idx + len(marker)
            break

    if best_start > 0:
        text = text[best_start:]

    # Find the end of useful content
    best_end = len(text)
    for marker in _END_MARKERS:
        idx = text.find(marker)
        if idx != -1 and idx < best_end:
            best_end = idx

    text = text[:best_end]

    # Remove noise patterns
    for pattern in _NOISE_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.MULTILINE | re.IGNORECASE)

    # Clean up excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    return text


# ---------------------------------------------------------------------------
# LLM-based description formatting
# ---------------------------------------------------------------------------

_FORMAT_SYSTEM_PROMPT = """\
You are a job description formatter. You receive raw text scraped from a LinkedIn
job posting page. The text may contain navigation elements, sidebar content,
company boilerplate, and formatting artifacts.

Your task:
1. Extract ONLY the job description content (role overview, responsibilities,
   requirements, qualifications, benefits, compensation)
2. Output clean **Markdown** with:
   - A short 1-2 sentence role summary at the top (no heading)
   - Clear section headings using **bold** text (e.g. **Responsibilities**, **Requirements**)
   - Bullet points (- ) for list items
   - **Bold** for key terms, skills, and important qualifications
   - Preserve salary/compensation if mentioned
3. Keep ALL factual content — do not omit any requirements, skills, or details
4. REMOVE: navigation text, sidebar widgets, "Try Premium", language selectors,
   "Show match details", "Tailor my resume", company follower counts,
   "About the company" boilerplate, "Set alert for similar jobs", footer text
5. Be concise but complete — no fluff, no commentary, no "Here is the cleaned..."

Output ONLY the Markdown-formatted job description.
"""


def clean_description_llm(raw: str, api_key: str, model: str = "gpt-4o-mini") -> str:
    """Use an LLM to clean and format a raw job description into Markdown.

    Falls back to rule-based cleaning if the LLM call fails.
    """
    if not raw or len(raw) < 50:
        return raw

    if not api_key:
        logger.debug("No API key — falling back to rule-based cleaning")
        return clean_description_rules(raw)

    # First apply rule-based cleaning to reduce token usage
    pre_cleaned = clean_description_rules(raw)
    if len(pre_cleaned) < 30:
        pre_cleaned = raw[:8000]

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _FORMAT_SYSTEM_PROMPT},
                {"role": "user", "content": pre_cleaned[:6000]},
            ],
            temperature=0.1,
            max_tokens=2000,
        )
        result = response.choices[0].message.content or ""
        if len(result) > 50:
            return result.strip()
        logger.warning("LLM returned too-short description, using rule-based fallback")
    except Exception as exc:
        logger.warning("LLM description cleaning failed: %s — using rule-based fallback", exc)

    return clean_description_rules(raw)


