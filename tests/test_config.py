"""Tests for configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_hunter.config.loader import load_profiles, load_settings
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

