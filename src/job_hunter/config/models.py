"""Pydantic models for application configuration and search profiles."""

from __future__ import annotations

import enum
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LogLevel(str, enum.Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class AppSettings(BaseSettings):
    """Global application settings, populated from env vars and CLI overrides."""

    model_config = SettingsConfigDict(env_prefix="JOBHUNTER_")

    llm_provider: str = "openai"
    openai_api_key: str = ""
    data_dir: Path = Path("data")

    # Runtime flags (set via CLI)
    mock: bool = False
    dry_run: bool = False
    headless: bool = True
    slowmo_ms: int = 0
    log_level: LogLevel = LogLevel.INFO


class SearchProfile(BaseModel):
    """A single job-search profile loaded from YAML."""

    name: str
    keywords: list[str] = Field(default_factory=list)
    location: str = ""
    remote: bool = False
    seniority: list[str] = Field(default_factory=list)
    blacklist_companies: list[str] = Field(default_factory=list)
    blacklist_titles: list[str] = Field(default_factory=list)

    # Scoring thresholds
    min_fit_score: int = 75
    min_similarity: float = 0.35
    max_applications_per_day: int = 25


class UserProfile(BaseModel):
    """User profile extracted from resume and LinkedIn PDF."""

    name: str = ""
    title: str = ""
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    experience_years: int = 0
    preferred_locations: list[str] = Field(default_factory=list)
    desired_roles: list[str] = Field(default_factory=list)
    seniority_level: str = ""
    education: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)


