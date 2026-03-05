
# AI Job Hunter — Coding Agent Task Specification (v1)

## 0) Purpose and Constraints

Build an “AI Job Hunter” system that:
1) Finds **fresh** LinkedIn jobs based on user-defined search profiles.
2) Computes **fit score** between each job and the user’s resume.
3) If job passes thresholds, performs **LinkedIn Easy Apply** where possible.
4) Tracks outcomes, avoids duplicates, and produces a daily report.

### Hard constraints
- **No captcha bypassing** and no evasion instructions. If LinkedIn shows a challenge, the bot must **pause**, notify, and require manual intervention.
- Respect LinkedIn’s UI and rate limits: add configurable delays, max applications/day, and safe retries.
- Must be testable with a **Mock UI mode** and HTML fixtures (no live LinkedIn required for CI tests).
- All user secrets (cookies, tokens) stored locally and never logged.

### Non-goals (v1)
- No outreach messaging (DMs) - at least not in v1.
- No external job boards besides LinkedIn.
- No cover-letter generation unless explicitly enabled (stub only).

---

## 1) High-level Architecture

### Components
1. **Config & Profiles**
   - YAML search profiles (keywords, location, remote, seniority, blacklist, etc.).
2. **Job Discovery**
   - Collect job cards + job details into DB.
3. **Matching**
   - Embedding similarity + LLM evaluation for structured scoring.
4. **Decision**
   - Apply / Skip / Review decisions.
5. **Apply Worker**
   - Browser automation for Easy Apply multi-step form (DOM-first; screenshot optional).
6. **Persistence**
   - SQLite (v1) + optional vector store (Qdrant optional; start with SQLite + embeddings table).
7. **Reporting**
   - Daily markdown report + JSON summary.
8. **CLI**
   - `hunt discover`, `hunt score`, `hunt apply`, `hunt report`, and `hunt run` orchestration.

### Suggested stack
- Python 3.13+
- Playwright (sync or async; prefer async)
- Pydantic for typed config/models
- SQLite via SQLAlchemy (or sqlite3 if simpler)
- A small LLM wrapper interface (OpenAI-compatible + local optional)
- Embeddings: OpenAI embeddings (or local sentence-transformer optional)
- Testing: pytest, pytest-asyncio, playwright test fixtures

---

## 2) Repository Layout

```
ai-job-hunter/
  README.md
  pyproject.toml
  src/job_hunter/
    __init__.py
    cli.py
    config/
      models.py
      loader.py
    db/
      models.py
      repo.py
      migrations.py
    linkedin/
      session.py
      discover.py
      parse.py
      apply.py
      forms.py
      selectors.py
      mock_site/
        fixtures/
          job_list.html
          job_detail.html
          easy_apply_step1.html
          easy_apply_step2_questions.html
          easy_apply_step3_review.html
    matching/
      embeddings.py
      llm_eval.py
      scoring.py
    orchestration/
      pipeline.py
      policies.py
    reporting/
      report.py
    utils/
      logging.py
      rate_limit.py
      retry.py
      hashing.py
  tests/
    test_config.py
    test_db_repo.py
    test_matching_scoring.py
    test_llm_eval_schema.py
    test_discover_parse_mock.py
    test_apply_mock_flow.py
    fixtures/
      resume.txt
      profiles.yml
  data/
```

---

## 3) Data Models

### Job Entity
Fields:
- id (UUID)
- source = linkedin
- external_id
- url
- title
- company
- location
- posted_at
- description_text
- easy_apply
- collected_at
- hash
- status
- notes

### Score Entity
- job_hash
- resume_id
- embedding_similarity
- llm_fit_score
- missing_skills
- risk_flags
- decision
- created_at

### ApplicationAttempt
- job_hash
- started_at
- ended_at
- result
- failure_stage
- screenshot_paths
- form_answers_json

---

## 4) Pipeline Phases

### Phase 1 — Skeleton + DB + CLI
Deliver CLI commands:

```
hunt init
hunt discover --profile <name>
hunt score --profile <name>
hunt apply --profile <name>
hunt run --profile <name>
hunt report --date YYYY-MM-DD
```

Acceptance tests:
- pytest passes
- database initializes

---

### Phase 2 — Mock LinkedIn Site + Parser

Create HTML fixtures simulating:

- job list page
- job detail page
- easy apply wizard

Parser extracts:

- title
- company
- location
- url
- easy_apply flag
- description text

---

### Phase 3 — Matching

Embedding similarity between resume and job description.

LLM evaluation returning:

```
{
  "fit_score": 0-100,
  "missing_skills": [],
  "risk_flags": [],
  "decision": "apply|skip|review"
}
```

Decision policy:

Apply if:

- easy_apply true
- score >= 75
- similarity >= 0.35

---

### Phase 4 — Apply Worker (Mock)

Playwright automation:

Steps:

1 Upload resume  
2 Answer questions  
3 Review  
4 Submit  

Tests must simulate wizard success and failure.

---

### Phase 5 — Real LinkedIn Integration

Session:

Manual login → save cookies.

Discover:

Navigate search results pages.

Apply:

Click Easy Apply if available.

If captcha/challenge detected:

STOP and mark BLOCKED.

---

### Phase 6 — Orchestration

Pipeline:

```
discover → score → queue → apply → report
```

Ensure resumability and idempotency.

---

### Phase 7 — Web GUI

Provide a browser-based dashboard for ease of use, so the user can manage
the entire job-hunting workflow without touching the CLI.

#### Stack
- **FastAPI** backend serving a REST API + static files
- **Jinja2 + HTMX** (or lightweight React/Vue SPA) for the frontend
- **SQLite** — same DB as the CLI; shared access

#### Pages / Views

1. **Dashboard** — summary cards: jobs discovered today, scored, queued,
   applied, failed, blocked.  Charts for trends over time.
2. **Jobs** — filterable/sortable table of all jobs.  Columns: title,
   company, location, status, fit score, similarity, easy_apply, actions.
   Click a row to see full detail + score breakdown.
3. **Job Detail** — description, score, missing skills, risk flags,
   application attempts, screenshots.  Buttons: "Force Apply", "Skip",
   "Mark Review".
4. **Profiles** — view/edit user profile and search profiles (YAML editor
   or structured form).  "Regenerate from PDF" button.
5. **Run Controls** — trigger discover / score / apply / full pipeline
   from the UI.  Show live progress via SSE or WebSocket.
6. **Reports** — browse daily reports rendered as HTML.  Download as
   Markdown or JSON.
7. **Settings** — edit environment config (API keys masked), toggle
   mock/dry-run, adjust thresholds.
8. **Logs** — live-tail of the application log stream.

#### API Endpoints (REST)

```
GET    /api/jobs                  — list jobs (filterable, paginated)
GET    /api/jobs/{hash}           — single job + scores + attempts
PATCH  /api/jobs/{hash}/status    — manually change status
GET    /api/scores                — list scores
GET    /api/profiles              — list search profiles
PUT    /api/profiles              — update profiles
GET    /api/user-profile          — current user profile
POST   /api/run/discover          — trigger discover
POST   /api/run/score             — trigger score
POST   /api/run/apply             — trigger apply
POST   /api/run/pipeline          — trigger full pipeline
GET    /api/reports               — list reports
GET    /api/reports/{date}        — single report
GET    /api/stats/dashboard       — dashboard summary stats
```

#### Acceptance Criteria
- `hunt serve` CLI command starts the web server (default port 8000)
- All existing CLI functionality accessible via the UI
- No new external DB — reuses the same SQLite file
- Works in mock mode for development/testing
- Mobile-responsive layout

---

## 5) Reporting

Generate:

```
reports/YYYY-MM-DD.md
reports/YYYY-MM-DD.json
```

Include:

- jobs discovered
- jobs scored
- applied
- skipped
- blocked
- review required
- top missing skills

---

## 6) Testing Strategy

### Unit tests

- config validation
- hashing
- scoring thresholds

### Integration tests

Mock LinkedIn UI:

- discover
- parse
- apply wizard

### Fake implementations

FakeEmbedder  
FakeLLMEvaluator  

CI must run with:

```
pytest -q
```

No internet required.

---

## 7) CLI Interface

Commands:

```
hunt init
hunt discover
hunt score
hunt apply
hunt run
hunt report
```

Global flags:

```
--mock
--real
--dry-run
--headless
--slowmo-ms
--data-dir
--log-level
```

---

## 8) Configuration

Environment variables:

```
JOBHUNTER_LLM_PROVIDER
JOBHUNTER_OPENAI_API_KEY
JOBHUNTER_DATA_DIR
```

Runtime files:

```
data/cookies.json
data/job_hunter.db
data/reports/
```

---

## 9) Definition of Done

Mock mode:

```
hunt run --profile <name> --mock
```

Produces:

- discovered jobs
- scoring results
- mock apply attempts
- daily report

Real mode:

- manual login works
- discovery works
- Easy Apply works in dry-run
- challenge detection stops system

---

## 10) Implementation Notes

- Store LinkedIn selectors in one file.
- Use retries with bounded attempts.
- Never reapply to already applied jobs.
- All thresholds configurable.

---

## 11) Phase 7 — Web GUI (✅ Complete)

Full command-and-control web dashboard using FastAPI + Jinja2 + HTMX + Pico CSS.

### Stack

- **Backend:** FastAPI with lifespan, Jinja2Templates, SSE via sse-starlette
- **Frontend:** Pico CSS (CDN) + HTMX (CDN) — no heavy JS framework
- **Shared DB:** Same SQLite as CLI with WAL mode for concurrent access
- **CLI entry:** `hunt serve --host 127.0.0.1 --port 8000`

### Pages & Routers

| Page | Route | Router | Functionality |
|---|---|---|---|
| Dashboard | `/` | `dashboard.py` | Summary cards, applied today, top missing skills |
| Jobs | `/jobs` | `jobs.py` | Filterable table, status badges, inline Queue/Skip/Review |
| Job Detail | `/api/jobs/{hash}` | `jobs.py` | Scores, missing skills, risk flags, attempts, status override |
| Profiles | `/profiles` | `profiles.py` | View/edit user profile + search profiles |
| Run Controls | `/run` | `run.py` | Trigger discover/score/apply/pipeline with live SSE progress |
| Reports | `/reports` | `reports.py` | Browse and view daily Markdown + JSON reports |
| Settings | `/settings` | `settings.py` | Toggle mock/dry-run/headless, API key management |

### API Endpoints

- `GET /api/stats/dashboard` — summary stats (JSON)
- `GET /api/jobs` — list with filters (status, company, title, pagination)
- `GET /api/jobs/{hash}` — single job detail
- `PATCH /api/jobs/{hash}/status` — manual status override
- `GET/PUT /api/profiles` — search profiles CRUD
- `GET/PUT /api/user-profile` — user profile CRUD
- `POST /api/run/{discover,score,apply,pipeline}` — trigger tasks (202/409)
- `GET /api/run/status` — SSE event stream
- `GET /api/reports` — list reports
- `GET /api/reports/{date}` — single report
- `GET/PUT /api/settings` — runtime settings

### Architecture

- `TaskManager` — single background task with SSE broadcast
- `deps.py` — FastAPI dependency injection (DB session, settings, task manager)
- `app.py` — factory with pre-injectable engine (for tests)
- Tests use `TestClient` with in-memory `StaticPool` engine

---

