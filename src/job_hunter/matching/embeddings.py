"""Embedding generation and similarity computation."""

from __future__ import annotations


class Embedder:
    """Base interface for embedding providers."""

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError

    def similarity(self, a: list[float], b: list[float]) -> float:
        raise NotImplementedError


class FakeEmbedder(Embedder):
    """Deterministic embedder for testing — returns a fixed similarity."""

    def __init__(self, fixed_similarity: float = 0.5) -> None:
        self._fixed = fixed_similarity

    def embed(self, text: str) -> list[float]:
        return [0.0] * 384  # mimic sentence-transformer dimension

    def similarity(self, a: list[float], b: list[float]) -> float:
        return self._fixed

