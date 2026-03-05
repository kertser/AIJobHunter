"""Tests for profile extraction and generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_hunter.config.loader import (
    load_profiles,
    load_user_profile,
    save_profiles,
    save_user_profile,
)
from job_hunter.config.models import SearchProfile, UserProfile
from job_hunter.profile.extract import extract_text_from_pdf, extract_texts, _is_linkedin_url
from job_hunter.profile.generator import FakeProfileGenerator, ProfileGenerator, ProfileResult


# ---------------------------------------------------------------------------
# Helpers — create a tiny PDF for testing
# ---------------------------------------------------------------------------

def _create_test_pdf(path: Path, text: str) -> Path:
    """Create a minimal single-page PDF with *text* using PyMuPDF."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

class TestExtractTextFromPdf:
    def test_extracts_text(self, tmp_path: Path) -> None:
        pdf = _create_test_pdf(tmp_path / "test.pdf", "Hello World from PDF")
        text = extract_text_from_pdf(pdf)
        assert "Hello World from PDF" in text

    def test_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            extract_text_from_pdf(Path("/nonexistent/file.pdf"))

    def test_empty_pdf_raises(self, tmp_path: Path) -> None:
        import fitz

        doc = fitz.open()
        doc.new_page()  # blank page, no text
        pdf = tmp_path / "empty.pdf"
        doc.save(str(pdf))
        doc.close()
        with pytest.raises(ValueError, match="No extractable text"):
            extract_text_from_pdf(pdf)


class TestExtractTexts:
    def test_resume_only(self, tmp_path: Path) -> None:
        resume = _create_test_pdf(tmp_path / "resume.pdf", "My Resume Content")
        text = extract_texts(resume)
        assert "=== RESUME ===" in text
        assert "My Resume Content" in text
        assert "=== LINKEDIN PROFILE ===" not in text

    def test_resume_and_linkedin_pdf(self, tmp_path: Path) -> None:
        resume = _create_test_pdf(tmp_path / "resume.pdf", "Resume Text")
        linkedin = _create_test_pdf(tmp_path / "linkedin.pdf", "LinkedIn Text")
        text = extract_texts(resume, str(linkedin))
        assert "=== RESUME ===" in text
        assert "Resume Text" in text
        assert "=== LINKEDIN PROFILE ===" in text
        assert "LinkedIn Text" in text

    def test_resume_and_linkedin_pdf_as_path(self, tmp_path: Path) -> None:
        resume = _create_test_pdf(tmp_path / "resume.pdf", "Resume Text")
        linkedin = _create_test_pdf(tmp_path / "linkedin.pdf", "LinkedIn Text")
        text = extract_texts(resume, linkedin)
        assert "=== LINKEDIN PROFILE ===" in text
        assert "LinkedIn Text" in text


class TestIsLinkedInUrl:
    def test_valid_urls(self) -> None:
        assert _is_linkedin_url("https://www.linkedin.com/in/john-doe/") is True
        assert _is_linkedin_url("https://linkedin.com/in/john-doe") is True
        assert _is_linkedin_url("http://www.linkedin.com/in/jane") is True
        assert _is_linkedin_url("https://www.linkedin.com/in/mike-kertser/") is True

    def test_invalid_urls(self) -> None:
        assert _is_linkedin_url("https://www.linkedin.com/company/acme") is False
        assert _is_linkedin_url("https://google.com") is False
        assert _is_linkedin_url("/path/to/file.pdf") is False
        assert _is_linkedin_url("C:\\Users\\resume.pdf") is False
        assert _is_linkedin_url("resume.pdf") is False


# ---------------------------------------------------------------------------
# Profile generation
# ---------------------------------------------------------------------------

class TestFakeProfileGenerator:
    def test_returns_profile_result(self) -> None:
        gen = FakeProfileGenerator()
        result = gen.generate("some text")
        assert isinstance(result, ProfileResult)
        assert isinstance(result.user_profile, UserProfile)
        assert len(result.search_profiles) >= 1
        assert all(isinstance(sp, SearchProfile) for sp in result.search_profiles)

    def test_user_profile_fields(self) -> None:
        gen = FakeProfileGenerator()
        result = gen.generate("some text")
        up = result.user_profile
        assert up.name != ""
        assert up.experience_years > 0
        assert len(up.skills) > 0
        assert len(up.desired_roles) > 0

    def test_custom_result(self) -> None:
        custom = ProfileResult(
            user_profile=UserProfile(name="Test User", title="Tester"),
            search_profiles=[SearchProfile(name="test-profile", keywords=["QA"])],
        )
        gen = FakeProfileGenerator(result=custom)
        result = gen.generate("anything")
        assert result.user_profile.name == "Test User"
        assert result.search_profiles[0].name == "test-profile"


class TestBaseProfileGenerator:
    def test_base_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError):
            ProfileGenerator().generate("text")


# ---------------------------------------------------------------------------
# YAML round-trips
# ---------------------------------------------------------------------------

class TestUserProfileYamlRoundTrip:
    def test_save_and_load(self, tmp_path: Path) -> None:
        profile = UserProfile(
            name="Alice",
            title="ML Engineer",
            skills=["Python", "TensorFlow"],
            experience_years=5,
            seniority_level="Mid-Senior",
        )
        path = tmp_path / "user_profile.yml"
        save_user_profile(profile, path)
        loaded = load_user_profile(path)
        assert loaded.name == "Alice"
        assert loaded.title == "ML Engineer"
        assert loaded.experience_years == 5
        assert "Python" in loaded.skills


class TestProfilesYamlRoundTrip:
    def test_save_and_load(self, tmp_path: Path) -> None:
        profiles = [
            SearchProfile(
                name="backend",
                keywords=["Python Developer"],
                location="Remote",
                remote=True,
                seniority=["Senior"],
            ),
            SearchProfile(
                name="ml",
                keywords=["ML Engineer", "Data Scientist"],
                location="NYC",
            ),
        ]
        path = tmp_path / "profiles.yml"
        save_profiles(profiles, path)
        loaded = load_profiles(path)
        assert len(loaded) == 2
        assert loaded[0].name == "backend"
        assert loaded[0].remote is True
        assert loaded[1].name == "ml"
        assert "ML Engineer" in loaded[1].keywords

