"""Tests for configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_hunter.config.loader import load_profiles, load_settings, save_settings_env
from job_hunter.config.models import AppSettings, LogLevel, SearchProfile

FIXTURES = Path(__file__).parent / "fixtures"


class TestLoadProfiles:
    def test_load_profiles_from_yaml(self) -> None:
        profiles = load_profiles(FIXTURES / "profiles.yml")
        assert len(profiles) == 2
        assert all(isinstance(p, SearchProfile) for p in profiles)

    def test_first_profile_fields(self) -> None:
        profiles = load_profiles(FIXTURES / "profiles.yml")
        default = profiles[0]
        assert default.name == "default"
        assert "Python Developer" in default.keywords
        assert default.remote is True
        assert default.min_fit_score == 75

    def test_second_profile_fields(self) -> None:
        profiles = load_profiles(FIXTURES / "profiles.yml")
        ml = profiles[1]
        assert ml.name == "ml-focused"
        assert ml.location == "New York, NY"
        assert ml.remote is False
        assert ml.min_similarity == 0.40

    def test_empty_yaml_returns_empty_list(self, tmp_path: Path) -> None:
        empty_file = tmp_path / "empty.yml"
        empty_file.write_text("")
        profiles = load_profiles(empty_file)
        assert profiles == []

    def test_invalid_structure_raises(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.yml"
        bad_file.write_text("key: value\n")
        with pytest.raises(ValueError, match="Invalid profiles YAML"):
            load_profiles(bad_file)


class TestLoadSettings:
    def test_defaults(self) -> None:
        settings = load_settings()
        assert isinstance(settings, AppSettings)
        assert settings.mock is False
        assert settings.dry_run is False
        assert settings.headless is True
        assert settings.log_level == LogLevel.INFO

    def test_cli_overrides(self) -> None:
        settings = load_settings(mock=True, dry_run=True, log_level=LogLevel.DEBUG)
        assert settings.mock is True
        assert settings.dry_run is True
        assert settings.log_level == LogLevel.DEBUG

    def test_none_overrides_are_ignored(self) -> None:
        settings = load_settings(mock=None, dry_run=None)
        assert settings.mock is False
        assert settings.dry_run is False


class TestSaveSettingsEnv:
    def test_creates_env_file(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        settings = AppSettings(resend_api_key="re_test_123", notification_email="a@b.com")
        save_settings_env(settings, env_path)
        content = env_path.read_text()
        assert "JOBHUNTER_RESEND_API_KEY=re_test_123" in content
        assert "JOBHUNTER_NOTIFICATION_EMAIL=a@b.com" in content

    def test_preserves_existing_comments(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        env_path.write_text("# My comment\nJOBHUNTER_MOCK=false\n")
        settings = AppSettings(mock=True)
        save_settings_env(settings, env_path)
        content = env_path.read_text()
        assert "# My comment" in content
        assert "JOBHUNTER_MOCK=true" in content

    def test_round_trip_resend_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_path = tmp_path / ".env"
        settings = AppSettings(resend_api_key="re_live_abc123", email_provider="resend")
        save_settings_env(settings, env_path)
        # Clear any real env var so .env file is the source of truth
        monkeypatch.delenv("JOBHUNTER_RESEND_API_KEY", raising=False)
        monkeypatch.delenv("JOBHUNTER_EMAIL_PROVIDER", raising=False)
        loaded = AppSettings(_env_file=str(env_path))
        assert loaded.resend_api_key == "re_live_abc123"
        assert loaded.email_provider == "resend"

    def test_enum_persisted_as_string(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        settings = AppSettings(log_level=LogLevel.DEBUG)
        save_settings_env(settings, env_path)
        content = env_path.read_text()
        assert "JOBHUNTER_LOG_LEVEL=DEBUG" in content

    def test_bool_persisted_as_lowercase(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        settings = AppSettings(notifications_enabled=True, smtp_use_tls=False)
        save_settings_env(settings, env_path)
        content = env_path.read_text()
        assert "JOBHUNTER_NOTIFICATIONS_ENABLED=true" in content
        assert "JOBHUNTER_SMTP_USE_TLS=false" in content

