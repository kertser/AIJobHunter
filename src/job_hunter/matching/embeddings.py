"""Embedding generation and similarity computation."""

from __future__ import annotations

import logging
import math

logger = logging.getLogger("job_hunter.matching.embeddings")


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        raise ValueError(f"Vector length mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class Embedder:
    """Base interface for embedding providers."""

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError

    def similarity(self, a: list[float], b: list[float]) -> float:
        raise NotImplementedError


class OpenAIEmbedder(Embedder):
    """Generate embeddings via the OpenAI Embeddings API."""

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        *,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def embed(self, text: str) -> list[float]:
        from openai import OpenAI

        kwargs: dict = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url

        client = OpenAI(**kwargs)

        # Truncate very long texts to stay within token limits
        truncated = text[:8000]

        logger.debug("Requesting embedding for %d chars via %s", len(truncated), self.model)
        response = client.embeddings.create(
            model=self.model,
            input=truncated,
        )
        return response.data[0].embedding

    def similarity(self, a: list[float], b: list[float]) -> float:
        return cosine_similarity(a, b)


class FakeEmbedder(Embedder):
    """Deterministic embedder for testing — returns a fixed similarity."""

    def __init__(self, fixed_similarity: float = 0.5) -> None:
        self._fixed = fixed_similarity

    def embed(self, text: str) -> list[float]:
        return [0.0] * 384  # mimic sentence-transformer dimension

    def similarity(self, a: list[float], b: list[float]) -> float:
        return self._fixed

