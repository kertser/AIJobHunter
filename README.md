# AI Job Hunter

Automated LinkedIn job discovery, scoring, and application powered by LLM-based
resume matching and browser automation.

AI Job Hunter finds fresh LinkedIn jobs matching your background, scores each one
against your resume using embedding similarity and LLM evaluation, and — when a
job passes your thresholds — applies via Easy Apply automatically.

---

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Web GUI](#web-gui)
- [Installation](#installation)
- [Configuration](#configuration)
  - [Environment Variables](#environment-variables)
  - [Profile Generation](#profile-generation)
  - [Manual Profile Configuration](#manual-profile-configuration)
- [CLI Reference](#cli-reference)
- [Pipeline Overview](#pipeline-overview)
- [Data Models](#data-models)
- [Project Structure](#project-structure)
- [Testing](#testing)
- [Development Roadmap](#development-roadmap)
- [Safety & Ethics](#safety--ethics)
- [License](#license)

---

## Features

| Feature | Description |
|---|---|
| **Profile generation** | Extract skills & experience from resume PDF + LinkedIn (URL or PDF) via LLM |
| **LinkedIn scraping** | Fetch public profile data via Playwright headless browser |
| **Job discovery** | Cookie-based LinkedIn search with pagination, CSS + JS fallback parsing |
| **AI scoring** | Embedding similarity + LLM fit evaluation (0–100) with skill gap analysis |
| **Industry preferences** | Boost/penalise scores based on preferred and disliked industries |
| **Easy Apply automation** | Multi-step wizard with LLM-powered form filling for arbitrary questions |
| **Challenge detection** | Pauses on captcha, marks job as BLOCKED — no bypass attempts |
| **Web GUI** | Full command & control dashboard (FastAPI + HTMX + Pico CSS) |
| **Visual dashboard** | Donut chart, fit histogram, skill gap bars, activity timeline |
| **Resume review** | AI-powered gap analysis comparing your resume to target jobs |
| **Daily reports** | Markdown + JSON summaries with stats and job tables |
| **Mock mode** | Full pipeline testable offline with HTML fixtures |
| **CLI** | `hunt` command with 9 subcommands and global flags |
| **`.env` support** | API keys and settings from `.env` file |
| **SQLite database** | Job, Score, ApplicationAttempt tracking with WAL mode |

---

## Quick Start

```bash
# 1. Clone & install (requires Python 3.13+ and uv)
git clone https://github.com/your-org/AIJobHunter.git
cd AIJobHunter
uv sync

# 2. Set your OpenAI API key
cp .env.example .env
#    Edit .env: JOBHUNTER_OPENAI_API_KEY=sk-...

# 3. Launch the web GUI (easiest way to get started)
uv run hunt --real --dry-run serve
#    Open http://localhost:8000 → Setup page guides you through profile creation
```

### Or use the CLI directly:

```bash
# Initialise database
uv run hunt init

# Generate profile from resume + LinkedIn
uv run hunt profile --resume path/to/resume.pdf --linkedin https://www.linkedin.com/in/your-name/

# Log in to LinkedIn (saves cookies)
uv run hunt login

# Run full pipeline (dry-run first to preview)
uv run hunt --real --dry-run run --profile default

# Run for real
uv run hunt --real run --profile default
```

After setup, your `data/` directory contains:

```
data/
  job_hunter.db         ← SQLite database
  user_profile.yml      ← your extracted profile
  profiles.yml          ← search profiles tailored to your background
  cookies.json          ← LinkedIn session cookies (after hunt login)
  reports/              ← daily reports (Markdown + JSON)
```

---

## Web GUI

Start the web server with:

```bash
uv run hunt --real --dry-run serve          # safe mode — won't submit applications
uv run hunt --real serve                    # live mode — will submit applications
uv run hunt --mock serve                    # offline testing with mock data
```

Then open **http://localhost:8000** in your browser.

### Pages

| Page | Path | Description |
|---|---|---|
| **Dashboard** | `/` | Visual stats — donut chart, fit histogram, skill gap bars, activity timeline, quick actions |
| **Jobs** | `/jobs` | Sortable/filterable table with bulk actions, status management, persistent sort preference |
| **Job Detail** | `/api/jobs/{hash}` | Full description (formatted), scores, Easy Apply button, application history |
| **Pipeline** | `/run` | Trigger Discover / Score / Apply / Full Pipeline with live SSE progress log |
| **Profiles** | `/profiles` | Edit user profile (name, contact, skills, industry preferences) and search profiles |
| **Resume Review** | `/resume-review` | AI gap analysis — missing skills, improvement suggestions, quick wins |
| **Reports** | `/reports` | Browse and view daily pipeline reports |
| **Settings** | `/settings` | Toggle mock/dry-run/headless, adjust slow-mo, update API key and log level |
| **Setup** | `/onboarding` | Upload resume PDF + LinkedIn URL to generate profiles (first-run wizard) |

All CLI functionality is accessible via the web UI — no terminal needed after initial setup.

---

## Installation

### Prerequisites

- **Python 3.13+**
- **[uv](https://docs.astral.sh/uv/)** — fast Python package manager

### Install

```bash
cd AIJobHunter
uv sync
```

### Verify

```bash
uv run hunt --help
uv run pytest -q
```

---

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `JOBHUNTER_OPENAI_API_KEY` | Yes | `""` | OpenAI API key (for scoring, profile gen, form filling) |
| `JOBHUNTER_LLM_PROVIDER` | No | `"openai"` | LLM provider |
| `JOBHUNTER_DATA_DIR` | No | `"data"` | Path to the data directory |

**Recommended:** use a `.env` file in the project root:

```bash
cp .env.example .env
# Edit .env:
JOBHUNTER_OPENAI_API_KEY=sk-proj-...
```

The app reads `.env` on startup. Shell environment variables take precedence.

### Profile Generation

The fastest way to configure is to let the LLM analyse your resume:

```bash
# Resume + LinkedIn URL (scraped via Playwright)
uv run hunt profile --resume cv.pdf --linkedin https://www.linkedin.com/in/your-name/

# Resume + LinkedIn PDF export
uv run hunt profile --resume cv.pdf --linkedin linkedin.pdf

# Resume only
uv run hunt profile --resume cv.pdf

# View current profile
uv run hunt profile --show
```

Or use the web GUI: go to **Setup** (`/onboarding`) and upload your files.

The LLM generates `data/user_profile.yml` and `data/profiles.yml`:

#### `user_profile.yml` (example)

```yaml
user_profile:
  name: Jane Doe
  first_name: Jane
  last_name: Doe
  email: jane@example.com
  phone: "555-0123"
  phone_country_code: "+1"
  title: Senior Python Developer
  summary: Experienced backend engineer with 8 years in Python ecosystems.
  skills:
    - Python
    - FastAPI
    - AWS
  experience_years: 8
  seniority_level: Senior
  spoken_languages:          # Human languages
    - English
    - Spanish
  programming_languages:     # Programming languages
    - Python
    - SQL
  preferred_industries:      # Boost score for matching jobs
    - startups
    - healthcare
  disliked_industries:       # Penalise score for matching jobs
    - fintech
    - adtech
```

#### `profiles.yml` (example)

```yaml
profiles:
  - name: backend-python
    keywords:
      - Senior Python Developer
      - Backend Engineer
    location: Remote
    remote: true
    seniority:
      - Senior
      - Mid-Senior
    blacklist_companies: []
    blacklist_titles:
      - Intern
    min_fit_score: 75
    min_similarity: 0.35
    max_applications_per_day: 25
```

### Manual Profile Configuration

You can edit `data/profiles.yml` by hand or via the **Profiles** page in the web GUI.

---

## CLI Reference

```
hunt [GLOBAL OPTIONS] COMMAND [COMMAND OPTIONS]
```

### Global Flags

| Flag | Default | Description |
|---|---|---|
| `--mock` | off | Use mock LinkedIn (HTML fixtures) |
| `--real` | off | Use real LinkedIn (requires cookies) |
| `--dry-run` | off | Run without submitting applications |
| `--headless / --no-headless` | headless | Browser visibility |
| `--slowmo-ms INT` | `0` | Slow-motion delay in ms |
| `--data-dir PATH` | `data` | Data directory path |
| `--log-level` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

### Commands

| Command | Description |
|---|---|
| `hunt init` | Initialise database and data directory |
| `hunt login` | Open browser for LinkedIn login, save cookies |
| `hunt profile` | Generate or view user profile and search profiles |
| `hunt discover` | Discover LinkedIn jobs for a search profile |
| `hunt score` | Compute fit-scores for discovered jobs |
| `hunt apply` | Apply to qualified jobs via Easy Apply |
| `hunt run` | Run full pipeline: discover → score → apply → report |
| `hunt report` | Generate daily Markdown + JSON report |
| `hunt serve` | Start web GUI server (default: http://localhost:8000) |

#### Examples

```bash
# Mock mode testing
uv run hunt --mock --dry-run run --profile default

# Real LinkedIn with dry-run
uv run hunt --real --dry-run run --profile backend-python

# Web GUI
uv run hunt --real serve
uv run hunt serve --host 0.0.0.0 --port 3000
```

---

## Pipeline Overview

```
┌──────────┐    ┌───────┐    ┌───────┐    ┌───────┐    ┌────────┐
│ Discover │───>│ Score │───>│ Queue │───>│ Apply │───>│ Report │
└──────────┘    └───────┘    └───────┘    └───────┘    └────────┘
     │               │                        │
     ▼               ▼                        ▼
  LinkedIn      Embeddings +           Easy Apply wizard
  search        LLM evaluation         + LLM form filler
  + parsing     + industry prefs       (Playwright)
```

**Decision policy** — a job is auto-applied if:
- `easy_apply` is `true`
- LLM fit score ≥ `min_fit_score` (default 75)
- Embedding similarity ≥ `min_similarity` (default 0.35)

Jobs below thresholds → `SKIPPED` or `REVIEW`.

**LLM form filling** — the Easy Apply wizard encounters arbitrary questions
(dropdowns, text fields, radio buttons). The system uses GPT to generate
contextually appropriate answers based on your user profile.

---

## Data Models

### Job

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `external_id` | string | LinkedIn job ID |
| `title` | string | Job title |
| `company` | string | Company name |
| `location` | string | Job location |
| `posted_at` | datetime | When posted on LinkedIn |
| `description_text` | text | Full job description |
| `easy_apply` | bool | Easy Apply available |
| `hash` | string | SHA-256 dedup hash |
| `status` | enum | `new` → `scored` → `queued` → `applied` / `skipped` / `blocked` / `review` / `failed` |

### Score

| Field | Type | Description |
|---|---|---|
| `job_hash` | string | References Job |
| `embedding_similarity` | float | Cosine similarity (0.0–1.0) |
| `llm_fit_score` | int | LLM evaluation (0–100) |
| `missing_skills` | JSON | Skills the candidate lacks |
| `risk_flags` | JSON | Red flags identified by LLM |
| `decision` | enum | `apply` / `skip` / `review` |

### ApplicationAttempt

| Field | Type | Description |
|---|---|---|
| `job_hash` | string | References Job |
| `result` | enum | `success` / `failed` / `blocked` / `dry_run` / `already_applied` |
| `failure_stage` | string | Which wizard step failed |
| `form_answers_json` | JSON | Answers submitted in the form |

---

## Project Structure

```
AIJobHunter/
├── pyproject.toml                        # Dependencies & project metadata
├── README.md
├── .env.example                          # Template for environment config
├── TODO.txt                              # Remaining work items
│
├── src/job_hunter/
│   ├── cli.py                            # Typer CLI — all 9 commands
│   │
│   ├── config/
│   │   ├── models.py                     # AppSettings, SearchProfile, UserProfile
│   │   └── loader.py                     # YAML load/save + settings factory
│   │
│   ├── db/
│   │   ├── models.py                     # ORM: Job, Score, ApplicationAttempt
│   │   ├── repo.py                       # DB init, session, CRUD helpers
│   │   └── migrations.py                 # Schema migration support
│   │
│   ├── profile/
│   │   ├── extract.py                    # PDF text + LinkedIn URL scraping
│   │   └── generator.py                  # LLM profile generation
│   │
│   ├── linkedin/
│   │   ├── session.py                    # Cookie-based Playwright session
│   │   ├── discover.py                   # Job search + pagination
│   │   ├── parse.py                      # HTML → structured data
│   │   ├── apply.py                      # Easy Apply wizard automation
│   │   ├── forms.py                      # Form-filling helpers (inputs, dropdowns, radios)
│   │   ├── form_filler_llm.py            # LLM-powered form answers
│   │   ├── selectors.py                  # CSS/XPath selectors
│   │   └── mock_site/fixtures/           # HTML fixtures for mock mode
│   │
│   ├── matching/
│   │   ├── embeddings.py                 # Embedding providers
│   │   ├── llm_eval.py                   # LLM evaluators
│   │   ├── scoring.py                    # Combined scoring + decision logic
│   │   └── description_cleaner.py        # Rule-based + LLM description cleanup
│   │
│   ├── orchestration/
│   │   ├── pipeline.py                   # discover → score → apply → report
│   │   └── policies.py                   # Rate limits, blacklists, daily caps
│   │
│   ├── reporting/
│   │   └── report.py                     # Markdown + JSON report generation
│   │
│   ├── web/                              # Web GUI (FastAPI + HTMX + Pico CSS)
│   │   ├── app.py                        # FastAPI app factory
│   │   ├── deps.py                       # Dependency injection
│   │   ├── task_manager.py               # Background tasks + SSE events
│   │   ├── routers/
│   │   │   ├── dashboard.py              # Visual stats + charts
│   │   │   ├── jobs.py                   # Jobs CRUD + bulk actions
│   │   │   ├── onboarding.py             # First-run profile wizard
│   │   │   ├── profiles.py               # User + search profile editing
│   │   │   ├── resume_review.py          # AI resume gap analysis
│   │   │   ├── run.py                    # Pipeline trigger + SSE progress
│   │   │   ├── reports.py                # Daily report viewer
│   │   │   └── settings.py               # Runtime settings
│   │   ├── templates/                    # Jinja2 HTML templates
│   │   └── static/                       # Banner, favicon
│   │
│   └── utils/
│       ├── logging.py                    # Structured logging setup
│       ├── rate_limit.py                 # Token-bucket rate limiter
│       ├── retry.py                      # Exponential back-off decorator
│       └── hashing.py                    # SHA-256 job dedup
│
├── tests/                                # 166 tests — all run offline
│   ├── test_web.py                       # Web GUI endpoints (37 tests)
│   ├── test_apply_mock_flow.py           # Easy Apply wizard
│   ├── test_discover_parse_mock.py       # Discovery & parsing
│   ├── test_matching_scoring.py          # Scoring logic
│   ├── test_pipeline.py                  # Pipeline orchestration
│   ├── test_db_repo.py                   # Database CRUD
│   ├── test_profile_generation.py        # Profile generation
│   ├── test_description_cleaner.py       # Description cleanup
│   ├── test_config.py                    # Config loading
│   ├── test_llm_eval_schema.py           # LLM evaluator schema
│   ├── test_linkedin_session.py          # Session & URLs
│   ├── test_reporting.py                 # Report generation
│   └── fixtures/                         # Sample data
│
└── data/                                 # Runtime data (gitignored)
    ├── .gitkeep
    ├── job_hunter.db                     # SQLite database
    ├── user_profile.yml                  # Your profile
    ├── profiles.yml                      # Search profiles
    ├── cookies.json                      # LinkedIn cookies
    └── reports/                          # Daily reports
```

---

## Testing

All tests run offline — no API keys or internet required.

```bash
uv run pytest -q               # Quick summary
uv run pytest -v               # Verbose output
uv run pytest tests/test_web.py # Specific test file
uv run pytest -k "test_upsert" # Pattern matching
```

**Current:** 166 tests passed.

Tests use `FakeEmbedder`, `FakeLLMEvaluator`, `FakeProfileGenerator`,
in-memory SQLite, and a local mock HTTP server. Mock discovery tests
spin up a lightweight server with HTML fixtures and use Playwright
to navigate them offline.

---

## Development Roadmap

| Phase | Description | Status |
|---|---|---|
| **1** | Skeleton + DB + CLI | ✅ |
| **2** | Profile generation from resume PDF + LinkedIn | ✅ |
| **3** | Mock LinkedIn + HTML parser + discovery | ✅ |
| **4** | Embeddings + LLM scoring | ✅ |
| **5** | Easy Apply automation | ✅ |
| **6** | Real LinkedIn integration + cookies | ✅ |
| **7** | Orchestration + reporting | ✅ |
| **8** | Web GUI dashboard (FastAPI + HTMX) | ✅ |
| **9** | LLM form filling, `.env` support, industry preferences | ✅ |
| **10** | Resume review, visual dashboard, UI polish | ✅ |
| **Next** | Scheduled pipelines, email/Slack notifications | 🔜 |

---

## Safety & Ethics

- **No captcha bypassing.** Challenges pause the bot and mark job as `BLOCKED`.
- **Rate limiting** — configurable delays, daily application caps.
- **Dry-run mode** — runs everything without submitting applications.
- **No secret logging** — cookies and API keys never appear in logs or reports.
- **Respectful automation** — navigates like a human with realistic delays.

---

## License

This project is for personal use. See `LICENSE` for details.

