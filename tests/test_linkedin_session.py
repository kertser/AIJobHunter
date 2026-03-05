"""Tests for LinkedIn session management and search URL construction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_hunter.linkedin.session import LinkedInSession, build_search_url


# ---------------------------------------------------------------------------
# build_search_url
# ---------------------------------------------------------------------------

class TestBuildSearchUrl:
    def test_basic_keywords(self) -> None:
        url = build_search_url(keywords=["Python Developer"])
        assert "linkedin.com/jobs/search/" in url
        assert "keywords=Python+Developer" in url

    def test_multiple_keywords(self) -> None:
        url = build_search_url(keywords=["Senior Python", "Backend Engineer"])
        assert "keywords=Senior+Python+Backend+Engineer" in url

    def test_location(self) -> None:
        url = build_search_url(keywords=["Dev"], location="New York, NY")
        assert "location=New+York" in url

    def test_remote_filter(self) -> None:
        url = build_search_url(keywords=["Dev"], remote=True)
        assert "f_WT=2" in url

    def test_no_remote_filter(self) -> None:
        url = build_search_url(keywords=["Dev"], remote=False)
        assert "f_WT" not in url

    def test_easy_apply_filter(self) -> None:
        url = build_search_url(keywords=["Dev"])
        assert "f_AL=true" in url

    def test_seniority_senior(self) -> None:
        url = build_search_url(keywords=["Dev"], seniority=["Senior"])
        assert "f_E=4" in url

    def test_seniority_multiple(self) -> None:
        url = build_search_url(keywords=["Dev"], seniority=["Senior", "Director"])
        assert "f_E=" in url
        # Both codes should be present
        assert "4" in url
        assert "5" in url

    def test_pagination_page_0(self) -> None:
        url = build_search_url(keywords=["Dev"], page=0)
        assert "start=" not in url

    def test_pagination_page_1(self) -> None:
        url = build_search_url(keywords=["Dev"], page=1)
        assert "start=25" in url

    def test_pagination_page_2(self) -> None:
        url = build_search_url(keywords=["Dev"], page=2)
        assert "start=50" in url

    def test_empty_keywords(self) -> None:
        url = build_search_url(keywords=[])
        assert "linkedin.com/jobs/search/" in url
        assert "f_AL=true" in url


# ---------------------------------------------------------------------------
# LinkedInSession — cookie save/load round-trip
# ---------------------------------------------------------------------------

class TestLinkedInSession:
    def test_has_cookies_false_when_no_file(self, tmp_path: Path) -> None:
        session = LinkedInSession(cookies_path=tmp_path / "cookies.json")
        assert session.has_cookies() is False

    def test_has_cookies_true_after_save(self, tmp_path: Path) -> None:
        session = LinkedInSession(cookies_path=tmp_path / "cookies.json")
        sample_cookies = [
            {"name": "li_at", "value": "abc123", "domain": ".linkedin.com", "path": "/"},
        ]
        session.save_cookies(sample_cookies)
        assert session.has_cookies() is True

    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        session = LinkedInSession(cookies_path=tmp_path / "cookies.json")
        original = [
            {"name": "li_at", "value": "token123", "domain": ".linkedin.com", "path": "/"},
            {"name": "JSESSIONID", "value": "sess456", "domain": ".linkedin.com", "path": "/"},
        ]
        session.save_cookies(original)
        loaded = session.load_cookies()
        assert len(loaded) == 2
        assert loaded[0]["name"] == "li_at"
        assert loaded[1]["value"] == "sess456"

    def test_load_cookies_raises_when_missing(self, tmp_path: Path) -> None:
        session = LinkedInSession(cookies_path=tmp_path / "nope.json")
        with pytest.raises(FileNotFoundError):
            session.load_cookies()

    def test_has_cookies_false_for_empty_file(self, tmp_path: Path) -> None:
        cookie_file = tmp_path / "cookies.json"
        cookie_file.write_text("")
        session = LinkedInSession(cookies_path=cookie_file)
        assert session.has_cookies() is False

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        deep_path = tmp_path / "a" / "b" / "c" / "cookies.json"
        session = LinkedInSession(cookies_path=deep_path)
        session.save_cookies([{"name": "test", "value": "1", "domain": "x", "path": "/"}])
        assert deep_path.exists()

