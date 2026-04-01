"""Job title normalisation — clean raw titles into canonical role families.

Provider pattern: ``TitleNormalizer`` base, ``HeuristicTitleNormalizer`` (regex +
fuzzy clustering), ``OpenAITitleNormalizer`` (LLM-based), ``FakeTitleNormalizer``
(deterministic, for tests).
"""

from __future__ import annotations

import logging
import re

from rapidfuzz import fuzz

logger = logging.getLogger("job_hunter.market.title_normalizer")

# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class TitleNormalizer:
    """Abstract base for job-title normalisers."""

    version: str = "base-0.0"

    def normalize(self, title: str, *, company: str = "") -> str:
        """Map a single raw title → canonical role name."""
        raise NotImplementedError

    def normalize_batch(
        self,
        titles: list[str],
        *,
        companies: list[str] | None = None,
    ) -> dict[str, str]:
        """Normalise a batch of titles. Returns ``{raw_title: canonical}``."""
        companies = companies or [""] * len(titles)
        return {
            t: self.normalize(t, company=c)
            for t, c in zip(titles, companies)
        }


# ---------------------------------------------------------------------------
# Heuristic normaliser
# ---------------------------------------------------------------------------

# Listing prefixes that leak from job board HTML
_PREFIX_PATTERNS = [
    r"^wanted\s*[:\-–—]?\s*",
    r"^hiring\s*[:\-–—]?\s*",
    r"^we(?:'re|\s+are)\s+(?:looking\s+for|hiring|seeking)\s*[:\-–—]?\s*",
    r"^looking\s+for\s*[:\-–—]?\s*",
    r"^position\s*[:\-–—]?\s*",
    r"^open\s+role\s*[:\-–—]?\s*",
    r"^apply\s+now\s*[:\-–—]?\s*",
    r"^job\s*[:\-–—]\s*",
]

# Seniority / mode words stripped from titles (same as the old _STRIP_WORDS)
_STRIP_WORDS = {
    "senior", "junior", "mid", "lead", "principal", "staff",
    "director", "vp", "head", "chief", "intern", "i", "ii", "iii",
    "iv", "v", "remote", "hybrid", "contract", "freelance",
    "full-time", "part-time", "sr", "jr",
}

# Junk suffixes — organisational words that aren't part of the role name
_SUFFIX_WORDS = {
    "team", "teams", "department", "dept", "group", "division",
    "unit", "org", "organization", "organisation", "squad",
    "opening", "openings", "position", "positions", "opportunity",
    "vacancy", "vacancies", "role", "roles", "needed",
}

# Common company-name indicators (trailing fragments after the real title)
_COMPANY_INDICATORS = {"at", "for", "@", "-", "–", "—"}


def _strip_prefixes(title: str) -> str:
    """Remove listing prefixes like 'Wanted:', 'Hiring:', etc."""
    for pat in _PREFIX_PATTERNS:
        title = re.sub(pat, "", title, flags=re.IGNORECASE).strip()
    return title


def _strip_company_fragments(title: str, company: str) -> str:
    """Remove company name and surrounding noise from the title."""
    if not company:
        return title
    # Build patterns for the company name and its parts
    company_tokens = [t for t in re.split(r"[\s,.\-_]+", company.lower()) if len(t) > 1]
    for tok in company_tokens:
        # Remove "at CompanyName", "- CompanyName", "@ CompanyName"
        title = re.sub(
            rf"(?:\s+(?:at|for|@|–|—|-)\s+)?{re.escape(tok)}\b",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip()
    return title


def _pick_primary_from_slash(title: str) -> str:
    """Split on / | and pick the primary (first substantial) part."""
    # Only split if there are actual compound titles
    parts = re.split(r"\s*[/|]\s*", title)
    if len(parts) <= 1:
        return title
    # Pick the shortest part that has at least 2 meaningful words
    # (the first part is usually the primary title)
    best = parts[0].strip()
    return best if best else title


def _strip_suffix_words(tokens: list[str]) -> list[str]:
    """Remove organisational junk words from the end of the token list."""
    while tokens and tokens[-1] in _SUFFIX_WORDS:
        tokens = tokens[:-1]
    return tokens


class HeuristicTitleNormalizer(TitleNormalizer):
    """Regex + fuzzy-clustering normaliser — no API calls required.

    Cleaning pipeline:
    1. Strip listing prefixes ("Wanted:", "Hiring:", etc.)
    2. Strip company name fragments
    3. Pick primary title from slash/pipe-separated compounds
    4. Remove parentheticals
    5. Strip seniority/mode words
    6. Strip organisational suffix words ("team", "department", etc.)
    7. Fuzzy-cluster similar titles (token_sort_ratio ≥ threshold)
    """

    version: str = "heuristic-title-1.0"

    def __init__(self, *, cluster_threshold: int = 85) -> None:
        self._cluster_threshold = cluster_threshold

    def normalize(self, title: str, *, company: str = "") -> str:
        t = title.strip()
        if not t:
            return t

        # 1. Strip listing prefixes
        t = _strip_prefixes(t)

        # 2. Strip company name fragments
        t = _strip_company_fragments(t, company)

        # 3. Pick primary from slash/pipe compounds
        t = _pick_primary_from_slash(t)

        # 4. Remove parentheticals
        t = re.sub(r"\(.*?\)", "", t)

        # 5. Normalise to lower, keep only alphanum + space
        t = t.lower().strip()
        t = re.sub(r"[^a-z0-9 ]+", " ", t)
        tokens = t.split()

        # 6. Strip seniority/mode words
        tokens = [w for w in tokens if w and w not in _STRIP_WORDS]

        # 7. Strip organisational suffix words
        tokens = _strip_suffix_words(tokens)

        return " ".join(tokens).strip() or title.lower().strip()

    def normalize_batch(
        self,
        titles: list[str],
        *,
        companies: list[str] | None = None,
    ) -> dict[str, str]:
        companies = companies or [""] * len(titles)

        # First pass: clean each title individually
        raw_to_clean: dict[str, str] = {}
        for t, c in zip(titles, companies):
            raw_to_clean[t] = self.normalize(t, company=c)

        # Second pass: fuzzy-cluster similar cleaned titles
        unique_cleaned = list(set(raw_to_clean.values()))
        if len(unique_cleaned) <= 1:
            return raw_to_clean

        # Build clusters: group titles with high similarity
        clusters: list[list[str]] = []
        assigned: set[str] = set()

        for i, a in enumerate(unique_cleaned):
            if a in assigned:
                continue
            cluster = [a]
            assigned.add(a)
            for b in unique_cleaned[i + 1:]:
                if b in assigned:
                    continue
                score = fuzz.token_set_ratio(a, b)
                if score >= self._cluster_threshold:
                    cluster.append(b)
                    assigned.add(b)
            clusters.append(cluster)

        # For each cluster, pick the representative: shortest name
        # (usually the most generic, e.g., "data scientist" vs "data scientist ai research")
        cluster_map: dict[str, str] = {}
        for cluster in clusters:
            representative = min(cluster, key=len)
            for member in cluster:
                cluster_map[member] = representative

        # Apply cluster mapping
        return {
            raw: cluster_map.get(cleaned, cleaned)
            for raw, cleaned in raw_to_clean.items()
        }


# ---------------------------------------------------------------------------
# OpenAI normaliser
# ---------------------------------------------------------------------------


class OpenAITitleNormalizer(TitleNormalizer):
    """LLM-backed title normaliser using OpenAI."""

    version: str = "openai-title-1.0"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        batch_size: int = 50,
        *,
        base_url: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.batch_size = batch_size
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._cache: dict[str, str] = {}

    def normalize(self, title: str, *, company: str = "") -> str:
        if title in self._cache:
            return self._cache[title]
        result = self.normalize_batch([title], companies=[company])
        return result.get(title, title.lower().strip())

    def normalize_batch(
        self,
        titles: list[str],
        *,
        companies: list[str] | None = None,
    ) -> dict[str, str]:
        from openai import OpenAI
        from job_hunter.llm_client import safe_json_parse

        companies = companies or [""] * len(titles)
        result: dict[str, str] = {}

        # Check cache first
        uncached_titles: list[str] = []
        uncached_companies: list[str] = []
        for t, c in zip(titles, companies):
            if t in self._cache:
                result[t] = self._cache[t]
            else:
                uncached_titles.append(t)
                uncached_companies.append(c)

        if not uncached_titles:
            return result

        kwargs: dict = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url

        client = OpenAI(**kwargs)

        # Process in batches
        for batch_start in range(0, len(uncached_titles), self.batch_size):
            batch_titles = uncached_titles[batch_start:batch_start + self.batch_size]
            batch_companies = uncached_companies[batch_start:batch_start + self.batch_size]

            entries = []
            for t, c in zip(batch_titles, batch_companies):
                entry = f'"{t}"'
                if c:
                    entry += f" (company: {c})"
                entries.append(entry)

            system = (
                "You are a labour-market analyst. Your task is to normalise raw job "
                "titles into clean, canonical role names.\n\n"
                "Rules:\n"
                "- Remove company names, prefixes (Wanted, Hiring), suffixes "
                "(Team, Department, Division).\n"
                "- Remove seniority levels (Senior, Junior, Lead, etc.).\n"
                "- Collapse near-duplicate roles into one canonical name "
                "(e.g. 'Data Scientist AI Research' → 'Data Scientist').\n"
                "- Pick the most common industry-standard title.\n"
                "- Use title case (e.g. 'Data Scientist', 'Backend Engineer').\n"
                "- For compound titles separated by / or |, extract the primary role.\n"
                "- Return a JSON object mapping each raw title to its canonical form.\n\n"
                "Return ONLY valid JSON. No markdown, no commentary."
            )
            user = "Normalise these job titles:\n" + "\n".join(entries)

            try:
                resp = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=self.temperature if self.temperature is not None else 0.0,
                )
                raw = resp.choices[0].message.content or "{}"
                mapping = safe_json_parse(raw)

                for t in batch_titles:
                    canonical = mapping.get(t, t).lower().strip()
                    # Normalise to the same format as heuristic (lower, no special chars)
                    canonical = re.sub(r"[^a-z0-9 ]+", " ", canonical)
                    canonical = re.sub(r"\s+", " ", canonical).strip()
                    self._cache[t] = canonical
                    result[t] = canonical

            except Exception:
                logger.exception("OpenAI title normalisation failed, falling back to heuristic")
                fallback = HeuristicTitleNormalizer()
                for t, c in zip(batch_titles, batch_companies):
                    canonical = fallback.normalize(t, company=c)
                    self._cache[t] = canonical
                    result[t] = canonical

        return result


# ---------------------------------------------------------------------------
# Fake normaliser (tests)
# ---------------------------------------------------------------------------


class FakeTitleNormalizer(TitleNormalizer):
    """Deterministic normaliser for offline tests.

    Applies the same heuristic cleaning as :class:`HeuristicTitleNormalizer`
    but skips fuzzy clustering for predictable output.
    """

    version: str = "fake-title-1.0"

    def __init__(self, overrides: dict[str, str] | None = None) -> None:
        self._overrides = overrides or {}
        self._heuristic = HeuristicTitleNormalizer(cluster_threshold=100)  # no clustering

    def normalize(self, title: str, *, company: str = "") -> str:
        if title in self._overrides:
            return self._overrides[title]
        return self._heuristic.normalize(title, company=company)

    def normalize_batch(
        self,
        titles: list[str],
        *,
        companies: list[str] | None = None,
    ) -> dict[str, str]:
        companies = companies or [""] * len(titles)
        return {
            t: self.normalize(t, company=c)
            for t, c in zip(titles, companies)
        }

