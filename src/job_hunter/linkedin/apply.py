"""Apply worker — Easy Apply automation via Playwright."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from job_hunter.linkedin import selectors as sel

logger = logging.getLogger("job_hunter.linkedin.apply")

MAX_WIZARD_STEPS = 10  # Safety limit to avoid infinite loops


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _save_debug_html(page, label: str, *, include_frames: bool = False) -> None:
    """Save current page HTML for debugging.

    *include_frames* controls whether every frame's HTML is also dumped
    (slow — only use for error/final steps).
    """
    try:
        safe_label = label.replace(" ", "_").replace("/", "_")
        html = await page.content()
        debug_path = Path(f"data/debug_apply_{safe_label}.html")
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(html, encoding="utf-8")
        logger.debug("Saved main page HTML (%s) → %s (%d bytes)", label, debug_path, len(html))

        if include_frames:
            for idx, frame in enumerate(page.frames):
                if frame == page.main_frame:
                    continue
                try:
                    frame_html = await frame.content()
                    if len(frame_html) > 500:
                        fp = Path(f"data/debug_apply_{safe_label}_frame{idx}.html")
                        fp.write_text(frame_html, encoding="utf-8")
                except Exception:
                    pass
    except Exception as exc:
        logger.debug("Failed to save debug HTML (%s): %s", label, exc)


async def _find_first_locator(ctx, selectors: list[str], *, visible_only: bool = True):
    """Try multiple CSS selectors on *ctx* (Page or Frame) and return first match."""
    for selector in selectors:
        try:
            loc = ctx.locator(selector)
            count = await loc.count()
            if count > 0:
                if visible_only:
                    for i in range(count):
                        el = loc.nth(i)
                        try:
                            if await el.is_visible():
                                logger.debug("Matched selector: %s (element %d)", selector, i)
                                return el
                        except Exception:
                            continue
                else:
                    logger.debug("Matched selector: %s", selector)
                    return loc.first
        except Exception:
            continue
    return None


async def _find_button_by_text(ctx, texts: list[str]):
    """Find a visible button/link by text content via JS on *ctx* (Page or Frame)."""
    for text in texts:
        try:
            js_result = await ctx.evaluate("""
                (searchText) => {
                    const candidates = document.querySelectorAll('button, a[role="button"], div[role="button"], input[type="submit"]');
                    for (const el of candidates) {
                        const t = (el.textContent || '').trim();
                        if (t.toLowerCase().includes(searchText.toLowerCase()) && el.offsetParent !== null) {
                            if (el.id) return '#' + el.id;
                            const aria = el.getAttribute('aria-label');
                            if (aria) return `[aria-label="${aria}"]`;
                            let sel = el.tagName.toLowerCase();
                            if (el.className && typeof el.className === 'string') {
                                sel += '.' + el.className.trim().split(/\\s+/).join('.');
                            }
                            return sel;
                        }
                    }
                    return null;
                }
            """, text)
            if js_result:
                loc = ctx.locator(js_result).first
                if await loc.count() > 0 and await loc.is_visible():
                    logger.debug("Found button by text '%s': %s", text, js_result)
                    return loc
        except Exception:
            continue
    return None


async def _find_progression_button(ctx):
    """Find the next progression button in the wizard.

    *ctx* can be a Page or a Frame.
    Returns (locator, button_type) where button_type is 'submit', 'review', or 'next'.
    """
    # Priority: Submit > Review > Next
    for selectors, btn_type in [
        (sel.WIZARD_SUBMIT_SELECTORS, "submit"),
        (sel.WIZARD_REVIEW_SELECTORS, "review"),
        (sel.WIZARD_NEXT_SELECTORS, "next"),
    ]:
        loc = await _find_first_locator(ctx, selectors)
        if loc:
            return loc, btn_type

    # JS text fallback
    for texts, btn_type in [
        (["Submit application", "Submit"], "submit"),
        (["Review"], "review"),
        (["Next", "Continue"], "next"),
    ]:
        loc = await _find_button_by_text(ctx, texts)
        if loc:
            return loc, btn_type

    return None, None


async def _get_wizard_context(page):
    """Locate the wizard rendering context.

    LinkedIn's SDUI wraps the classic Ember app inside an iframe.  The Easy
    Apply wizard renders as an ``artdeco-modal`` inside that Ember iframe,
    with content loaded asynchronously into ``#artdeco-modal-outlet``.

    Returns ``(frame_or_page, found)`` — *frame_or_page* is the context
    where wizard operations should run.
    """
    # --- 1. Find the Ember iframe (largest frame / jobs URL) ---
    ember_frame = None
    for frame in page.frames:
        url = frame.url or ""
        if "/jobs/" in url and "linkedin.com" in url:
            ember_frame = frame
            logger.debug("Ember iframe candidate: %s", url[:120])
            break

    # Fallback: interop-iframe
    if ember_frame is None:
        try:
            iframe_el = page.locator("iframe[data-testid='interop-iframe']")
            if await iframe_el.count() > 0:
                ember_frame = await iframe_el.first.content_frame()
                if ember_frame:
                    logger.debug("Found interop-iframe via data-testid")
        except Exception:
            pass

    # Fallback: any frame with /apply/ in URL
    if ember_frame is None:
        for frame in page.frames:
            url = frame.url or ""
            if "/apply" in url:
                ember_frame = frame
                logger.debug("Found apply frame: %s", url[:120])
                break

    if ember_frame:
        # Check if wizard content has loaded inside the modal
        try:
            # The modal outlet must have real content (buttons, forms, etc.)
            modal_outlet = ember_frame.locator("#artdeco-modal-outlet")
            if await modal_outlet.count() > 0:
                inner = await modal_outlet.inner_html()
                if len(inner.strip()) > 100:
                    logger.info("Wizard content found in Ember iframe (#artdeco-modal-outlet: %d chars)", len(inner))
                    return ember_frame, True
                else:
                    logger.debug("Modal outlet exists but is empty/small (%d chars)", len(inner.strip()))
        except Exception as exc:
            logger.debug("Error checking modal outlet: %s", exc)

        # Also check for the modal class on body
        try:
            body_cls = await ember_frame.locator("body").get_attribute("class") or ""
            if "artdeco-modal-is-open" in body_cls:
                logger.debug("Modal is-open class found on Ember iframe body")
                # Even though outlet is empty, the modal framework is active
                # — content may still be loading
        except Exception:
            pass

    # --- 2. Check for a modal dialog on the main page (mock mode) ---
    for modal_sel in sel.WIZARD_MODAL_SELECTORS:
        try:
            loc = page.locator(modal_sel)
            if await loc.count() > 0:
                # Verify there's actual form content inside (not just an empty container)
                inner = await loc.first.inner_html()
                if len(inner.strip()) > 100:
                    logger.info("Wizard modal with content on main page: %s (%d chars)", modal_sel, len(inner))
                    return page, True
                else:
                    logger.debug("Empty modal container on main page: %s", modal_sel)
        except Exception:
            continue

    # --- 3. Page-level flow (URL changed to /apply/) ---
    if "/apply" in page.url:
        logger.info("Wizard at page level — URL: %s", page.url[:120])
        return page, True

    return ember_frame or page, False


async def _wait_for_wizard_content(page, timeout_ms: int = 20_000):
    """Wait for actual wizard form content to appear.

    Probes every second until real wizard UI (buttons, form fields) is found
    in the correct context.  Returns ``(context, found)`` where *context* is
    the Page/Frame containing the wizard.
    """
    import asyncio

    elapsed = 0
    interval = 1000

    while elapsed < timeout_ms:
        ctx, found = await _get_wizard_context(page)
        if found:
            return ctx, True

        # Also check if the Ember frame's modal outlet has populated
        for frame in page.frames:
            url = frame.url or ""
            if "/jobs/" in url and "linkedin.com" in url:
                try:
                    # Wait for any wizard-like element inside the modal
                    for sel_str in [
                        "#artdeco-modal-outlet button",
                        "#artdeco-modal-outlet input",
                        "#artdeco-modal-outlet form",
                        "#artdeco-modal-outlet .jobs-easy-apply-content",
                        "#artdeco-modal-outlet [aria-label*='Continue']",
                        "#artdeco-modal-outlet [aria-label*='Submit']",
                        "#artdeco-modal-outlet [aria-label*='Dismiss']",
                    ]:
                        loc = frame.locator(sel_str)
                        if await loc.count() > 0:
                            logger.info("Wizard content appeared in Ember frame via: %s", sel_str)
                            return frame, True
                except Exception:
                    pass

        elapsed += interval
        await asyncio.sleep(interval / 1000)
        logger.debug("Waiting for wizard content... (%d ms / %d ms)", elapsed, timeout_ms)

    return await _get_wizard_context(page)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def apply_to_job(
    *,
    job_url: str,
    resume_path: str | Path,
    dry_run: bool = False,
    headless: bool = True,
    slowmo_ms: int = 0,
    mock: bool = False,
    form_answers: dict[str, str] | None = None,
    cookies_path: str | Path = "data/cookies.json",
    openai_api_key: str = "",
    user_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute the Easy Apply wizard for a single job.

    *user_profile* is a dict of profile data (from UserProfile.model_dump()).
    If *openai_api_key* and *user_profile* are provided, form fields that
    can't be filled by simple lookup will be answered by an LLM.

    Returns::

        {
            "result": "success" | "dry_run" | "failed" | "blocked",
            "failure_stage": None | "step1" | "step2" | ... | "no_easy_apply",
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

    from job_hunter.linkedin.session import LinkedInSession

    # Initialize LLM form filler if we have the API key and profile
    llm_filler = None
    profile_context = ""
    if openai_api_key and user_profile:
        try:
            from job_hunter.linkedin.form_filler_llm import LLMFormFiller, build_profile_context
            llm_filler = LLMFormFiller(api_key=openai_api_key)
            profile_context = build_profile_context(user_profile)
            logger.info("LLM form filler initialized (profile: %d chars)", len(profile_context))
        except Exception as exc:
            logger.warning("Failed to initialize LLM form filler: %s", exc)

    async with async_playwright() as pw:
        # Use persistent stealth context for real LinkedIn (same profile as
        # discover), fall back to a plain browser for mock mode.
        session = LinkedInSession(cookies_path=cookies_path)
        _using_persistent = False

        if session.has_cookies() and not mock:
            context = await session.launch_stealth_context(
                pw, headless=headless, slowmo_ms=slowmo_ms,
            )
            page = context.pages[0] if context.pages else await context.new_page()
            _using_persistent = True
            logger.info("Using persistent stealth context for authentication")
        else:
            from job_hunter.linkedin.session import _STEALTH_ARGS
            try:
                browser = await pw.chromium.launch(
                    headless=headless, slow_mo=slowmo_ms, channel="chrome",
                    args=_STEALTH_ARGS,
                )
            except Exception:
                browser = await pw.chromium.launch(
                    headless=headless, slow_mo=slowmo_ms,
                    args=_STEALTH_ARGS,
                )
            page = await browser.new_page()
            context = None
            if not mock:
                logger.warning("No saved cookies — page may load as guest")

        try:
            # Navigate to the job detail page
            logger.info("Navigating to job: %s", job_url)
            await page.goto(job_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(3000)

            # Check for challenge/captcha
            if await detect_challenge(page):
                logger.warning("Challenge detected! Marking as BLOCKED.")
                result["result"] = "blocked"
                result["failure_stage"] = "challenge"
                await _save_debug_html(page, "challenge", include_frames=True)
                return result

            # ---- Find and click Easy Apply button ----
            # The button may be on the main SDUI page OR inside the Ember iframe
            easy_apply_btn = None
            btn_ctx = page  # which context the button lives in

            # Try main page first
            easy_apply_btn = await _find_first_locator(page, sel.EASY_APPLY_BUTTON_SELECTORS)
            if easy_apply_btn is None:
                easy_apply_btn = await _find_button_by_text(page, ["Easy Apply"])

            # Try each frame (Ember iframe)
            if easy_apply_btn is None:
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    easy_apply_btn = await _find_first_locator(frame, sel.EASY_APPLY_BUTTON_SELECTORS)
                    if easy_apply_btn is None:
                        easy_apply_btn = await _find_button_by_text(frame, ["Easy Apply"])
                    if easy_apply_btn is not None:
                        btn_ctx = frame
                        logger.info("Easy Apply button found in frame: %s", (frame.url or "")[:80])
                        break

            if easy_apply_btn is None:
                # --- Check for "Already applied" indicators ---
                already_applied = False
                for ctx in [page] + [f for f in page.frames if f != page.main_frame]:
                    try:
                        applied_indicator = await ctx.evaluate("""
                            () => {
                                // Check for common "already applied" UI patterns
                                const indicators = [
                                    // Button text: "Applied", "Submitted", etc.
                                    ...document.querySelectorAll('button, a, span, div'),
                                ].filter(el => {
                                    const t = (el.textContent || '').trim().toLowerCase();
                                    return (
                                        t === 'applied' ||
                                        t === 'submitted' ||
                                        t === 'already applied' ||
                                        t.includes('application submitted') ||
                                        t.includes('you applied') ||
                                        t.includes('application sent')
                                    ) && el.offsetParent !== null;
                                });
                                if (indicators.length > 0) return indicators[0].textContent.trim();
                                // Check aria-labels
                                const ariaEls = document.querySelectorAll('[aria-label*="Applied"], [aria-label*="applied"], [aria-label*="Submitted"]');
                                for (const el of ariaEls) {
                                    if (el.offsetParent !== null) return el.getAttribute('aria-label');
                                }
                                return null;
                            }
                        """)
                        if applied_indicator:
                            already_applied = True
                            logger.info("Already applied indicator found: '%s'", applied_indicator)
                            break
                    except Exception:
                        continue

                if already_applied:
                    logger.info("Job already applied to — skipping: %s", job_url)
                    result["result"] = "already_applied"
                    return result

                await _save_debug_html(page, "no_easy_apply", include_frames=True)
                logger.warning("No Easy Apply button found on %s (URL: %s)", job_url, page.url)
                result["failure_stage"] = "no_easy_apply"
                return result

            logger.info("Clicking Easy Apply button")
            await easy_apply_btn.click()
            await page.wait_for_timeout(3000)

            # ---- Wait for wizard content to actually appear ----
            wizard_ctx, wizard_found = await _wait_for_wizard_content(page)

            if not wizard_found:
                await _save_debug_html(page, "wizard_not_found", include_frames=True)
                logger.warning("Wizard content did not appear after clicking Easy Apply")
                result["failure_stage"] = "wizard_not_found"
                return result

            logger.info("Wizard context found — starting step loop")

            # ---- Adaptive wizard step loop ----
            all_filled: dict[str, str] = {}
            prev_modal_html = ""

            for step in range(1, MAX_WIZARD_STEPS + 1):
                logger.info("Wizard step %d", step)

                # Re-acquire wizard context each step (content may have changed)
                wizard_ctx, _ = await _get_wizard_context(page)

                # Try to scope operations to the modal outlet for Ember frames
                modal_scope = wizard_ctx
                modal_locator = None  # Will be set if we find the outlet via locator
                try:
                    outlet = wizard_ctx.locator("#artdeco-modal-outlet")
                    if await outlet.count() > 0:
                        inner = await outlet.inner_html()
                        if len(inner.strip()) > 50:
                            modal_scope = outlet
                            modal_locator = outlet
                            logger.debug("Scoped to #artdeco-modal-outlet (%d chars)", len(inner))

                            # Save the live DOM content of the modal for debugging
                            try:
                                debug_path = Path(f"data/debug_apply_step{step}_modal.html")
                                debug_path.write_text(inner, encoding="utf-8")
                            except Exception:
                                pass
                except Exception:
                    pass

                # ---- Log what the wizard contains ----
                try:
                    if modal_locator:
                        # Run diagnostics on the actual modal element (locator-scoped)
                        diag = await modal_locator.evaluate("""
                            (outlet) => {
                                const inputs = outlet.querySelectorAll('input:not([type="hidden"])');
                                const selects = outlet.querySelectorAll('select');
                                const textareas = outlet.querySelectorAll('textarea');
                                const buttons = outlet.querySelectorAll('button');
                                const labels = outlet.querySelectorAll('label');
                                const radios = outlet.querySelectorAll('input[type="radio"]');
                                const errors = outlet.querySelectorAll('[class*="error"], [class*="invalid"], [role="alert"]');
                                return {
                                    outlet: true,
                                    outletHTML: outlet.innerHTML.length,
                                    inputs: Array.from(inputs).map(i => ({type: i.type, name: i.name, id: i.id, value: i.value, required: i.required, ariaLabel: i.getAttribute('aria-label')})),
                                    selects: Array.from(selects).map(s => ({name: s.name, id: s.id, ariaLabel: s.getAttribute('aria-label')})),
                                    textareas: Array.from(textareas).map(t => ({name: t.name, id: t.id, ariaLabel: t.getAttribute('aria-label')})),
                                    buttons: Array.from(buttons).map(b => ({text: b.textContent.trim().substring(0,60), ariaLabel: b.getAttribute('aria-label'), disabled: b.disabled, type: b.type})),
                                    labels: Array.from(labels).map(l => l.textContent.trim().substring(0,60)),
                                    radios: radios.length,
                                    errors: Array.from(errors).map(e => e.textContent.trim().substring(0,100)),
                                    headings: Array.from(outlet.querySelectorAll('h1,h2,h3')).map(h => h.textContent.trim().substring(0,80)),
                                };
                            }
                        """)
                    else:
                        diag = {"outlet": False}
                    if diag.get("outlet"):
                        logger.info("Step %d modal: %d chars, %d inputs, %d selects, %d textareas, %d buttons, %d radios",
                                    step, diag["outletHTML"], len(diag["inputs"]), len(diag["selects"]),
                                    len(diag["textareas"]), len(diag["buttons"]), diag.get("radios", 0))
                        if diag["headings"]:
                            logger.info("  Headings: %s", diag["headings"])
                        if diag["errors"]:
                            logger.warning("  Validation errors: %s", diag["errors"])
                    else:
                        logger.warning("Step %d: modal outlet not found for diagnostics", step)
                except Exception as exc:
                    logger.debug("Diagnostics failed: %s", exc)

                # ---- Stuck pre-check (compare modal content) ----
                # We record the HTML now but only declare "stuck" AFTER form filling
                try:
                    cur_modal_html = await modal_scope.inner_html() if hasattr(modal_scope, 'inner_html') else await wizard_ctx.content()
                except Exception:
                    cur_modal_html = ""
                page_unchanged = cur_modal_html and cur_modal_html == prev_modal_html and step > 1
                prev_modal_html = cur_modal_html

                # Check for challenge
                if await detect_challenge(wizard_ctx):
                    logger.warning("Challenge detected at step %d", step)
                    result["result"] = "blocked"
                    result["failure_stage"] = f"challenge_step{step}"
                    return result

                # Upload resume if file input is visible (search in modal scope)
                resume_file = Path(resume_path)
                if resume_file.exists():
                    # Try modal scope first, then whole wizard context
                    for scope in ([modal_scope, wizard_ctx] if modal_scope is not wizard_ctx else [wizard_ctx]):
                        file_input = scope.locator(sel.WIZARD_RESUME_INPUT) if hasattr(scope, 'locator') else wizard_ctx.locator(sel.WIZARD_RESUME_INPUT)
                        if await file_input.count() > 0:
                            try:
                                if await file_input.first.is_visible():
                                    await upload_resume(wizard_ctx, resume_path)
                                    break
                            except Exception:
                                pass

                # Fill form fields — use modal_locator if available (works across frame boundaries)
                # Locator.locator() works for sub-selectors, and Locator.evaluate() runs in element context
                filled: dict[str, str] = {}
                fill_ctx = modal_locator if modal_locator else wizard_ctx
                try:
                    filled = await fill_form_fields(fill_ctx, form_answers)
                    if filled:
                        logger.info("  Filled %d fields: %s", len(filled), filled)
                except Exception as exc:
                    logger.debug("fill_form_fields failed: %s", exc)

                # Always try JS fallback too (handles fields the Playwright approach missed)
                if modal_locator:
                    try:
                        js_filled = await modal_locator.evaluate("""
                            (outlet, answers) => {
                                const filled = {};

                                // Smart label matching
                                function lookupAnswer(label) {
                                    if (!label) return '';
                                    const norm = label.toLowerCase().replace(/[\\n\\r]+/g, ' ').trim();
                                    if (answers[norm]) return answers[norm];
                                    for (const [key, val] of Object.entries(answers)) {
                                        if (norm.includes(key)) return val;
                                    }
                                    const kwMap = {
                                        'first name': ['first name', 'given name'],
                                        'last name': ['last name', 'family name', 'surname'],
                                        'email': ['email', 'e-mail'],
                                        'phone': ['phone', 'mobile', 'cell'],
                                        'country code': ['country code'],
                                        'years of experience': ['years of experience', 'how many years'],
                                    };
                                    for (const [ansKey, kwList] of Object.entries(kwMap)) {
                                        for (const kw of kwList) {
                                            if (norm.includes(kw) && answers[ansKey]) return answers[ansKey];
                                        }
                                    }
                                    return '';
                                }

                                // Fill text, number, tel, email inputs
                                const inputs = outlet.querySelectorAll('input[type="text"]:not([readonly]), input[type="number"]:not([readonly]), input[type="tel"]:not([readonly]), input[type="email"]:not([readonly]), input:not([type]):not([readonly])');
                                inputs.forEach(inp => {
                                    if (inp.offsetParent === null) return;
                                    if (inp.type === 'hidden' || inp.type === 'radio' || inp.type === 'checkbox' || inp.type === 'file') return;
                                    if (inp.value && inp.value.trim()) return;
                                    const label = inp.getAttribute('aria-label') || inp.name || inp.id || '';
                                    let val = lookupAnswer(label);
                                    // For number fields with no answer, provide defaults
                                    if (!val && inp.type === 'number') {
                                        const norm = label.toLowerCase();
                                        if (norm.includes('year') || norm.includes('experience') || norm.includes('how many') || norm.includes('how long')) {
                                            val = answers['years of experience'] || '10';
                                        } else {
                                            val = '5';
                                        }
                                    }
                                    if (val) {
                                        const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                                        nativeSetter.call(inp, val);
                                        inp.dispatchEvent(new Event('input', {bubbles: true}));
                                        inp.dispatchEvent(new Event('change', {bubbles: true}));
                                        filled[label] = val;
                                    }
                                });

                                // Fill selects
                                const selects = outlet.querySelectorAll('select');
                                selects.forEach(sel => {
                                    if (sel.offsetParent === null) return;
                                    if (sel.selectedIndex > 0) return;
                                    const label = sel.getAttribute('aria-label') || sel.name || sel.id || '';
                                    const desired = lookupAnswer(label);
                                    let found = false;
                                    if (desired) {
                                        for (const opt of sel.options) {
                                            if (opt.value && opt.text.toLowerCase().includes(desired.toLowerCase())) {
                                                sel.value = opt.value;
                                                found = true;
                                                break;
                                            }
                                        }
                                    }
                                    if (!found) {
                                        for (const opt of sel.options) {
                                            if (opt.value && opt.text.trim() && !opt.text.toLowerCase().includes('select')) {
                                                sel.value = opt.value;
                                                found = true;
                                                break;
                                            }
                                        }
                                    }
                                    if (found) {
                                        sel.dispatchEvent(new Event('change', {bubbles: true}));
                                        filled[label] = sel.options[sel.selectedIndex]?.text || sel.value;
                                    }
                                });

                                return filled;
                            }
                        """, form_answers or {})
                        if js_filled:
                            filled.update(js_filled)
                            logger.info("  JS-filled %d fields: %s", len(js_filled), js_filled)
                    except Exception as exc:
                        logger.debug("JS form fill failed: %s", exc)

                # --- Playwright-based radio click (Ember needs real user-like clicks) ---
                if modal_locator:
                    try:
                        unchecked = await modal_locator.evaluate("""
                            (root) => {
                                const radios = root.querySelectorAll('input[type="radio"]');
                                const groups = {};
                                radios.forEach(r => {
                                    if (!r.name) return;
                                    if (!groups[r.name]) groups[r.name] = [];
                                    groups[r.name].push({
                                        id: r.id,
                                        checked: r.checked,
                                        dataLabel: r.getAttribute('data-test-text-selectable-option__input') || '',
                                    });
                                });
                                const result = [];
                                for (const [name, opts] of Object.entries(groups)) {
                                    if (opts.some(o => o.checked)) continue;
                                    const yesOpt = opts.find(o => o.dataLabel.toLowerCase() === 'yes');
                                    result.push({
                                        name: name,
                                        targetId: yesOpt ? yesOpt.id : (opts[0] ? opts[0].id : null),
                                        label: yesOpt ? 'Yes' : 'first',
                                    });
                                }
                                return result;
                            }
                        """)
                        if unchecked:
                            logger.info("Found %d unchecked radio groups, clicking via Playwright", len(unchecked))
                            for group in unchecked:
                                target_id = group.get("targetId")
                                if not target_id:
                                    continue
                                try:
                                    # Click the label (Ember reacts to label clicks)
                                    label_loc = wizard_ctx.locator(f"label[for='{target_id}']")
                                    if await label_loc.count() > 0:
                                        await label_loc.first.click()
                                        filled[f"radio:{group['name'][:40]}"] = group["label"]
                                        logger.info("  Clicked radio label '%s' for group %s",
                                                    group["label"], group["name"][:50])
                                    else:
                                        # Fallback: click the input directly
                                        inp_loc = wizard_ctx.locator(f"#{target_id}")
                                        if await inp_loc.count() > 0:
                                            await inp_loc.first.click()
                                            filled[f"radio:{group['name'][:40]}"] = group["label"]
                                            logger.info("  Clicked radio input '%s' for group %s",
                                                        group["label"], group["name"][:50])
                                except Exception as exc:
                                    logger.debug("Failed to click radio for %s: %s", group.get("name", "?")[:30], exc)
                        else:
                            logger.debug("No unchecked radio groups found")
                    except Exception as exc:
                        logger.debug("Playwright radio click failed: %s", exc)

                all_filled.update(filled)

                # ---- LLM-based form filling for remaining unfilled fields ----
                if llm_filler and modal_locator:
                    try:
                        # Extract unfilled fields from the modal
                        unfilled = await modal_locator.evaluate("""
                            (outlet) => {
                                const fields = [];

                                function getLabel(el) {
                                    var lbl = el.getAttribute('aria-label');
                                    if (lbl) return lbl.trim();
                                    if (el.id) {
                                        var labelEl = outlet.querySelector('label[for="' + el.id + '"]');
                                        if (labelEl && labelEl.textContent) return labelEl.textContent.trim();
                                    }
                                    return el.name || '';
                                }

                                var inputs = outlet.querySelectorAll(
                                    'input[type="text"], input[type="number"], input[type="tel"], input[type="email"]');
                                for (var i = 0; i < inputs.length; i++) {
                                    var inp = inputs[i];
                                    if (inp.offsetParent === null) continue;
                                    if (inp.value && inp.value.trim()) continue;
                                    var label = getLabel(inp);
                                    if (label) fields.push({label: label, type: inp.type || 'text', id: inp.id || '', required: inp.required});
                                }

                                var selects = outlet.querySelectorAll('select');
                                for (var j = 0; j < selects.length; j++) {
                                    var sel = selects[j];
                                    if (sel.offsetParent === null) continue;
                                    if (sel.selectedIndex > 0) continue;
                                    var slabel = getLabel(sel);
                                    var options = [];
                                    for (var k = 0; k < sel.options.length; k++) {
                                        var o = sel.options[k];
                                        if (o.value && o.text.trim() && o.text.toLowerCase().indexOf('select') === -1) {
                                            options.push(o.text.trim());
                                        }
                                    }
                                    if (slabel) fields.push({label: slabel, type: 'select', id: sel.id || '', options: options, required: sel.required});
                                }

                                var radios = outlet.querySelectorAll('input[type="radio"]');
                                var groups = {};
                                for (var ri = 0; ri < radios.length; ri++) {
                                    var r = radios[ri];
                                    if (!r.name) continue;
                                    if (!groups[r.name]) groups[r.name] = {checked: false, options: []};
                                    if (r.checked) groups[r.name].checked = true;
                                    var optLabel = r.getAttribute('data-test-text-selectable-option__input') || '';
                                    if (!optLabel && r.id) {
                                        var rl = outlet.querySelector('label[for="' + r.id + '"]');
                                        if (rl && rl.textContent) optLabel = rl.textContent.trim();
                                    }
                                    if (optLabel) groups[r.name].options.push(optLabel.trim());
                                }
                                var gnames = Object.keys(groups);
                                for (var gi = 0; gi < gnames.length; gi++) {
                                    var gname = gnames[gi];
                                    var g = groups[gname];
                                    if (g.checked) continue;
                                    var firstR = outlet.querySelector('input[type="radio"][name="' + gname + '"]');
                                    var fieldset = firstR ? firstR.closest('fieldset') : null;
                                    var legend = gname;
                                    if (fieldset) {
                                        var legEl = fieldset.querySelector('legend, span.fb-form-element-label');
                                        if (legEl && legEl.textContent) legend = legEl.textContent.trim();
                                    }
                                    fields.push({label: legend, type: 'radio', name: gname, options: g.options});
                                }

                                var textareas = outlet.querySelectorAll('textarea');
                                for (var ti = 0; ti < textareas.length; ti++) {
                                    var ta = textareas[ti];
                                    if (ta.offsetParent === null) continue;
                                    if (ta.value && ta.value.trim()) continue;
                                    var tlabel = getLabel(ta);
                                    if (tlabel) fields.push({label: tlabel, type: 'textarea', id: ta.id || '', required: ta.required});
                                }

                                return fields;
                            }
                        """)

                        if unfilled:
                            logger.info("LLM form filler: %d unfilled fields detected", len(unfilled))
                            llm_answers = llm_filler.answer_fields(
                                fields=unfilled,
                                profile_context=profile_context,
                                job_context=f"Job URL: {job_url}",
                            )
                            if llm_answers:
                                logger.info("LLM answered %d fields: %s", len(llm_answers),
                                            {k[:50]: v[:50] for k, v in llm_answers.items()})

                                # Fill the LLM answers back into the form
                                llm_fill_result = await modal_locator.evaluate("""
                                    (outlet, answers) => {
                                        var filled = {};

                                        function getLabel(el) {
                                            var lbl = el.getAttribute('aria-label');
                                            if (lbl) return lbl.trim();
                                            if (el.id) {
                                                var labelEl = outlet.querySelector('label[for="' + el.id + '"]');
                                                if (labelEl && labelEl.textContent) return labelEl.textContent.trim();
                                            }
                                            return el.name || '';
                                        }

                                        var inputs = outlet.querySelectorAll(
                                            'input[type="text"], input[type="number"], input[type="tel"], input[type="email"]');
                                        for (var i = 0; i < inputs.length; i++) {
                                            var inp = inputs[i];
                                            if (inp.offsetParent === null) continue;
                                            if (inp.value && inp.value.trim()) continue;
                                            var label = getLabel(inp);
                                            var val = answers[label];
                                            if (val) {
                                                var setter = Object.getOwnPropertyDescriptor(
                                                    window.HTMLInputElement.prototype, 'value').set;
                                                setter.call(inp, val);
                                                inp.dispatchEvent(new Event('input', {bubbles: true}));
                                                inp.dispatchEvent(new Event('change', {bubbles: true}));
                                                filled[label] = val;
                                            }
                                        }

                                        var selects = outlet.querySelectorAll('select');
                                        for (var j = 0; j < selects.length; j++) {
                                            var sel = selects[j];
                                            if (sel.offsetParent === null) continue;
                                            if (sel.selectedIndex > 0) continue;
                                            var slabel = getLabel(sel);
                                            var desired = answers[slabel];
                                            if (!desired) continue;
                                            for (var k = 0; k < sel.options.length; k++) {
                                                var opt = sel.options[k];
                                                if (opt.value && opt.text.toLowerCase().indexOf(desired.toLowerCase()) >= 0) {
                                                    sel.value = opt.value;
                                                    sel.dispatchEvent(new Event('change', {bubbles: true}));
                                                    filled[slabel] = opt.text.trim();
                                                    break;
                                                }
                                            }
                                        }

                                        var textareas = outlet.querySelectorAll('textarea');
                                        for (var ti = 0; ti < textareas.length; ti++) {
                                            var ta = textareas[ti];
                                            if (ta.offsetParent === null) continue;
                                            if (ta.value && ta.value.trim()) continue;
                                            var tlabel = getLabel(ta);
                                            var tval = answers[tlabel];
                                            if (tval) {
                                                var tsetter = Object.getOwnPropertyDescriptor(
                                                    window.HTMLTextAreaElement.prototype, 'value').set;
                                                tsetter.call(ta, tval);
                                                ta.dispatchEvent(new Event('input', {bubbles: true}));
                                                ta.dispatchEvent(new Event('change', {bubbles: true}));
                                                filled[tlabel] = tval;
                                            }
                                        }

                                        return filled;
                                    }
                                """, llm_answers)

                                if llm_fill_result:
                                    filled.update(llm_fill_result)
                                    all_filled.update(llm_fill_result)
                                    logger.info("  LLM JS-filled %d fields: %s", len(llm_fill_result), llm_fill_result)

                                # For radio buttons answered by LLM, click via Playwright
                                for field_info in unfilled:
                                    if field_info.get("type") != "radio":
                                        continue
                                    label = field_info.get("label", "")
                                    answer = llm_answers.get(label)
                                    name = field_info.get("name", "")
                                    if not answer or not name:
                                        continue
                                    try:
                                        # Find the radio with matching label via JS
                                        radio_clicked = await modal_locator.evaluate("""
                                            (outlet, args) => {
                                                var group = outlet.querySelectorAll('input[type="radio"][name="' + args.name + '"]');
                                                for (var i = 0; i < group.length; i++) {
                                                    var r = group[i];
                                                    var dl = r.getAttribute('data-test-text-selectable-option__input') || '';
                                                    var lbl = '';
                                                    if (r.id) {
                                                        var labelEl = outlet.querySelector('label[for="' + r.id + '"]');
                                                        if (labelEl && labelEl.textContent) lbl = labelEl.textContent.trim();
                                                    }
                                                    if (dl.toLowerCase() === args.answer.toLowerCase() ||
                                                        (lbl && lbl.toLowerCase() === args.answer.toLowerCase())) {
                                                        r.click();
                                                        return dl || lbl || 'clicked';
                                                    }
                                                }
                                                if (group.length > 0) { group[0].click(); return 'first'; }
                                                return null;
                                            }
                                        """, {"name": name, "answer": answer})
                                        if radio_clicked:
                                            filled[f"radio:{name[:40]}"] = answer
                                            all_filled[f"radio:{name[:40]}"] = answer
                                            # Also click via Playwright for Ember
                                            target_radio = wizard_ctx.locator(f"input[type='radio'][name='{name}']").first
                                            if await target_radio.count() > 0:
                                                target_id = await target_radio.get_attribute("id")
                                                if target_id:
                                                    lbl = wizard_ctx.locator(f"label[for='{target_id}']")
                                                    if await lbl.count() > 0:
                                                        await lbl.first.click()
                                    except Exception as exc:
                                        logger.debug("LLM radio click for '%s' failed: %s", label[:40], exc)

                    except Exception as exc:
                        logger.warning("LLM form filling failed: %s", exc)

                # ---- Final stuck check ----
                # If the page didn't change from prev step AND we didn't fill any new fields,
                # we're truly stuck (validation errors we can't fix).
                if page_unchanged and not filled:
                    logger.warning("Wizard stuck at step %d — page unchanged and no fields filled!", step)
                    result["failure_stage"] = f"stuck_step{step}"
                    result["form_answers"] = all_filled
                    return result
                elif page_unchanged and filled:
                    logger.info("Page unchanged but filled %d fields — retrying progression", len(filled))

                # Find progression button — use fast JS approach first
                btn, btn_type = None, None

                # Fast JS-based button search in the modal
                if modal_locator:
                    try:
                        btn_info = await modal_locator.evaluate("""
                            (outlet) => {
                                const buttons = outlet.querySelectorAll('button');
                                const priorities = [
                                    {type: 'submit', patterns: ['submit application', 'submit']},
                                    {type: 'review', patterns: ['review your application', 'review']},
                                    {type: 'next', patterns: ['continue to next step', 'continue', 'next']},
                                ];
                                for (const {type, patterns} of priorities) {
                                    for (const btn of buttons) {
                                        if (btn.offsetParent === null || btn.disabled) continue;
                                        const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
                                        const text = (btn.textContent || '').trim().toLowerCase();
                                        for (const p of patterns) {
                                            if (aria.includes(p) || text === p || text.includes(p)) {
                                                if (btn.id) return {id: btn.id, type: type};
                                                if (aria) return {aria: btn.getAttribute('aria-label'), type: type};
                                                return {text: btn.textContent.trim(), type: type};
                                            }
                                        }
                                    }
                                }
                                return null;
                            }
                        """)
                        if btn_info:
                            btn_type = btn_info["type"]
                            if "id" in btn_info:
                                btn = wizard_ctx.locator(f"#{btn_info['id']}")
                            elif "aria" in btn_info:
                                btn = wizard_ctx.locator(f"button[aria-label='{btn_info['aria']}']").first
                            elif "text" in btn_info:
                                btn = wizard_ctx.locator(f"button:has-text('{btn_info['text']}')").first
                            if btn and await btn.count() == 0:
                                btn = None
                    except Exception:
                        pass

                # Fallback to Playwright-based search if JS didn't find it
                if btn is None:
                    for search_ctx in ([modal_scope, wizard_ctx] if modal_scope is not wizard_ctx else [wizard_ctx]):
                        btn, btn_type = await _find_progression_button(search_ctx)
                        if btn is not None:
                            break

                if btn is None:
                    # Check for success text
                    try:
                        body_text = await wizard_ctx.evaluate("() => document.body?.innerText || ''")
                        if any(p in body_text.lower() for p in [
                            "application was sent", "application submitted",
                            "you applied", "successfully submitted",
                        ]):
                            result["result"] = "success"
                            result["form_answers"] = all_filled
                            logger.info("Application appears submitted (confirmation text found)")
                            return result
                    except Exception:
                        pass

                    await _save_debug_html(page, f"step{step}_no_button", include_frames=True)
                    logger.warning("No progression button found at step %d", step)
                    result["failure_stage"] = f"step{step}"
                    result["form_answers"] = all_filled
                    return result

                if btn_type == "submit":
                    if dry_run:
                        logger.info("DRY RUN — skipping submit at step %d", step)
                        result["result"] = "dry_run"
                        result["form_answers"] = all_filled
                        return result

                    logger.info("Submitting application at step %d", step)
                    await btn.click()
                    await page.wait_for_timeout(3000)

                    # Check confirmation
                    confirmed = False
                    try:
                        wc, _ = await _get_wizard_context(page)
                        body_text = await wc.evaluate("() => document.body?.innerText || ''")
                        if any(p in body_text.lower() for p in [
                            "application was sent", "application submitted",
                            "you applied", "successfully submitted",
                        ]):
                            confirmed = True
                    except Exception:
                        pass

                    if not confirmed:
                        for conf_sel in sel.APPLY_CONFIRMATION_SELECTORS:
                            try:
                                loc = page.locator(conf_sel)
                                if await loc.count() > 0:
                                    confirmed = True
                                    break
                            except Exception:
                                continue

                    if confirmed:
                        result["result"] = "success"
                        logger.info("Application submitted successfully!")
                    elif await detect_challenge(page):
                        result["result"] = "blocked"
                        result["failure_stage"] = "challenge_post_submit"
                    else:
                        result["result"] = "success"
                        logger.info("Submit clicked (assuming success)")

                    await _save_debug_html(page, "after_submit", include_frames=True)
                    result["form_answers"] = all_filled
                    return result
                else:
                    logger.info("Clicking '%s' button at step %d", btn_type, step)
                    await btn.click()
                    await page.wait_for_timeout(1500)

            # Exhausted steps
            logger.warning("Exhausted max wizard steps (%d)", MAX_WIZARD_STEPS)
            result["failure_stage"] = "max_steps_exceeded"
            result["form_answers"] = all_filled

        except Exception as exc:
            logger.error("Apply failed: %s", exc)
            result["result"] = "failed"
            result["failure_stage"] = f"exception: {type(exc).__name__}: {exc}"
            try:
                await _save_debug_html(page, "exception", include_frames=True)
            except Exception:
                pass

        finally:
            result["ended_at"] = datetime.now(timezone.utc)
            if _using_persistent:
                await context.close()
            else:
                await browser.close()

    return result

