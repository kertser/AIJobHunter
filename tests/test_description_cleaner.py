"""Tests for the description cleaner — rule-based text cleanup."""

from __future__ import annotations

from job_hunter.matching.description_cleaner import clean_description_rules, looks_llm_formatted


class TestCleanDescriptionRules:
    def test_extracts_after_about_the_job(self) -> None:
        raw = (
            "0 notifications Skip to main content Home My Network\n"
            "About the job\n"
            "We are looking for a Senior Python Developer.\n\n"
            "Requirements:\n- 5 years experience\n- FastAPI\n\n"
            "Set alert for similar jobs\n"
            "About the company\nSome Corp"
        )
        result = clean_description_rules(raw)
        assert "Senior Python Developer" in result
        assert "5 years experience" in result
        assert "FastAPI" in result
        # Noise removed
        assert "notifications" not in result
        assert "Set alert" not in result
        assert "About the company" not in result

    def test_removes_navigation_noise(self) -> None:
        raw = (
            "Skip to main content\n"
            "Home My Network Jobs Messaging\n\n"
            "About the job\n"
            "Great role for a data scientist.\n"
            "LinkedIn Corporation © 2026"
        )
        result = clean_description_rules(raw)
        assert "data scientist" in result
        assert "Skip to main" not in result
        assert "LinkedIn Corporation" not in result

    def test_removes_show_more_artifacts(self) -> None:
        raw = (
            "About the job\n"
            "Description text here.\n"
            "… more"
        )
        result = clean_description_rules(raw)
        assert "Description text here" in result
        assert "… more" not in result

    def test_preserves_short_text(self) -> None:
        raw = "Short text"
        assert clean_description_rules(raw) == "Short text"

    def test_empty_input(self) -> None:
        assert clean_description_rules("") == ""
        assert clean_description_rules(None) is None

    def test_collapses_excessive_newlines(self) -> None:
        raw = (
            "About the job\n"
            "Line one.\n\n\n\n\nLine two.\n\n\n\n\nLine three.\n"
            "Set alert for similar jobs"
        )
        result = clean_description_rules(raw)
        assert "\n\n\n" not in result
        assert "Line one" in result
        assert "Line two" in result

    def test_full_linkedin_page_extraction(self) -> None:
        """Simulate a full LinkedIn page dump like the user reported."""
        raw = (
            "0 notifications Skip to main content Home My Network Jobs Messaging 2 "
            "Notifications Me For Business\nTry Premium for ₪0\n\n"
            "Acme Corp\nSenior Python Developer\n"
            "Remote · 1 week ago · Over 50 applicants\n"
            "Promoted by hirer · Actively reviewing applicants\n"
            "Remote Full-time Easy Apply\n"
            "Save Use AI to assess how you fit\n"
            "Get AI-powered advice on this job and more exclusive features with Premium. "
            "Try Premium for ₪0\n"
            "Show match details\nTailor my resume\nHelp me stand out\n\n"
            "About the job\n"
            "We are looking for a Senior Python Developer with 5+ years experience.\n\n"
            "Requirements:\n- Python\n- FastAPI\n- AWS\n- Docker\n\n"
            "Benefits:\n- Remote work\n- Competitive salary\n\n"
            "Set alert for similar jobs\n"
            "About the company\nAcme Corp\n24,170 followers\n\n"
            "Looking for talent?\nPost a job\n"
            "Select language\nEnglish (English)\n"
        )
        result = clean_description_rules(raw)
        # Core content preserved
        assert "Senior Python Developer" in result
        assert "5+ years experience" in result
        assert "Python" in result
        assert "FastAPI" in result
        assert "Remote work" in result
        assert "Competitive salary" in result
        # Noise removed
        assert "notifications" not in result
        assert "Try Premium" not in result
        assert "Show match details" not in result
        assert "Tailor my resume" not in result
        assert "Select language" not in result
        assert "Post a job" not in result


class TestLooksLlmFormatted:
    def test_detects_markdown_formatted_text(self) -> None:
        text = (
            "Senior Python Developer at Acme Corp.\n\n"
            "**Responsibilities**\n"
            "- Build and maintain backend services\n"
            "- Collaborate with cross-functional teams\n\n"
            "**Requirements**\n"
            "- 5+ years of **Python** experience\n"
            "- Experience with **FastAPI** and **Docker**\n"
        )
        assert looks_llm_formatted(text) is True

    def test_rejects_plain_text(self) -> None:
        text = (
            "We are looking for a Senior Python Developer with 5+ years experience.\n"
            "Requirements: Python, FastAPI, AWS, Docker.\n"
            "Benefits: Remote work, competitive salary.\n"
        )
        assert looks_llm_formatted(text) is False

    def test_rejects_empty_and_short(self) -> None:
        assert looks_llm_formatted("") is False
        assert looks_llm_formatted(None) is False
        assert looks_llm_formatted("short") is False

    def test_rejects_bold_only_without_bullets(self) -> None:
        text = "This has **bold** text but no bullet points at all. " * 5
        assert looks_llm_formatted(text) is False

    def test_rejects_bullets_only_without_bold(self) -> None:
        text = "List of items:\n- Item one\n- Item two\n- Item three\n" * 3
        assert looks_llm_formatted(text) is False

