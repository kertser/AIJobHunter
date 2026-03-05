"""Form-filling helpers for the Easy Apply wizard."""

from __future__ import annotations


def fill_text_field(selector: str, value: str) -> None:
    raise NotImplementedError


def upload_resume(selector: str, path: str) -> None:
    raise NotImplementedError


def select_dropdown(selector: str, value: str) -> None:
    raise NotImplementedError

