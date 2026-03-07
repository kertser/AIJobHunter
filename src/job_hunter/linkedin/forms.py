"""Form-filling helpers for the Easy Apply wizard."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Union

from playwright.async_api import Frame, Page

from job_hunter.linkedin import selectors as sel

logger = logging.getLogger("job_hunter.linkedin.forms")

# Page and Frame share the same locator/evaluate API
PageOrFrame = Union[Page, Frame]


async def upload_resume(page: PageOrFrame, resume_path: str | Path) -> None:
    """Upload a resume file via the file input in the wizard."""
    file_input = page.locator(sel.WIZARD_RESUME_INPUT)
    if await file_input.count() > 0:
        try:
            await file_input.set_input_files(str(resume_path))
            logger.info("Uploaded resume: %s", Path(resume_path).name)
        except Exception as exc:
            logger.warning("Failed to upload resume: %s", exc)
    else:
        logger.debug("No file input found — resume may already be attached")


async def _find_first_matching(page: PageOrFrame, selectors: list[str]):
    """Try multiple selectors and return first matching locator."""
    for selector in selectors:
        try:
            loc = page.locator(selector)
            if await loc.count() > 0:
                return loc
        except Exception:
            continue
    return None


async def fill_form_fields(page: PageOrFrame, answers: dict[str, str] | None = None) -> dict[str, str]:
    """Fill text inputs and selects in the current wizard step.

    *answers* maps lowercase label text → value. If a question is not in
    *answers* a sensible default is used.

    Returns the dict of answers that were actually filled.
    """
    answers = answers or {}
    filled: dict[str, str] = {}

    def _lookup_answer(label: str, default: str = "") -> str:
        """Smart lookup: exact match, then substring match on label keywords."""
        if not label:
            return default
        # Normalize: collapse whitespace/newlines, lowercase
        norm = " ".join(label.lower().split())
        # 1. Exact match
        if norm in answers:
            return answers[norm]
        # 2. Check if any answer key is contained in the label
        for key, val in answers.items():
            if key in norm:
                return val
        # 3. Check if the label is contained in any answer key
        for key, val in answers.items():
            if norm in key:
                return val
        # 4. Keyword-based matching
        keywords_map = {
            "first name": ["first name", "given name", "first_name"],
            "last name": ["last name", "family name", "surname", "last_name"],
            "email": ["email", "e-mail"],
            "phone": ["phone", "mobile", "cell", "telephone"],
            "country code": ["country code", "phone code"],
            "city": ["city", "location"],
            "headline": ["headline", "current title", "job title"],
            "years of experience": ["years of experience", "experience years", "how many years"],
        }
        for answer_key, kw_list in keywords_map.items():
            for kw in kw_list:
                if kw in norm and answer_key in answers:
                    return answers[answer_key]
        return default

    # --- Text/number/tel/email inputs --- collect ALL fillable inputs
    seen_input_ids: set[str] = set()
    for input_selector in sel.WIZARD_TEXT_INPUT_SELECTORS:
        try:
            text_inputs = page.locator(input_selector)
            count = await text_inputs.count()
            if count == 0:
                continue
        except Exception:
            continue

        for i in range(count):
            inp = text_inputs.nth(i)
            try:
                if not await inp.is_visible():
                    continue
            except Exception:
                continue

            # Deduplicate by input id
            try:
                input_id = await inp.get_attribute("id") or ""
                if input_id and input_id in seen_input_ids:
                    continue
                if input_id:
                    seen_input_ids.add(input_id)
            except Exception:
                pass

            # Get input type for type-aware handling
            try:
                input_type = (await inp.get_attribute("type") or "text").lower()
            except Exception:
                input_type = "text"

            # Skip if field already has a value
            try:
                existing_val = await inp.input_value()
                if existing_val and existing_val.strip():
                    continue
            except Exception:
                pass

            # Try to find the label — try multiple strategies
            label_text = ""
            # Strategy 1: aria-label attribute
            try:
                aria = await inp.get_attribute("aria-label")
                if aria:
                    label_text = aria.strip().lower()
            except Exception:
                pass

            # Strategy 2: associated <label> via for/id
            if not label_text:
                try:
                    inp_id = await inp.get_attribute("id")
                    if inp_id:
                        label_el = page.locator(f"label[for='{inp_id}']")
                        if await label_el.count() > 0:
                            label_text = (await label_el.first.inner_text()).strip().lower()
                except Exception:
                    pass

            # Strategy 3: closest form section with label
            if not label_text:
                for section_sel in sel.WIZARD_FORM_SECTION_SELECTORS:
                    try:
                        section = page.locator(section_sel).filter(has=inp)
                        label_el = section.locator(sel.WIZARD_FORM_LABEL)
                        if await label_el.count() > 0:
                            label_text = (await label_el.first.inner_text()).strip().lower()
                            if label_text:
                                break
                    except Exception:
                        continue

            # Strategy 4: placeholder text
            if not label_text:
                try:
                    placeholder = await inp.get_attribute("placeholder")
                    if placeholder:
                        label_text = placeholder.strip().lower()
                except Exception:
                    pass

            value = _lookup_answer(label_text)
            if not value:
                # For number fields with no specific answer, provide a sensible default
                if input_type == "number":
                    # Check if it's asking for years of experience
                    if any(kw in label_text for kw in ["year", "experience", "how many", "how long"]):
                        value = str(answers.get("years of experience", "10"))
                    else:
                        value = "5"  # safe numeric default
                else:
                    logger.debug("No answer for %s field '%s' — skipping", input_type, label_text)
                    continue

            try:
                await inp.fill(value)
                filled[label_text] = value
                logger.info("Filled %s field '%s' = '%s'", input_type, label_text, value)
            except Exception as exc:
                logger.debug("Failed to fill %s field '%s': %s", input_type, label_text, exc)

    # --- Select dropdowns --- try multiple selector strategies
    selects = await _find_first_matching(page, sel.WIZARD_SELECT_SELECTORS)
    if selects is not None:
        count = await selects.count()
        for i in range(count):
            sel_el = selects.nth(i)
            try:
                if not await sel_el.is_visible():
                    continue
            except Exception:
                continue

            # Skip if dropdown already has a valid selection (not the placeholder)
            try:
                cur_val = await sel_el.input_value()
                if cur_val and cur_val.strip() and cur_val != "Select an option":
                    continue
            except Exception:
                pass

            label_text = ""
            # Strategy 1: aria-label
            try:
                aria = await sel_el.get_attribute("aria-label")
                if aria:
                    label_text = aria.strip().lower()
            except Exception:
                pass

            # Strategy 2: associated <label>
            if not label_text:
                try:
                    sel_id = await sel_el.get_attribute("id")
                    if sel_id:
                        label_el = page.locator(f"label[for='{sel_id}']")
                        if await label_el.count() > 0:
                            label_text = (await label_el.first.inner_text()).strip().lower()
                except Exception:
                    pass

            # Strategy 3: parent section label
            if not label_text:
                for section_sel in sel.WIZARD_FORM_SECTION_SELECTORS:
                    try:
                        section = page.locator(section_sel).filter(has=sel_el)
                        label_el = section.locator(sel.WIZARD_FORM_LABEL)
                        if await label_el.count() > 0:
                            label_text = (await label_el.first.inner_text()).strip().lower()
                            if label_text:
                                break
                    except Exception:
                        continue

            desired_value = _lookup_answer(label_text)

            # Try multiple strategies to select the right option
            selected = False

            # Strategy A: select by value if we have a specific answer
            if desired_value:
                try:
                    await sel_el.select_option(value=desired_value)
                    selected = True
                except Exception:
                    pass

            # Strategy B: match option text containing our desired value
            if not selected and desired_value:
                try:
                    options = await sel_el.locator("option").all()
                    for opt in options:
                        opt_text = (await opt.inner_text()).strip()
                        opt_val = await opt.get_attribute("value")
                        if opt_val and desired_value.lower() in opt_text.lower():
                            await sel_el.select_option(value=opt_val)
                            desired_value = opt_text
                            selected = True
                            break
                except Exception:
                    pass

            # Strategy C: for email/phone dropdowns, select first non-placeholder
            if not selected:
                try:
                    options = await sel_el.locator("option").all()
                    for opt in options:
                        val = await opt.get_attribute("value")
                        text = (await opt.inner_text()).strip()
                        # Skip empty/placeholder options
                        if val and text and text.lower() not in ("select an option", "select", "--", ""):
                            await sel_el.select_option(value=val)
                            desired_value = text
                            selected = True
                            break
                except Exception as exc:
                    logger.debug("Failed to select option for '%s': %s", label_text, exc)

            if selected:
                filled[label_text] = desired_value
                logger.info("Selected dropdown '%s' = '%s'", label_text, desired_value)

    # --- Radio buttons / checkboxes ---
    # Use a single JS call for speed (each Playwright round-trip is slow in iframes)
    try:
        # When page is a Locator, evaluate() passes the matched element as first arg.
        # When page is a Frame/Page, evaluate() gets no args — we use document as root.
        is_locator = hasattr(page, 'evaluate') and not hasattr(page, 'goto')
        if is_locator:
            radio_results = await page.evaluate("""
                (root) => {
                    const radios = root.querySelectorAll('input[type="radio"]');
                    if (radios.length === 0) return {count: 0, filled: {}};

                    const filled = {};
                    const seenGroups = new Set();

                    radios.forEach(radio => {
                        const name = radio.name;
                        if (!name || seenGroups.has(name)) return;

                        const group = root.querySelectorAll('input[type="radio"][name="' + name + '"]');
                        let hasSelection = false;
                        group.forEach(r => { if (r.checked) hasSelection = true; });
                        if (hasSelection) { seenGroups.add(name); return; }
                        seenGroups.add(name);

                        let clicked = false;
                        group.forEach(r => {
                            if (clicked) return;
                            const dataLabel = r.getAttribute('data-test-text-selectable-option__input') || '';
                            if (dataLabel.toLowerCase() === 'yes') {
                                r.click();
                                filled['radio:' + name.substring(0, 50)] = 'Yes';
                                clicked = true;
                            }
                        });
                        if (!clicked) {
                            group.forEach(r => {
                                if (clicked) return;
                                const lbl = r.id ? root.querySelector('label[for="' + r.id + '"]') : null;
                                if (lbl && lbl.textContent.trim().toLowerCase() === 'yes') {
                                    r.click();
                                    filled['radio:' + name.substring(0, 50)] = 'Yes';
                                    clicked = true;
                                }
                            });
                        }
                        if (!clicked && group.length > 0) {
                            group[0].click();
                            filled['radio:' + name.substring(0, 50)] = 'first_option';
                        }
                    });

                    return {count: radios.length, filled: filled};
                }
            """)
        else:
            radio_results = await page.evaluate("""
                () => {
                    const root = document.getElementById('artdeco-modal-outlet')
                                 || document.querySelector('div[role="dialog"]')
                                 || document;
                    const radios = root.querySelectorAll('input[type="radio"]');
                    if (radios.length === 0) return {count: 0, filled: {}};

                    const filled = {};
                    const seenGroups = new Set();

                    radios.forEach(radio => {
                        const name = radio.name;
                        if (!name || seenGroups.has(name)) return;

                        const group = root.querySelectorAll('input[type="radio"][name="' + name + '"]');
                        let hasSelection = false;
                        group.forEach(r => { if (r.checked) hasSelection = true; });
                        if (hasSelection) { seenGroups.add(name); return; }
                        seenGroups.add(name);

                        let clicked = false;
                        group.forEach(r => {
                            if (clicked) return;
                            const dataLabel = r.getAttribute('data-test-text-selectable-option__input') || '';
                            if (dataLabel.toLowerCase() === 'yes') {
                                r.click();
                                filled['radio:' + name.substring(0, 50)] = 'Yes';
                                clicked = true;
                            }
                        });
                        if (!clicked) {
                            group.forEach(r => {
                                if (clicked) return;
                                const lbl = r.id ? root.querySelector('label[for="' + r.id + '"]') : null;
                                if (lbl && lbl.textContent.trim().toLowerCase() === 'yes') {
                                    r.click();
                                    filled['radio:' + name.substring(0, 50)] = 'Yes';
                                    clicked = true;
                                }
                            });
                        }
                        if (!clicked && group.length > 0) {
                            group[0].click();
                            filled['radio:' + name.substring(0, 50)] = 'first_option';
                        }
                    });

                    return {count: radios.length, filled: filled};
                }
            """)
        if radio_results and radio_results.get("count", 0) > 0:
            logger.info("Radio buttons: %d found, %d groups filled",
                        radio_results["count"], len(radio_results.get("filled", {})))
            for k, v in radio_results.get("filled", {}).items():
                filled[k] = v
                logger.info("  %s = %s", k, v)
    except Exception as exc:
        logger.debug("Radio button JS handling failed: %s", exc)

    return filled


async def detect_challenge(page: PageOrFrame) -> bool:
    """Return True if a visible captcha, security challenge, or auth-wall is detected.

    Only flags *blocking* challenges — hidden/preloaded captcha iframes are ignored.
    """
    # Check multiple challenge selectors — only if visible
    for marker in sel.CHALLENGE_MARKERS:
        el = page.locator(marker)
        if await el.count() > 0:
            # Check if at least one matching element is actually visible
            try:
                visible = await el.first.is_visible()
            except Exception:
                visible = False
            if visible:
                logger.warning("Visible challenge detected: %s", marker)
                return True
            else:
                logger.debug("Hidden challenge element found (not blocking): %s", marker)

    # Also check if we got redirected to auth-wall or login
    url = page.url
    if any(kw in url for kw in ("/login", "/checkpoint", "/authwall")):
        logger.warning("Redirected to challenge/login URL: %s", url)
        return True

    # Check page title for challenge indicators
    try:
        title = await page.title()
        if title and any(kw in title.lower() for kw in ("security verification", "captcha", "challenge")):
            logger.warning("Challenge page detected via title: '%s'", title)
            return True
    except Exception:
        pass

    return False

