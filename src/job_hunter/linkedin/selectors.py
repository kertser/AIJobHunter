"""Centralised CSS / XPath selectors for LinkedIn pages.

Keep all selectors here so they can be updated in one place when
LinkedIn changes its DOM.
"""

# Job search results page
JOB_CARD = "div.job-card-container"
JOB_CARD_TITLE = "a.job-card-list__title"
JOB_CARD_COMPANY = "span.job-card-container__primary-description"
JOB_CARD_LOCATION = "li.job-card-container__metadata-item"
JOB_CARD_LINK = "a.job-card-list__title"

# Job detail page
JOB_DETAIL_TITLE = "h1.t-24"
JOB_DETAIL_COMPANY = "a.topcard__org-name-link"
JOB_DETAIL_DESCRIPTION = "div.show-more-less-html__markup"
EASY_APPLY_BUTTON = "button.jobs-apply-button"

# Easy Apply wizard
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

