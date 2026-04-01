"""Load and save configuration from/to YAML files and environment."""

from __future__ import annotations

import enum
from pathlib import Path
from typing import Any

import yaml

from job_hunter.config.models import AppSettings, ScheduleConfig, ScheduleRunRecord, SearchProfile, UserProfile


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


# Fields persisted to .env when the user clicks "Save Settings"
_PERSISTED_FIELDS: dict[str, str] = {
    "openai_api_key": "JOBHUNTER_OPENAI_API_KEY",
    "llm_provider": "JOBHUNTER_LLM_PROVIDER",
    "local_llm_url": "JOBHUNTER_LOCAL_LLM_URL",
    "local_llm_model": "JOBHUNTER_LOCAL_LLM_MODEL",
    "llm_temperature": "JOBHUNTER_LLM_TEMPERATURE",
    "llm_max_tokens": "JOBHUNTER_LLM_MAX_TOKENS",
    "mock": "JOBHUNTER_MOCK",
    "dry_run": "JOBHUNTER_DRY_RUN",
    "headless": "JOBHUNTER_HEADLESS",
    "slowmo_ms": "JOBHUNTER_SLOWMO_MS",
    "log_level": "JOBHUNTER_LOG_LEVEL",
    "email_provider": "JOBHUNTER_EMAIL_PROVIDER",
    "resend_api_key": "JOBHUNTER_RESEND_API_KEY",
    "smtp_host": "JOBHUNTER_SMTP_HOST",
    "smtp_port": "JOBHUNTER_SMTP_PORT",
    "smtp_user": "JOBHUNTER_SMTP_USER",
    "smtp_password": "JOBHUNTER_SMTP_PASSWORD",
    "smtp_use_tls": "JOBHUNTER_SMTP_USE_TLS",
    "notification_email": "JOBHUNTER_NOTIFICATION_EMAIL",
    "notifications_enabled": "JOBHUNTER_NOTIFICATIONS_ENABLED",
    "secret_key": "JOBHUNTER_SECRET_KEY",
    "registration_enabled": "JOBHUNTER_REGISTRATION_ENABLED",
    "admin_password": "JOBHUNTER_ADMIN_PASSWORD",
}


def save_settings_env(settings: AppSettings, dotenv_path: Path | None = None) -> None:
    """Persist current AppSettings to the ``.env`` file.

    Uses ``python-dotenv`` ``set_key`` so comments and ordering are preserved.
    The file is created if it doesn't exist.
    """
    from dotenv import set_key

    if dotenv_path is None:
        dotenv_path = Path(".env")
    dotenv_path.touch(exist_ok=True)
    env_str = str(dotenv_path)

    for attr, env_var in _PERSISTED_FIELDS.items():
        value = getattr(settings, attr, "")
        # Normalise to string for .env
        if isinstance(value, bool):
            value = str(value).lower()
        elif isinstance(value, enum.Enum):
            value = value.value
        else:
            value = str(value)
        set_key(env_str, env_var, value, quote_mode="never")


# ---------------------------------------------------------------------------
# Schedule config & history
# ---------------------------------------------------------------------------

def load_schedule(path: Path) -> ScheduleConfig:
    """Load schedule config from YAML, returning defaults if missing."""
    if not path.exists():
        return ScheduleConfig()
    with open(path) as f:
        raw: Any = yaml.safe_load(f)
    if not raw:
        return ScheduleConfig()
    data = raw.get("schedule", raw) if isinstance(raw, dict) else raw
    return ScheduleConfig(**data)


def save_schedule(config: ScheduleConfig, path: Path) -> None:
    """Persist schedule config to YAML."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"schedule": config.model_dump(mode="json")}
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


_MAX_HISTORY = 100


def load_schedule_history(path: Path) -> list[ScheduleRunRecord]:
    """Load schedule run history from YAML."""
    if not path.exists():
        return []
    with open(path) as f:
        raw: Any = yaml.safe_load(f)
    if not raw or not isinstance(raw, list):
        return []
    return [ScheduleRunRecord(**r) for r in raw[-_MAX_HISTORY:]]


def append_schedule_history(record: ScheduleRunRecord, path: Path) -> None:
    """Append a run record to the history YAML (capped at 100)."""
    history = load_schedule_history(path)
    history.append(record)
    history = history[-_MAX_HISTORY:]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(
            [r.model_dump() for r in history],
            f, default_flow_style=False, sort_keys=False, allow_unicode=True,
        )


