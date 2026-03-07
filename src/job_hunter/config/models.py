"""Pydantic models for application configuration and search profiles."""

from __future__ import annotations

import enum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LogLevel(str, enum.Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class AppSettings(BaseSettings):
    """Global application settings, populated from env vars and CLI overrides."""

    model_config = SettingsConfigDict(
        env_prefix="JOBHUNTER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

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
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    phone_country_code: str = ""
    title: str = ""
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    experience_years: int = 0
    preferred_locations: list[str] = Field(default_factory=list)
    desired_roles: list[str] = Field(default_factory=list)
    seniority_level: str = ""
    education: list[str] = Field(default_factory=list)
    spoken_languages: list[str] = Field(default_factory=list)
    programming_languages: list[str] = Field(default_factory=list)

    # Industry / area preferences for filtering discovered jobs
    preferred_industries: list[str] = Field(default_factory=list)
    disliked_industries: list[str] = Field(default_factory=list)

    # Backward compatibility: accept old 'languages' key
    languages: list[str] = Field(default_factory=list, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _migrate_languages(cls, values: dict) -> dict:
        """Migrate old 'languages' field to spoken/programming split."""
        if isinstance(values, dict):
            old_langs = values.get("languages", [])
            if old_langs and not values.get("spoken_languages"):
                # Known programming languages to separate
                prog_langs = {
                    "python", "java", "javascript", "typescript", "c", "c++", "c#",
                    "go", "golang", "rust", "ruby", "php", "swift", "kotlin", "scala",
                    "r", "matlab", "sql", "html", "css", "perl", "bash", "shell",
                    "powershell", "lua", "dart", "haskell", "julia", "fortran",
                    "assembly", "vba", "objective-c", "groovy", "elixir", "erlang",
                    "clojure", "lisp", "prolog", "cobol", "vhdl", "verilog",
                }
                spoken = []
                programming = []
                for lang in old_langs:
                    if lang.lower().strip() in prog_langs:
                        programming.append(lang)
                    else:
                        spoken.append(lang)
                values["spoken_languages"] = spoken
                if not values.get("programming_languages"):
                    values["programming_languages"] = programming
        return values

    def get_first_name(self) -> str:
        """Return first name, deriving from full name if needed."""
        if self.first_name:
            return self.first_name
        parts = self.name.split()
        return parts[0] if parts else ""

    def get_last_name(self) -> str:
        """Return last name, deriving from full name if needed."""
        if self.last_name:
            return self.last_name
        parts = self.name.split()
        return " ".join(parts[1:]) if len(parts) > 1 else ""

    def build_form_answers(self) -> dict[str, str]:
        """Build a label→value mapping for Easy Apply form filling.

        Keys are lowercase label text as LinkedIn renders them.
        """
        answers: dict[str, str] = {}
        first = self.get_first_name()
        last = self.get_last_name()

        if first:
            answers["first name"] = first
        if last:
            answers["last name"] = last
        if self.email:
            answers["email address"] = self.email
            answers["email"] = self.email
        if self.phone:
            answers["mobile phone number"] = self.phone
            answers["phone number"] = self.phone
            answers["phone"] = self.phone
        if self.phone_country_code:
            answers["phone country code"] = self.phone_country_code
        if self.title:
            answers["headline"] = self.title
            answers["current title"] = self.title
        if self.summary:
            answers["summary"] = self.summary
        if self.experience_years:
            years = str(self.experience_years)
            answers["years of experience"] = years
            answers["total years of experience"] = years
        if self.preferred_locations:
            answers["city"] = self.preferred_locations[0]
            answers["location"] = self.preferred_locations[0]
        return answers


