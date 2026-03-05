"""Form-filling helpers for the Easy Apply wizard."""

from __future__ import annotations

import logging
from pathlib import Path

from playwright.async_api import Page

from job_hunter.linkedin import selectors as sel

logger = logging.getLogger("job_hunter.linkedin.forms")


async def upload_resume(page: Page, resume_path: str | Path) -> None:
    """Upload a resume file via the file input in the wizard."""
    file_input = page.locator(sel.WIZARD_RESUME_INPUT)
    if await file_input.count() > 0:
        await file_input.set_input_files(str(resume_path))
        logger.info("Uploaded resume: %s", Path(resume_path).name)
    else:
        logger.debug("No file input found — resume may already be attached")


async def fill_form_fields(page: Page, answers: dict[str, str] | None = None) -> dict[str, str]:
    """Fill text inputs and selects in the current wizard step.

    *answers* maps lowercase label text → value. If a question is not in
    *answers* a sensible default is used.

    Returns the dict of answers that were actually filled.
    """
    answers = answers or {}
    filled: dict[str, str] = {}

    # --- Text inputs ---
    text_inputs = page.locator(sel.WIZARD_TEXT_INPUT)
    count = await text_inputs.count()
    for i in range(count):
        inp = text_inputs.nth(i)
        # Try to find the label
        section = page.locator(sel.WIZARD_FORM_SECTION).filter(has=inp)
        label_el = section.locator(sel.WIZARD_FORM_LABEL)
        label_text = ""
        if await label_el.count() > 0:
            label_text = (await label_el.first.inner_text()).strip().lower()

        value = answers.get(label_text, "5")  # default answer
        await inp.fill(value)
        filled[label_text] = value
        logger.debug("Filled text field '%s' = '%s'", label_text, value)

    # --- Select dropdowns ---
    selects = page.locator(sel.WIZARD_SELECT)
    count = await selects.count()
    for i in range(count):
        sel_el = selects.nth(i)
        section = page.locator(sel.WIZARD_FORM_SECTION).filter(has=sel_el)
        label_el = section.locator(sel.WIZARD_FORM_LABEL)
        label_text = ""
        if await label_el.count() > 0:
            label_text = (await label_el.first.inner_text()).strip().lower()

        value = answers.get(label_text, "yes")  # default answer
        # Try to select by value, fall back to selecting first non-empty option
        try:
            await sel_el.select_option(value=value)
        except Exception:
            options = await sel_el.locator("option").all()
            for opt in options:
                val = await opt.get_attribute("value")
                if val:
                    await sel_el.select_option(value=val)
                    value = val
                    break

        filled[label_text] = value
        logger.debug("Selected dropdown '%s' = '%s'", label_text, value)

    return filled


async def detect_challenge(page: Page) -> bool:
    """Return True if a captcha, security challenge, or auth-wall is detected."""
    # Check multiple challenge selectors
    for marker in sel.CHALLENGE_MARKERS:
        el = page.locator(marker)
        if await el.count() > 0:
            logger.warning("Challenge marker found: %s", marker)
            return True

    # Also check if we got redirected to auth-wall or login
    url = page.url
    if any(kw in url for kw in ("/login", "/checkpoint", "/authwall", "challenge")):
        logger.warning("Redirected to challenge/login URL: %s", url)
        return True

    return False

