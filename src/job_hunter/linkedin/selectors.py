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

JOB_DETAIL_TITLE_SELECTORS = [
    "h1.t-24",
    "h1.job-details-jobs-unified-top-card__job-title",
    "h1.jobs-unified-top-card__job-title",
    "h2.t-24.t-bold",
]
JOB_DETAIL_TITLE = "h1.t-24"

JOB_DETAIL_COMPANY_SELECTORS = [
    "a.topcard__org-name-link",
    "div.job-details-jobs-unified-top-card__company-name a",
    ".jobs-unified-top-card__company-name a",
    ".jobs-unified-top-card__company-name",
]
JOB_DETAIL_COMPANY = "a.topcard__org-name-link"

JOB_DETAIL_DESCRIPTION_SELECTORS = [
    "div.show-more-less-html__markup",
    "div.jobs-description-content__text",
    "div.jobs-description__content",
    "article.jobs-description__container",
    "#job-details",
]
JOB_DETAIL_DESCRIPTION = "div.show-more-less-html__markup"

EASY_APPLY_BUTTON_SELECTORS = [
    "button.jobs-apply-button",
    "button.jobs-apply-button--top-card",
    "button[aria-label*='Easy Apply']",
    "button.jobs-s-apply",
]
EASY_APPLY_BUTTON = "button.jobs-apply-button"

# -------------------------------------------------------------------------
# Easy Apply wizard
# -------------------------------------------------------------------------

WIZARD_MODAL = "div.jobs-easy-apply-modal"
WIZARD_NEXT = "button[aria-label='Continue to next step']"
WIZARD_REVIEW = "button[aria-label='Review your application']"
WIZARD_SUBMIT = "button[aria-label='Submit application']"
WIZARD_CLOSE = "button[aria-label='Dismiss']"
WIZARD_RESUME_INPUT = "input[type='file']"
WIZARD_TEXT_INPUT = "div.jobs-easy-apply-form-section input[type='text']"
WIZARD_SELECT = "div.jobs-easy-apply-form-section select"
WIZARD_FORM_SECTION = "div.jobs-easy-apply-form-section"
WIZARD_FORM_LABEL = "label"

# Success confirmation
APPLY_CONFIRMATION = "div.jobs-easy-apply-confirmation"

# Challenge / captcha detection
CHALLENGE_MARKER = "div#captcha-internal"
CHALLENGE_MARKERS = [
    "div#captcha-internal",
    "div.challenge-dialog",
    "#captcha-challenge",
    "iframe[src*='captcha']",
]


