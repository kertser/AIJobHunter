"""Centralised CSS / XPath selectors for LinkedIn pages.

Keep all selectors here so they can be updated in one place when
LinkedIn changes its DOM.

Each selector constant may be a CSS selector string or a *list* of
strings.  When it's a list, the parse helpers try each one in order
and use the first that matches.
"""

# -------------------------------------------------------------------------
# Job search results page — card containers
# -------------------------------------------------------------------------

# LinkedIn has changed card markup several times.  We try multiple selectors.
JOB_CARD_SELECTORS = [
    # Real LinkedIn (2025-2026 scaffold)
    "li.jobs-search-results__list-item",
    "div.job-card-container",
    # Older real LinkedIn
    "li.ember-view.jobs-search-results__list-item",
    "div.jobs-search-results-list__list-item",
    # Scaffold layout variant
    "li.scaffold-layout__list-item",
    # Our mock HTML
    "div.job-card-container",
]

# Use a single string for backwards compat — parse.py will also try the list
JOB_CARD = "div.job-card-container"

JOB_CARD_TITLE_SELECTORS = [
    # Real LinkedIn (2025-2026)
    "a.job-card-list__title--link strong",
    "a.job-card-list__title--link",
    "a.job-card-container__link strong",
    "a.job-card-container__link",
    # Scaffold / search results
    "a[data-control-name='jobCard_jobTitle'] span",
    "a.job-card-list__title strong",
    "a.job-card-list__title",
    # Fallback — any prominent link inside the card
    ".artdeco-entity-lockup__title a",
    ".artdeco-entity-lockup__title",
]
JOB_CARD_TITLE = "a.job-card-list__title"

JOB_CARD_COMPANY_SELECTORS = [
    # Real LinkedIn
    ".artdeco-entity-lockup__subtitle span",
    ".artdeco-entity-lockup__subtitle",
    "span.job-card-container__primary-description",
    "a.job-card-container__company-name",
    ".job-card-container__primary-description",
]
JOB_CARD_COMPANY = "span.job-card-container__primary-description"

JOB_CARD_LOCATION_SELECTORS = [
    # Real LinkedIn
    ".artdeco-entity-lockup__caption span",
    ".artdeco-entity-lockup__caption",
    "li.job-card-container__metadata-item",
    ".job-card-container__metadata-wrapper li",
]
JOB_CARD_LOCATION = "li.job-card-container__metadata-item"

JOB_CARD_LINK_SELECTORS = [
    "a.job-card-list__title--link",
    "a.job-card-container__link",
    "a.job-card-list__title",
    ".artdeco-entity-lockup__title a",
]
JOB_CARD_LINK = "a.job-card-list__title"

# -------------------------------------------------------------------------
# Job detail page
# -------------------------------------------------------------------------

JOB_DETAIL_DESCRIPTION_SELECTORS = [
    # 2025-2026 LinkedIn
    "div.jobs-description__content",
    "div.jobs-description-content__text",
    "div.show-more-less-html__markup",
    "article.jobs-description__container",
    "#job-details",
    # Scaffold / unified layout
    "div.jobs-box__html-content",
    "div.jobs-description",
    # Generic fallbacks
    "div[class*='description__text']",
    "div[class*='description-content']",
    "section.description",
]
JOB_DETAIL_DESCRIPTION = "div.show-more-less-html__markup"

JOB_DETAIL_TITLE_SELECTORS = [
    "h1.t-24",
    "h1.job-details-jobs-unified-top-card__job-title",
    "h1.jobs-unified-top-card__job-title",
    "h2.t-24.t-bold",
    # Scaffold variants
    "h1[class*='job-title']",
    "h1[class*='topcard__title']",
    "h1",
]
JOB_DETAIL_TITLE = "h1.t-24"

JOB_DETAIL_COMPANY_SELECTORS = [
    "a.topcard__org-name-link",
    "div.job-details-jobs-unified-top-card__company-name a",
    ".jobs-unified-top-card__company-name a",
    ".jobs-unified-top-card__company-name",
    # Scaffold variants
    "a[class*='company-name']",
    "span[class*='company-name']",
]
JOB_DETAIL_COMPANY = "a.topcard__org-name-link"

EASY_APPLY_BUTTON_SELECTORS = [
    # SDUI / 2025-2026 — Easy Apply is often an <a> tag, not a <button>
    "a[data-view-name='job-apply-button']",
    "a[aria-label*='Easy Apply']",
    "a[href*='openSDUIApplyFlow']",
    # aria-label based (most reliable across redesigns)
    "button[aria-label*='Easy Apply']",
    "button[aria-label*='easy apply']",
    "button[aria-label*='Easy apply']",
    # Classic button selectors
    "button.jobs-apply-button",
    "button.jobs-apply-button--top-card",
    "button.jobs-s-apply",
    # SDUI / 2025-2026 redesign variants
    "div[data-job-apply-button]",
    "button[data-job-apply-button]",
    ".jobs-apply-button--top-card",
    ".jobs-s-apply",
    # Any element with Easy Apply text content (data-control-name patterns)
    "[data-control-name='jobdetails_topcard_inapply']",
    "[data-control-name='apply_button']",
    # Broader class-based matches
    "button[class*='jobs-apply-button']",
    "button[class*='apply-button']",
    # Fallback: any button with 'apply' in class
    "button[class*='apply']",
    # Fallback: any clickable element with 'Easy Apply' visible text
    ".artdeco-button--icon-right[aria-label*='Apply']",
]
EASY_APPLY_BUTTON = "button.jobs-apply-button"

# -------------------------------------------------------------------------
# Easy Apply wizard
# -------------------------------------------------------------------------

# The wizard may be a modal overlay OR a full-page SDUI flow depending on
# LinkedIn version.  We try multiple selector patterns.

WIZARD_MODAL_SELECTORS = [
    "div.jobs-easy-apply-modal",
    "div[data-testid='easy-apply-modal']",
    "div.artdeco-modal[role='dialog']",
    "div[role='dialog'][aria-label*='apply' i]",
    "div[role='dialog'][aria-label*='Apply']",
    "div[role='dialog']",
]
WIZARD_MODAL = "div.jobs-easy-apply-modal"

# Next / Continue button — multiple possible labels
WIZARD_NEXT_SELECTORS = [
    "button[aria-label='Continue to next step']",
    "button[aria-label*='Continue']",
    "button[aria-label*='Next']",
    "button[aria-label*='next']",
    "button:has-text('Next')",
    "button:has-text('Continue')",
]
WIZARD_NEXT = "button[aria-label='Continue to next step']"

# Review button
WIZARD_REVIEW_SELECTORS = [
    "button[aria-label='Review your application']",
    "button[aria-label*='Review']",
    "button:has-text('Review')",
]
WIZARD_REVIEW = "button[aria-label='Review your application']"

# Submit button
WIZARD_SUBMIT_SELECTORS = [
    "button[aria-label='Submit application']",
    "button[aria-label*='Submit']",
    "button:has-text('Submit application')",
    "button:has-text('Submit')",
]
WIZARD_SUBMIT = "button[aria-label='Submit application']"

WIZARD_CLOSE_SELECTORS = [
    "button[aria-label='Dismiss']",
    "button[aria-label='Close']",
    "button[aria-label*='Dismiss']",
    "button[aria-label*='Close']",
]
WIZARD_CLOSE = "button[aria-label='Dismiss']"

WIZARD_RESUME_INPUT = "input[type='file']"

# Form field selectors — prefer semantic matching over class-based
WIZARD_TEXT_INPUT_SELECTORS = [
    "div.jobs-easy-apply-form-section input[type='text']",
    "div[role='dialog'] input[type='text']",
    "form input[type='text']",
    "input[type='text']",
]
WIZARD_TEXT_INPUT = "div.jobs-easy-apply-form-section input[type='text']"

WIZARD_SELECT_SELECTORS = [
    "div.jobs-easy-apply-form-section select",
    "div[role='dialog'] select",
    "form select",
    "select",
]
WIZARD_SELECT = "div.jobs-easy-apply-form-section select"

WIZARD_FORM_SECTION_SELECTORS = [
    "div.jobs-easy-apply-form-section",
    "div[role='dialog'] div.fb-dash-form-element",
    "div[role='group']",
    "fieldset",
]
WIZARD_FORM_SECTION = "div.jobs-easy-apply-form-section"
WIZARD_FORM_LABEL = "label"

# Success confirmation
APPLY_CONFIRMATION_SELECTORS = [
    "div.jobs-easy-apply-confirmation",
    "div[data-testid='apply-confirmation']",
    "div[role='dialog'] h2:has-text('application was sent')",
    "div:has-text('Your application was sent')",
]
APPLY_CONFIRMATION = "div.jobs-easy-apply-confirmation"

# Challenge / captcha detection
# NOTE: Do NOT include generic selectors like iframe[src*='captcha'] — LinkedIn
# pages routinely include hidden reCAPTCHA iframes that are never shown to users.
# Only match elements that indicate a *blocking* challenge.
CHALLENGE_MARKER = "div#captcha-internal"
CHALLENGE_MARKERS = [
    "div#captcha-internal",
    "div.challenge-dialog",
    "#captcha-challenge",
    # Full-page security check
    "div.auth-challenge",
    "div#recaptcha-dialog",
]


