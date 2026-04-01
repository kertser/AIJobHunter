"""Central factory for OpenAI-compatible LLM clients.

Routes requests to either the real OpenAI API or a local llama-cpp-python
sidecar based on ``AppSettings.llm_provider``.

Usage::

    from job_hunter.llm_client import build_llm_client, get_chat_model

    client = build_llm_client(settings)
    model  = get_chat_model(settings)
    resp   = client.chat.completions.create(model=model, messages=[...])

The local sidecar exposes an OpenAI-compatible ``/v1`` endpoint, so the
same ``openai.OpenAI`` client works for both providers — only ``base_url``
and ``api_key`` differ.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from job_hunter.config.models import AppSettings

logger = logging.getLogger("job_hunter.llm_client")


class ResolvedLLMParams(NamedTuple):
    """Resolved inference parameters for a single LLM call."""

    temperature: float
    max_tokens: int | None  # None = let the provider decide


def get_task_params(settings: AppSettings, task_name: str) -> ResolvedLLMParams:
    """Resolve inference parameters for *task_name*.

    Merges: global defaults ← per-task override.  ``max_tokens=0`` in
    settings is treated as "no limit" → ``None``.
    """
    temperature = settings.llm_temperature
    max_tokens: int | None = settings.llm_max_tokens or None  # 0 → None

    override = settings.llm_task_overrides.get(task_name)
    if override is not None:
        if override.temperature is not None:
            temperature = override.temperature
        if override.max_tokens is not None:
            max_tokens = override.max_tokens

    return ResolvedLLMParams(temperature=temperature, max_tokens=max_tokens)


def build_llm_client(settings: AppSettings):
    """Return an ``openai.OpenAI`` client configured for the active provider.

    * ``llm_provider="openai"`` → uses the real OpenAI API (requires key).
    * ``llm_provider="local"``  → points at ``local_llm_url`` with a dummy key.
    """
    from openai import OpenAI

    provider = (settings.llm_provider or "openai").lower()

    if provider == "local":
        url = settings.local_llm_url or "http://localhost:8080/v1"
        logger.debug("Using local LLM at %s", url)
        return OpenAI(base_url=url, api_key="local-no-key-needed")

    # Default: real OpenAI
    if not settings.openai_api_key:
        raise ValueError(
            "OpenAI API key not set.  Go to Settings and enter your key, "
            "or set JOBHUNTER_OPENAI_API_KEY, or switch to llm_provider=local."
        )
    return OpenAI(api_key=settings.openai_api_key)


def get_chat_model(settings: AppSettings) -> str:
    """Return the chat-completion model name for the active provider."""
    provider = (settings.llm_provider or "openai").lower()
    if provider == "local":
        # llama-cpp-python ignores the model name in single-model mode,
        # but we pass it for logging / compatibility.
        return settings.local_llm_model or "local"
    return "gpt-4o-mini"


def get_embedding_model(settings: AppSettings) -> str:
    """Return the embedding model name.

    Embeddings always use OpenAI for now (small local models produce
    poor embeddings).  Returns ``""`` when no embedding model is available.
    """
    return "text-embedding-3-small"


def is_local_provider(settings: AppSettings) -> bool:
    """Check whether the active provider is a local LLM."""
    return (settings.llm_provider or "openai").lower() == "local"


def _strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` wrappers that local models sometimes emit."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    return text.strip()


def safe_json_parse(raw: str) -> dict:
    """Parse JSON from an LLM response, tolerating markdown fences.

    Local models sometimes wrap JSON in ````` ```json ... ``` ````` or add
    trailing commentary.  This helper strips fences and attempts a lenient
    parse.
    """
    raw = _strip_markdown_fences(raw)

    # First try: straight parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Second try: extract the first { … } block
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("Could not extract JSON from LLM response", raw, 0)

