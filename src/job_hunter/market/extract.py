"""Signal extraction — deterministic + LLM-backed extractors.

Provider pattern: ``MarketExtractor`` base, ``HeuristicExtractor`` (default),
``OpenAIMarketExtractor``, and ``FakeMarketExtractor`` (for tests).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from job_hunter.market.db_models import ExtractionStatus, MarketExtraction
from job_hunter.market.repo import (
    create_extraction,
    get_events_without_extraction,
)
from job_hunter.market.schemas import ExtractionInput, ExtractionResult

logger = logging.getLogger("job_hunter.market.extract")


# ---------------------------------------------------------------------------
# Base extractor
# ---------------------------------------------------------------------------


class MarketExtractor:
    """Abstract base for market-signal extractors."""

    version: str = "base-0.0"

    def extract(self, inp: ExtractionInput) -> ExtractionResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Heuristic (deterministic) extractor
# ---------------------------------------------------------------------------

# Curated vocabulary — intentionally compact; extend as needed.
_SKILLS: set[str] = {
    "python", "java", "javascript", "typescript", "go", "rust", "ruby",
    "php", "swift", "kotlin", "scala", "r", "matlab", "sql", "html", "css",
    "bash", "shell", "powershell", "lua", "dart", "haskell", "perl",
    "elixir", "clojure", "c", "c++", "c#", "objective-c",
    # Data / ML
    "machine learning", "deep learning", "nlp",
    "natural language processing", "computer vision", "data science",
    "data engineering", "statistics",
    # Practices
    "agile", "scrum", "kanban", "devops", "ci/cd", "tdd", "bdd",
    "microservices", "rest api", "graphql", "event-driven",
    # Cloud
    "aws", "gcp", "azure", "cloud computing",
}

_TOOLS: set[str] = {
    "docker", "kubernetes", "terraform", "ansible", "jenkins",
    "github actions", "gitlab ci", "circleci", "prometheus", "grafana",
    "datadog", "elasticsearch", "kibana", "logstash",
    "postgresql", "mysql", "mongodb", "redis", "cassandra", "dynamodb",
    "sqlite", "oracle", "sql server",
    "kafka", "rabbitmq", "celery", "airflow",
    "spark", "hadoop", "flink", "dbt", "snowflake", "databricks",
    "react", "angular", "vue", "svelte", "next.js", "nuxt",
    "node.js", "express", "django", "flask", "fastapi", "spring boot",
    ".net", "rails", "laravel",
    "tensorflow", "pytorch", "scikit-learn", "pandas", "numpy",
    "git", "jira", "confluence", "figma", "postman",
    "nginx", "apache", "linux",
}

_SENIORITY_PATTERNS: dict[str, str] = {
    r"\bjunior\b": "junior",
    r"\bmid[- ]?level\b": "mid",
    r"\bsenior\b": "senior",
    r"\bstaff\b": "staff",
    r"\bprincipal\b": "principal",
    r"\blead\b": "lead",
    r"\bdirector\b": "director",
    r"\bvp\b": "vp",
    r"\bmanager\b": "manager",
}

_INDUSTRY_KEYWORDS: dict[str, str] = {
    "fintech": "fintech", "finance": "finance", "banking": "banking",
    "healthcare": "healthcare", "healthtech": "healthtech",
    "e-commerce": "e-commerce", "ecommerce": "e-commerce",
    "edtech": "edtech", "education": "education",
    "adtech": "adtech", "advertising": "advertising",
    "gaming": "gaming", "gamedev": "gaming",
    "biotech": "biotech", "pharmaceutical": "pharmaceutical",
    "cybersecurity": "cybersecurity", "security": "security",
    "logistics": "logistics", "supply chain": "supply chain",
    "saas": "saas", "enterprise": "enterprise",
    "startup": "startup", "media": "media",
    "automotive": "automotive", "aerospace": "aerospace",
    "energy": "energy", "cleantech": "cleantech",
    "real estate": "real estate", "proptech": "proptech",
    "insurance": "insurance", "insurtech": "insurtech",
    "telecom": "telecom", "telecommunications": "telecom",
}

# Verbs that introduce task phrases.
_TASK_VERBS = (
    "design", "implement", "build", "develop", "create", "architect",
    "deploy", "maintain", "manage", "lead", "optimize", "scale",
    "integrate", "automate", "monitor", "test", "debug", "review",
    "mentor", "collaborate", "migrate", "refactor", "analyze",
    "evaluate", "research", "document", "coordinate", "plan",
    "deliver", "support", "configure", "troubleshoot", "write",
)

# Indicators for problem / constraint phrases.
_PROBLEM_INDICATORS = (
    "solve", "address", "handle", "tackle", "overcome", "improve",
    "reduce", "ensure", "prevent", "troubleshoot", "fix", "resolve",
    "mitigate", "streamline", "diagnose",
)

# Skill inference map: if tool X is found, infer skill Y.
_INFERENCE_MAP: dict[str, list[str]] = {
    "react": ["javascript"],
    "angular": ["typescript"],
    "vue": ["javascript"],
    "next.js": ["javascript", "react"],
    "django": ["python"],
    "flask": ["python"],
    "fastapi": ["python"],
    "spring boot": ["java"],
    "rails": ["ruby"],
    "laravel": ["php"],
    ".net": ["c#"],
    "tensorflow": ["python", "machine learning"],
    "pytorch": ["python", "machine learning"],
    "scikit-learn": ["python", "machine learning"],
    "kubernetes": ["docker"],
    "kafka": ["event-driven"],
    "spark": ["data engineering"],
}


def _ngrams(tokens: list[str], n: int) -> list[str]:
    return [" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


class HeuristicExtractor(MarketExtractor):
    """Deterministic keyword- and pattern-based extractor."""

    version: str = "heuristic-1.0"

    def extract(self, inp: ExtractionInput) -> ExtractionResult:
        text = inp.raw_text
        text_lower = text.lower()
        # Tokenize, then strip trailing punctuation from each token
        raw_tokens = re.findall(r"[\w#+.\-/]+", text_lower)
        tokens = [t.rstrip(".,;:!?)") for t in raw_tokens]
        tokens = [t for t in tokens if t]

        # Build candidate n-grams (1–3)
        candidates: set[str] = set(tokens)
        for n in (2, 3):
            candidates.update(_ngrams(tokens, n))

        # --- Skills & tools ---
        explicit_skills: list[str] = sorted(
            {s for s in _SKILLS if s in candidates}
        )
        found_tools: list[str] = sorted(
            {t for t in _TOOLS if t in candidates}
        )

        # --- Inferred skills ---
        inferred: set[str] = set()
        for tool in found_tools:
            for inf_skill in _INFERENCE_MAP.get(tool, []):
                if inf_skill not in explicit_skills:
                    inferred.add(inf_skill)
        inferred_skills = sorted(inferred)

        # --- Tasks (verb-phrase extraction) ---
        tasks: list[str] = []
        sentences = re.split(r"[.\n•;]", text)
        for sent in sentences:
            stripped = sent.strip()
            if not stripped:
                continue
            first_word = stripped.split()[0].lower() if stripped.split() else ""
            if first_word in _TASK_VERBS:
                # Trim to reasonable length
                task = stripped[:120].strip()
                if task:
                    tasks.append(task)
        # De-duplicate while preserving order
        tasks = list(dict.fromkeys(tasks))

        # --- Problems ---
        problems: list[str] = []
        for sent in sentences:
            stripped = sent.strip()
            if not stripped:
                continue
            lower_sent = stripped.lower()
            if any(ind in lower_sent for ind in _PROBLEM_INDICATORS):
                prob = stripped[:120].strip()
                if prob:
                    problems.append(prob)
        problems = list(dict.fromkeys(problems))

        # --- Context ---
        context: dict[str, Any] = {}

        # Seniority
        title_lower = (inp.title or "").lower()
        full_text = f"{title_lower} {text_lower}"
        for pattern, level in _SENIORITY_PATTERNS.items():
            if re.search(pattern, title_lower):
                context["seniority"] = level
                break

        # Industry
        industries: list[str] = []
        for kw, industry in _INDUSTRY_KEYWORDS.items():
            if kw in full_text:
                if industry not in industries:
                    industries.append(industry)
        if industries:
            context["industries"] = industries

        # Remote
        if "remote" in full_text:
            context["remote"] = True
        elif "hybrid" in full_text:
            context["remote"] = "hybrid"
        elif "on-site" in full_text or "onsite" in full_text:
            context["remote"] = False

        return ExtractionResult(
            explicit_skills=explicit_skills,
            inferred_skills=inferred_skills,
            tasks=tasks,
            problems=problems,
            tools=found_tools,
            context=context,
        )


# ---------------------------------------------------------------------------
# Fake extractor (for tests)
# ---------------------------------------------------------------------------


class FakeMarketExtractor(MarketExtractor):
    """Returns canned data — for deterministic offline tests."""

    version: str = "fake-1.0"

    def __init__(
        self,
        skills: list[str] | None = None,
        tools: list[str] | None = None,
        tasks: list[str] | None = None,
    ) -> None:
        self.skills = skills if skills is not None else ["python", "sql"]
        self.tools = tools if tools is not None else ["docker", "postgresql"]
        self.tasks = tasks if tasks is not None else ["Build scalable APIs"]

    def extract(self, inp: ExtractionInput) -> ExtractionResult:
        return ExtractionResult(
            explicit_skills=list(self.skills),
            inferred_skills=[],
            tasks=list(self.tasks),
            problems=[],
            tools=list(self.tools),
            context={"seniority": "senior"},
        )


# ---------------------------------------------------------------------------
# OpenAI extractor (real, requires API key)
# ---------------------------------------------------------------------------


class OpenAIMarketExtractor(MarketExtractor):
    """LLM-backed extractor using OpenAI structured output."""

    version: str = "openai-1.0"

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

    def extract(self, inp: ExtractionInput) -> ExtractionResult:
        from openai import OpenAI
        from job_hunter.llm_client import safe_json_parse

        kwargs: dict = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url

        client = OpenAI(**kwargs)

        system = (
            "You are a labor-market analyst.  Given a job posting, extract:\n"
            '- "explicit_skills": skills explicitly stated\n'
            '- "inferred_skills": skills implied but not stated\n'
            '- "tasks": concrete work activities (verb phrases)\n'
            '- "problems": challenges or constraints mentioned\n'
            '- "tools": specific technologies, platforms, frameworks\n'
            '- "context": dict with optional keys seniority, industries, '
            "remote\n\nReturn ONLY a JSON object."
        )
        user = f"Title: {inp.title}\nCompany: {inp.company}\n\n{inp.raw_text}"

        resp = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self.temperature if self.temperature is not None else 0.0,
        )
        raw = resp.choices[0].message.content or "{}"
        data = safe_json_parse(raw)
        return ExtractionResult(**data)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_extraction(
    session: Session,
    extractor: MarketExtractor,
) -> int:
    """Extract signals from all un-extracted events.

    Idempotent per ``(event_id, extractor_version)``.  Returns the count of
    new extractions created.
    """
    events = get_events_without_extraction(session, extractor.version)
    created = 0
    for event in events:
        inp = ExtractionInput(
            event_id=str(event.id),
            title=event.title,
            company=event.company,
            raw_text=event.raw_text,
        )
        try:
            result = extractor.extract(inp)
            status = ExtractionStatus.COMPLETE
        except Exception:
            logger.exception("Extraction failed for event %s", event.id)
            result = ExtractionResult()
            status = ExtractionStatus.FAILED

        extraction = MarketExtraction(
            event_id=event.id,
            extractor_version=extractor.version,
            status=status,
            explicit_skills=result.explicit_skills,
            inferred_skills=result.inferred_skills,
            tasks=result.tasks,
            problems=result.problems,
            tools=result.tools,
            context=result.context,
        )
        create_extraction(session, extraction)
        created += 1

    logger.info(
        "Extracted %d event(s) with %s", created, extractor.version,
    )
    return created

