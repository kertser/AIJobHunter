"""Load and save configuration from/to YAML files and environment."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from job_hunter.config.models import AppSettings, SearchProfile, UserProfile


def load_profiles(path: Path) -> list[SearchProfile]:
    """Parse a YAML file and return a list of SearchProfile objects."""
    with open(path) as f:
        raw: Any = yaml.safe_load(f)

    if raw is None:
        return []

    profiles_data: list[dict[str, Any]]
    if isinstance(raw, list):
        profiles_data = raw
    elif isinstance(raw, dict) and "profiles" in raw:
        profiles_data = raw["profiles"]
    else:
        raise ValueError(f"Invalid profiles YAML structure in {path}")

    return [SearchProfile(**p) for p in profiles_data]


def save_profiles(profiles: list[SearchProfile], path: Path) -> None:
    """Serialize a list of SearchProfile objects to a YAML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"profiles": [p.model_dump() for p in profiles]}
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def load_user_profile(path: Path) -> UserProfile:
    """Load a UserProfile from a YAML file."""
    with open(path) as f:
        raw: Any = yaml.safe_load(f)

    if raw is None:
        raise ValueError(f"Empty user profile file: {path}")

    if isinstance(raw, dict) and "user_profile" in raw:
        return UserProfile(**raw["user_profile"])

    return UserProfile(**raw)


def save_user_profile(profile: UserProfile, path: Path) -> None:
    """Serialize a UserProfile to a YAML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"user_profile": profile.model_dump()}
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def load_settings(**cli_overrides: Any) -> AppSettings:
    """Build AppSettings from env vars, then overlay any CLI flag overrides."""
    # Filter out None values so env/defaults aren't overridden by absent flags
    overrides = {k: v for k, v in cli_overrides.items() if v is not None}
    return AppSettings(**overrides)

