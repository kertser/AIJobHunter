"""Apply worker — Easy Apply automation via Playwright."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from job_hunter.linkedin import selectors as sel

logger = logging.getLogger("job_hunter.linkedin.apply")


async def apply_to_job(
    *,
    job_url: str,
    resume_path: str | Path,
    dry_run: bool = False,
    headless: bool = True,
    slowmo_ms: int = 0,
    mock: bool = False,
    form_answers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute the Easy Apply wizard for a single job.

    Returns::

        {
            "result": "success" | "dry_run" | "failed" | "blocked",
            "failure_stage": None | "step1" | "step2" | "step3" | ...,
            "form_answers": {...},
            "started_at": datetime,
            "ended_at": datetime,
        }
    """
    from playwright.async_api import async_playwright

    from job_hunter.linkedin.forms import detect_challenge, fill_form_fields, upload_resume

    started_at = datetime.now(timezone.utc)
    result: dict[str, Any] = {
        "result": "failed",
        "failure_stage": None,
        "form_answers": {},
        "started_at": started_at,
        "ended_at": None,
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless, slow_mo=slowmo_ms)
        page = await browser.new_page()

        try:
            # Navigate to the job detail page
            logger.info("Navigating to job: %s", job_url)
            await page.goto(job_url, wait_until="domcontentloaded", timeout=15_000)

            # Check for challenge/captcha
            if await detect_challenge(page):
                logger.warning("Challenge detected! Marking as BLOCKED.")
                result["result"] = "blocked"
                result["failure_stage"] = "challenge"
                return result

            # Click Easy Apply button
            easy_apply_btn = page.locator(sel.EASY_APPLY_BUTTON)
            if await easy_apply_btn.count() == 0:
                logger.warning("No Easy Apply button found on %s", job_url)
                result["failure_stage"] = "no_easy_apply"
                return result

            await easy_apply_btn.click()
            await page.wait_for_timeout(500)

            # ----- Step 1: Resume upload -----
            logger.info("Step 1: Resume upload")
            wizard = page.locator(sel.WIZARD_MODAL)
            if await wizard.count() == 0:
                # The click may have navigated to a new page (mock mode)
                await page.wait_for_load_state("domcontentloaded")

            resume_file = Path(resume_path)
            if resume_file.exists():
                await upload_resume(page, resume_path)

            next_btn = page.locator(sel.WIZARD_NEXT)
            if await next_btn.count() > 0:
                await next_btn.click()
                await page.wait_for_timeout(500)
            else:
                result["failure_stage"] = "step1"
                return result

            # ----- Step 2: Questions -----
            logger.info("Step 2: Answer questions")
            await page.wait_for_load_state("domcontentloaded")

            if await detect_challenge(page):
                result["result"] = "blocked"
                result["failure_stage"] = "challenge"
                return result

            filled = await fill_form_fields(page, form_answers)
            result["form_answers"] = filled

            next_btn = page.locator(sel.WIZARD_NEXT)
            if await next_btn.count() > 0:
                await next_btn.click()
                await page.wait_for_timeout(500)
            else:
                result["failure_stage"] = "step2"
                return result

            # ----- Step 3: Review & Submit -----
            logger.info("Step 3: Review")
            await page.wait_for_load_state("domcontentloaded")

            if await detect_challenge(page):
                result["result"] = "blocked"
                result["failure_stage"] = "challenge"
                return result

            submit_btn = page.locator(sel.WIZARD_SUBMIT)
            if await submit_btn.count() == 0:
                result["failure_stage"] = "step3"
                return result

            if dry_run:
                logger.info("DRY RUN — skipping submit")
                result["result"] = "dry_run"
            else:
                logger.info("Submitting application")
                await submit_btn.click()
                await page.wait_for_timeout(500)
                await page.wait_for_load_state("domcontentloaded")

                # Check for confirmation
                confirmation = page.locator(sel.APPLY_CONFIRMATION)
                if await confirmation.count() > 0:
                    result["result"] = "success"
                    logger.info("Application submitted successfully!")
                else:
                    # Check for challenge after submit
                    if await detect_challenge(page):
                        result["result"] = "blocked"
                        result["failure_stage"] = "challenge_post_submit"
                    else:
                        result["result"] = "success"
                        logger.info("Submit clicked (no confirmation page detected, assuming success)")

        except Exception as exc:
            logger.error("Apply failed: %s", exc)
            result["result"] = "failed"
            result["failure_stage"] = f"exception: {type(exc).__name__}: {exc}"

        finally:
            result["ended_at"] = datetime.now(timezone.utc)
            await browser.close()

    return result

