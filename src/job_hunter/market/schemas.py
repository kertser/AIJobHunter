"""Pydantic models for market extraction I/O."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ExtractionInput(BaseModel):
    """Input to a market extractor — one event's worth of text + metadata."""

    event_id: str
    title: str = ""
    company: str = ""
    raw_text: str = ""


class ExtractionResult(BaseModel):
    """Output contract for all market extractors.

    Every field is a simple list of strings.  Confidence and evidence
    linkage are handled downstream during graph materialisation.
    """

    explicit_skills: list[str] = Field(default_factory=list)
    inferred_skills: list[str] = Field(default_factory=list)
    tasks: list[str] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)

