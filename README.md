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
- [Installation](#installation)
- [Configuration](#configuration)
  - [Environment Variables](#environment-variables)
  - [Profile Generation from PDFs](#profile-generation-from-pdfs)
  - [Manual Profile Configuration](#manual-profile-configuration)
- [CLI Reference](#cli-reference)
  - [Global Flags](#global-flags)
  - [Commands](#commands)
- [Pipeline Overview](#pipeline-overview)
- [Data Models](#data-models)
- [Project Structure](#project-structure)
- [Testing](#testing)
- [Development Roadmap](#development-roadmap)
- [Safety & Ethics](#safety--ethics)
- [License](#license)

---

## Features

| Feature | Status |
|---|---|
| **Profile generation** from resume PDF + LinkedIn (URL or PDF) via LLM | ✅ Ready |
| **LinkedIn profile scraping** — fetch public profile via Playwright | ✅ Ready |
| **Database** — SQLite with Job, Score, ApplicationAttempt tracking | ✅ Ready |
| **CLI** — `hunt` command with 7 subcommands and global flags | ✅ Ready |
| **Job discovery** — mock LinkedIn site + Playwright navigation | ✅ Ready |
| **HTML parsing** — BeautifulSoup job card & detail extraction | ✅ Ready |
| **Mock mode** — full discovery pipeline testable with HTML fixtures | ✅ Ready |
| **Scoring** — embedding similarity + LLM fit evaluation + decision logic | ✅ Ready |
| **Easy Apply** — multi-step form automation via Playwright | 📋 Stubbed |
| **Daily reports** — Markdown + JSON summaries | 📋 Stubbed |
| **Challenge detection** — pauses on captcha, never bypasses | 📋 Planned |

---

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/your-org/AIJobHunter.git
cd AIJobHunter

# 2. Install (requires Python 3.13+ and uv)
uv sync

# 3. Set your OpenAI API key
#    Windows PowerShell:
$env:JOBHUNTER_OPENAI_API_KEY = "sk-..."
#    Linux/macOS:
export JOBHUNTER_OPENAI_API_KEY="sk-..."

# 4. Initialise the database
uv run hunt init

# 5. Generate your profile from your resume + LinkedIn
uv run hunt profile --resume path/to/resume.pdf --linkedin https://www.linkedin.com/in/your-name/

# 6. Review what was generated
uv run hunt profile --show
```

That's it — your `data/` directory now contains:

```
data/
  job_hunter.db         ← SQLite database
  user_profile.yml      ← your extracted profile
  profiles.yml          ← search profiles tailored to your background
  reports/              ← daily reports (future)
```

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

This installs all runtime and dev dependencies into a local `.venv`.

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
| `JOBHUNTER_OPENAI_API_KEY` | Yes (for profile generation & scoring) | `""` | OpenAI API key |
| `JOBHUNTER_LLM_PROVIDER` | No | `"openai"` | LLM provider (`openai`) |
| `JOBHUNTER_DATA_DIR` | No | `"data"` | Path to the data directory |

Set them in your shell, a `.env` file, or pass `--data-dir` via CLI.

### Profile Generation from PDFs

The fastest way to configure the system is to let the LLM analyse your resume:

```bash
# Resume only
uv run hunt profile --resume cv.pdf

# Resume + LinkedIn profile URL (scraped via Playwright)
uv run hunt profile --resume cv.pdf --linkedin https://www.linkedin.com/in/your-name/

# Resume + LinkedIn profile PDF (if you have an export)
uv run hunt profile --resume cv.pdf --linkedin linkedin_profile.pdf

# View the generated profile
uv run hunt profile --show
```

> **Note:** When using a LinkedIn URL, Playwright launches a headless Chromium
> browser to render the page.  Use `--no-headless` if you need to see the
> browser (e.g. to solve a login challenge):
> ```bash
> uv run hunt --no-headless profile --resume cv.pdf --linkedin https://www.linkedin.com/in/your-name/
> ```

The LLM extracts your skills, experience, seniority, and desired roles, then
generates 1–3 search profiles optimised for your career tracks. Output is saved
to `data/user_profile.yml` and `data/profiles.yml`.

If files already exist you'll be prompted before overwriting.

#### Generated `user_profile.yml` (example)

```yaml
user_profile:
  name: Jane Doe
  title: Senior Python Developer
  summary: Experienced backend engineer with 8 years in Python ecosystems.
  skills:
    - Python
    - FastAPI
    - AWS
    - PostgreSQL
    - Docker
  experience_years: 8
  preferred_locations:
    - Remote
    - New York, NY
  desired_roles:
    - Senior Python Developer
    - Backend Engineer
    - Staff Engineer
  seniority_level: Senior
  education:
    - M.Sc. Computer Science
  languages:
    - Python
    - SQL
    - English
```

#### Generated `profiles.yml` (example)

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
      - Junior
    min_fit_score: 75
    min_similarity: 0.35
    max_applications_per_day: 25
```

### Manual Profile Configuration

You can also write or edit `data/profiles.yml` by hand:

```yaml
profiles:
  - name: default
    keywords:
      - Python Developer
      - Backend Engineer
    location: "Remote"
    remote: true
    seniority:
      - Senior
      - Mid-Senior
    blacklist_companies:
      - SpamCorp
    blacklist_titles:
      - Intern
    min_fit_score: 75        # 0-100, LLM fit score threshold
    min_similarity: 0.35     # 0.0-1.0, embedding cosine similarity threshold
    max_applications_per_day: 25

  - name: ml-focused
    keywords:
      - Machine Learning Engineer
      - Data Scientist
    location: "New York, NY"
    remote: false
    seniority:
      - Senior
    blacklist_companies: []
    blacklist_titles: []
    min_fit_score: 80
    min_similarity: 0.40
    max_applications_per_day: 15
```

---

## CLI Reference

```
hunt [GLOBAL OPTIONS] COMMAND [COMMAND OPTIONS]
```

### Global Flags

| Flag | Type | Default | Description |
|---|---|---|---|
| `--mock` | flag | off | Use mock LinkedIn site (HTML fixtures) |
| `--real` | flag | off | Use real LinkedIn (requires cookies) |
| `--dry-run` | flag | off | Run without submitting applications |
| `--headless / --no-headless` | bool | `--headless` | Browser headless mode |
| `--slowmo-ms` | int | `0` | Slow-motion delay in ms (for debugging) |
| `--data-dir` | path | `data` | Path to the data directory |
| `--log-level` | choice | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### Commands

#### `hunt init`

Initialise the database and data directory.

```bash
uv run hunt init
# ✓ Database initialised at data/job_hunter.db
```

Creates `data/`, `data/reports/`, and the SQLite database with all tables.

#### `hunt profile`

Generate or view your user profile and search profiles.

```bash
# Generate from resume PDF + LinkedIn URL
uv run hunt profile --resume cv.pdf --linkedin https://www.linkedin.com/in/your-name/

# Generate from resume PDF + LinkedIn PDF
uv run hunt profile --resume cv.pdf --linkedin linkedin.pdf

# Resume only
uv run hunt profile --resume cv.pdf

# Display current profile
uv run hunt profile --show
```

| Option | Short | Description |
|---|---|---|
| `--resume FILE` | `-r` | Path to resume PDF (required for generation) |
| `--linkedin TEXT` | `-l` | LinkedIn profile URL **or** path to PDF (optional) |
| `--show` | | Display the current saved profile |

#### `hunt discover`

Discover fresh LinkedIn jobs for a search profile.

```bash
# Mock mode (uses local HTML fixtures — no LinkedIn account needed)
uv run hunt --mock discover --profile default
# ✓ Discovered 3 job(s) and saved to database

# Real mode (not yet implemented — Phase 5)
uv run hunt --real discover --profile default
```

Navigates the job list page, parses each card, visits every detail page, and
upserts all discovered jobs into the database with status `NEW`.

#### `hunt score`

Compute fit-scores for discovered jobs against your resume.

```bash
# Mock mode (uses fake embedder + evaluator)
uv run hunt --mock score --profile default
# ✓ Scored 3 job(s): 2 queued, 0 skipped, 1 review

# Real mode (uses OpenAI embeddings + LLM evaluation)
uv run hunt score --profile default
```

Iterates all jobs with status `NEW`, computes embedding similarity and LLM fit
score against your resume, saves `Score` rows, and updates each job's status:
- **QUEUED** — passes all thresholds and has Easy Apply → ready for application
- **SKIPPED** — LLM decision is "skip" or poor fit
- **REVIEW** — borderline, needs human judgment

#### `hunt apply`

Apply to qualified jobs via Easy Apply.

```bash
uv run hunt --dry-run apply --profile default
```

> 📋 *Not yet implemented — stubbed for Phase 4.*

#### `hunt run`

Run the full pipeline: discover → score → apply → report.

```bash
uv run hunt --mock --dry-run run --profile default
```

> 📋 *Not yet implemented — stubbed for Phase 6.*

#### `hunt report`

Generate a daily report.

```bash
uv run hunt report --date 2026-03-05
```

> 📋 *Not yet implemented — stubbed for Phase 6.*

---

## Pipeline Overview

```
┌──────────┐    ┌───────┐    ┌───────┐    ┌───────┐    ┌────────┐
│ Discover │───▶│ Score │───▶│ Queue │───▶│ Apply │───▶│ Report │
└──────────┘    └───────┘    └───────┘    └───────┘    └────────┘
     │               │                        │
     ▼               ▼                        ▼
  LinkedIn      Embeddings +           Easy Apply wizard
  search        LLM evaluation         (Playwright)
  results
```

**Decision policy** — a job is auto-applied if:
- `easy_apply` is `true`
- LLM fit score ≥ `min_fit_score` (default 75)
- Embedding similarity ≥ `min_similarity` (default 0.35)

Jobs below thresholds are marked `SKIPPED` or `REVIEW`.

---

## Data Models

Three core entities are stored in the SQLite database:

### Job

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `source` | string | Always `"linkedin"` for now |
| `external_id` | string | LinkedIn's job ID |
| `url` | string | Full job URL |
| `title` | string | Job title |
| `company` | string | Company name |
| `location` | string | Job location |
| `posted_at` | datetime | When the job was posted |
| `description_text` | text | Full job description |
| `easy_apply` | bool | Whether Easy Apply is available |
| `collected_at` | datetime | When we discovered this job |
| `hash` | string | SHA-256 dedup hash |
| `status` | enum | `new` → `scored` → `queued` → `applied` / `skipped` / `blocked` / `review` / `failed` |
| `notes` | text | Free-form notes |

### Score

| Field | Type | Description |
|---|---|---|
| `job_hash` | string | References the Job |
| `resume_id` | string | Which resume was used |
| `embedding_similarity` | float | Cosine similarity (0.0–1.0) |
| `llm_fit_score` | int | LLM evaluation (0–100) |
| `missing_skills` | JSON | Skills the candidate lacks |
| `risk_flags` | JSON | Red flags identified by LLM |
| `decision` | enum | `apply` / `skip` / `review` |

### ApplicationAttempt

| Field | Type | Description |
|---|---|---|
| `job_hash` | string | References the Job |
| `started_at` | datetime | When the attempt started |
| `ended_at` | datetime | When the attempt finished |
| `result` | enum | `success` / `failed` / `blocked` / `dry_run` |
| `failure_stage` | string | Which wizard step failed |
| `screenshot_paths` | JSON | Paths to screenshots taken |
| `form_answers_json` | JSON | Answers submitted in the form |

---

## Project Structure

```
AIJobHunter/
├── pyproject.toml                        # Project metadata & dependencies
├── README.md
├── coding_guidelines.md                  # Full specification document
│
├── src/job_hunter/
│   ├── __init__.py
│   ├── cli.py                            # Typer CLI — all commands
│   │
│   ├── config/
│   │   ├── models.py                     # AppSettings, SearchProfile, UserProfile
│   │   └── loader.py                     # YAML load/save + settings factory
│   │
│   ├── db/
│   │   ├── models.py                     # SQLAlchemy ORM (Job, Score, ApplicationAttempt)
│   │   ├── repo.py                       # DB init, session factory, CRUD helpers
│   │   └── migrations.py                 # Alembic placeholder
│   │
│   ├── profile/
│   │   ├── extract.py                    # PDF extraction + LinkedIn URL scraping
│   │   └── generator.py                  # LLM profile generation (OpenAI + Fake)
│   │
│   ├── linkedin/
│   │   ├── session.py                    # Cookie-based browser session
│   │   ├── discover.py                   # Job search navigation
│   │   ├── parse.py                      # HTML → structured data
│   │   ├── apply.py                      # Easy Apply automation
│   │   ├── forms.py                      # Form-filling helpers
│   │   ├── selectors.py                  # Centralised CSS/XPath selectors
│   │   └── mock_site/fixtures/           # HTML fixtures for mock mode
│   │
│   ├── matching/
│   │   ├── embeddings.py                 # Embedding providers (+ FakeEmbedder)
│   │   ├── llm_eval.py                   # LLM evaluators (+ FakeLLMEvaluator)
│   │   └── scoring.py                    # Combined scoring + decision logic
│   │
│   ├── orchestration/
│   │   ├── pipeline.py                   # discover → score → apply → report
│   │   └── policies.py                   # Rate limits, blacklists, daily caps
│   │
│   ├── reporting/
│   │   └── report.py                     # Markdown + JSON report generation
│   │
│   └── utils/
│       ├── logging.py                    # Structured logging setup
│       ├── rate_limit.py                 # Token-bucket rate limiter
│       ├── retry.py                      # Exponential back-off decorator
│       └── hashing.py                    # SHA-256 job dedup hashing
│
├── tests/
│   ├── test_config.py                    # Config loading & validation
│   ├── test_db_repo.py                   # DB init, upsert, CRUD round-trips
│   ├── test_matching_scoring.py          # Scoring logic & thresholds
│   ├── test_llm_eval_schema.py           # LLM evaluator schema contract
│   ├── test_profile_generation.py        # PDF extraction, profile gen, YAML I/O
│   ├── test_discover_parse_mock.py       # Discovery & parsing (Phase 2 stubs)
│   ├── test_apply_mock_flow.py           # Easy Apply flow (Phase 4 stubs)
│   └── fixtures/
│       ├── resume.txt                    # Sample resume text
│       └── profiles.yml                  # Sample search profiles
│
└── data/                                 # Runtime data (gitignored)
    ├── job_hunter.db
    ├── user_profile.yml
    ├── profiles.yml
    └── reports/
```

---

## Testing

All tests run offline — no API keys or internet required.

```bash
# Run all tests
uv run pytest -q

# Run with verbose output
uv run pytest -v

# Run a specific test file
uv run pytest tests/test_profile_generation.py -v

# Run tests matching a pattern
uv run pytest -k "test_upsert" -v
```

**Current test suite:** 77 passed, 2 skipped (Phase 4 Easy Apply stubs).

The skipped tests are for the Easy Apply worker (not yet implemented) and will
be unskipped when Phase 4 is built.

### Test architecture

- **No network calls** — all tests use `FakeEmbedder`, `FakeLLMEvaluator`,
  `FakeProfileGenerator`, in-memory SQLite, and a local mock HTTP server.
- **Mock discovery tests** spin up a lightweight HTTP server serving HTML fixtures
  and use Playwright to navigate them — same as real discovery but offline.
- **PDF tests** create tiny PDFs on-the-fly using PyMuPDF.
- **YAML round-trip tests** verify save → load produces identical data.
- **DB integration tests** verify discover → upsert → query round-trips and
  idempotent re-discovery.

---

## Development Roadmap

| Phase | Description | Status |
|---|---|---|
| **Phase 1** | Skeleton + DB + CLI | ✅ Complete |
| **Phase 1.5** | Profile generation from resume PDF + LinkedIn (URL or PDF) | ✅ Complete |
| **Phase 2** | Mock LinkedIn site + HTML parser + `hunt discover` | ✅ Complete |
| **Phase 3** | Matching — embeddings + LLM scoring + `hunt score` | ✅ Complete |
| **Phase 4** | Easy Apply worker (mock Playwright) | 📋 Next |
| **Phase 5** | Real LinkedIn integration | 📋 Planned |
| **Phase 6** | Orchestration + reporting | 📋 Planned |

---

## Safety & Ethics

- **No captcha bypassing.** If LinkedIn shows a challenge, the bot pauses and
  marks the job as `BLOCKED`, requiring manual intervention.
- **Rate limiting** — configurable delays between actions, daily application caps.
- **Dry-run mode** (`--dry-run`) — runs the full pipeline without submitting any
  applications.
- **No secret logging** — cookies and API keys are never written to logs or reports.
- **Respect LinkedIn's UI** — the tool navigates pages like a human user with
  realistic delays, not via undocumented APIs.

---

## License

This project is for personal use. See `LICENSE` for details.

