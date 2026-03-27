# AGENTS.md

## Project Overview

AI Job Hunter — automated LinkedIn job discovery, scoring, and application. Python 3.13+, managed with **uv**. Source lives in `src/job_hunter/`, installed as a `hatchling` package. Entry point: `hunt` CLI (Typer).

## Architecture

Two pipelines sharing one SQLite DB:

- **Application pipeline**: **Discover → Score → Queue → Apply → Report** (`orchestration/pipeline.py`)
- **Market Intelligence pipeline** (Stages 1–4 complete + operational integration): **Ingest → Extract → Graph → Trends → Role Model → Candidate Model → Match → Report** (`market/` package); dialogue evaluation and opportunity scoring with web UI fully operational; single-command `hunt market run-all` and web "Market Analysis" button for one-click pipeline execution; market-enhanced scoring via `compute_market_boost()`

### Core Packages

- **config/** — `AppSettings` (Pydantic Settings, env prefix `JOBHUNTER_`, reads `.env`), `SearchProfile`, `UserProfile`, `ScheduleConfig`, `ScheduleRunRecord`, `PipelineMode` as Pydantic models; YAML loader/saver in `loader.py`; `save_settings_env()` persists settings to `.env` via `python-dotenv` `set_key()`
- **db/** — SQLAlchemy 2.0 ORM (`Job`, `Score`, `ApplicationAttempt`) over SQLite with WAL mode; `repo.py` has all CRUD helpers; dedup via SHA-256 hash (`utils/hashing.py`); `migrations.py` is a stub for future Alembic integration
- **linkedin/** — Playwright browser automation: `session.py` (cookie auth), `discover.py` (search + pagination), `parse.py` (HTML→data), `apply.py` (Easy Apply wizard), `forms.py`/`form_filler_llm.py` (LLM-powered form filling), `selectors.py` (CSS/XPath constants)
- **matching/** — `embeddings.py` (OpenAI embeddings + cosine similarity), `llm_eval.py` (GPT fit evaluation returning structured JSON), `scoring.py` (combines both into a decision; `compute_market_boost()` enriches scores with market opportunity signals when market data exists), `description_cleaner.py`
- **orchestration/** — `pipeline.py` (async `run_pipeline()` wiring all stages), `policies.py` (rate limits, blacklists, daily caps)
- **reporting/** — `report.py` (`generate_report()` producing daily Markdown + JSON summaries under `data/reports/`)
- **notifications/** — `email.py` with provider pattern: `BaseNotifier` (ABC) → `SmtpNotifier` (SMTP with optional auth, TLS, `last_error` diagnostics), `ResendNotifier` (API-key-based, free tier), `FakeNotifier` (tests); `build_notifier_from_settings()` auto-selects provider (Resend → SMTP fallback → None); `send_pipeline_summary()` and `send_test_email()` helpers
- **scheduling/** — `scheduler.py`: `PipelineScheduler` wrapping APScheduler `AsyncIOScheduler`; cron trigger from `ScheduleConfig`; integrates with `TaskManager` (one-task-at-a-time); `wire(app_state)` + `start(config)` + `stop()` + `reschedule(config)`; records `ScheduleRunRecord` history to YAML; sends notification email on completion
- **auth/** — `models.py` (`User` ORM — credentials, per-user settings overrides, email/notification prefs), `repo.py` (CRUD: `create_user`, `authenticate_user`, `update_user_profile`, `change_user_password`, `update_user_settings`, per-user data dirs), `security.py` (bcrypt password hashing, JWT access tokens via `python-jose`, standalone admin-password tokens); first registered user is auto-promoted to admin; per-user OpenAI keys, runtime flags, and email settings stored on the `User` row (NULL = inherit global)
- **web/** — FastAPI + HTMX + Pico CSS; app factory in `app.py` (`app.state.dotenv_path` for settings persistence); login-required middleware (JWT cookie or `Authorization: Bearer` header); DI via `deps.py` (DB session, settings, task manager, `get_current_user`, `get_effective_settings` with per-user overlays, `require_admin` gate); routers under `web/routers/` (auth, account, admin, dashboard, jobs, onboarding, profiles, reports, resume_review, run, settings, schedule); `TaskManager` in `task_manager.py` runs one background task at a time with SSE event broadcasting
- **profile/** — `extract.py` (PDF text + LinkedIn scraping), `generator.py` (LLM profile generation)
- **utils/** — `hashing.py` (SHA-256 job dedup), `logging.py` (namespaced logger setup), `rate_limit.py` (`RateLimiter` — token-bucket for browser automation), `retry.py` (`retry` decorator with exponential back-off, works for sync and async functions)

### Market Intelligence Package

Self-contained under `src/job_hunter/market/`. Detailed execution plan: **`agentic_data/AIJobHunter_Coding_Agent_Plan.md`** (the single authoritative plan).

#### Implemented (Stage 1 — graph foundation)

- **market/db_models.py** — 15 tables across all stages (created by `init_db()`): Stage 1 (`market_events`, `market_extractions`, `market_entities`, `market_aliases`, `market_evidence`, `market_edges`, `market_snapshots`), Stage 2 (`dialogue_sessions`, `dialogue_turns`, `dialogue_assessments`), Stage 3 (`candidate_capabilities`, `role_requirements`, `match_explanations`); shared `Base` from `db/models.py`
- **market/schemas.py** — `ExtractionInput` / `ExtractionResult` Pydantic models for extractor I/O
- **market/events.py** — `ingest_jobs(session)` converts jobs → `market_events` (idempotent via `UNIQUE(event_type, job_hash)`)
- **market/extract.py** — `MarketExtractor` base + `HeuristicExtractor` (keyword/pattern) + `OpenAIMarketExtractor` (LLM) + `FakeMarketExtractor` (tests); `run_extraction(session, extractor)` orchestrates (idempotent via `UNIQUE(event_id, extractor_version)`)
- **market/normalize.py** — `canonicalize()`, `resolve_alias()` (YAML dict at `market/data/aliases.yml` + RapidFuzz fuzzy matching), `resolve_or_create_entity()` (find-or-create with alias and fuzzy resolution)
- **market/repo.py** — CRUD helpers for all market tables: events, extractions, entities, evidence, edges, snapshots
- **market/graph/builder.py** — `build_graph(session)` materialises entities + evidence + co-occurrence edges from ungraphed extractions
- **market/graph/metrics.py** — `to_networkx(session)`, `export_graphml()`, `export_json()` (NetworkX export)
- **market/graph/nx_export.py** — re-exports from `metrics.py`
- **market/data/aliases.yml** — curated technology alias dictionary
- Dependencies: `rapidfuzz>=3.0`, `networkx>=3.0`

#### Implemented (Stage 2 — trends, roles, dialogue, web)

- **market/trends/compute.py** — `compute_trends(session)` computes frequency, momentum, novelty, burst per entity and persists `market_snapshots`
- **market/trends/queries.py** — `recent_entity_counts()`, `entity_frequency_by_bucket()`, `all_entity_bucket_counts()`, `get_latest_snapshots()` — SQL helpers for trend analysis
- **market/role_model.py** — `build_role_archetypes(session)` clusters extractions by normalised title, computes entity importance, stores `role_requirements`; `get_role_archetypes(session)` reads them back
- **market/dialogue.py** — Full CRUD for `dialogue_sessions`, `dialogue_turns`, `dialogue_assessments`; `ingest_dialogue_evidence()` converts dialogue signals into `market_evidence` records
- **market/report.py** — `generate_market_report(session, out_dir)` produces Markdown + JSON market intelligence reports with stats, top entities, rising trends, and role archetypes
- **market/web/router.py** — FastAPI router with: `/market` (HTML page), `/api/market/overview`, `/api/market/trends`, `/api/market/entities`, `/api/market/roles`, `/api/market/companies/{company}` (JSON APIs)
- **market/cli.py** — `hunt market ingest|extract|graph|export|trends|role-model|report` — all commands fully implemented
- Market router registered in `web/app.py`; nav link added to `base.html`

#### Implemented (Stage 3 — candidate model, matching, opportunity scoring)

- **market/candidate_model.py** — `build_candidate_capabilities(session, profile, candidate_key)` projects `UserProfile` skills, programming languages, desired roles, and education into `CandidateCapability` rows linked to `MarketEntity` records; incorporates dialogue-derived evidence (supporting/contradicting); contradiction-aware confidence scoring with floor at 0.1; `get_candidate_capabilities()` reader; idempotent (delete-before-insert)
- **market/matching.py** — `match_candidate_to_roles(session, candidate_key, role_keys)` compares `CandidateCapability` vs `RoleRequirement`, classifies gaps (hard/soft/learnable), computes `success_score`, `confidence`, `learning_upside`, `mismatch_risk`; graph proximity boost via NetworkX shortest-path (max 3 hops); trend momentum boost from latest snapshots; persists `MatchExplanation` rows; idempotent
- **market/opportunity.py** — `score_opportunities(session, candidate_key)` augments match scores with trend-weighted opportunity ranking; `find_adjacent_roles(session, candidate_key, top_n)` discovers reachable roles via graph proximity; `gap_analysis(session, candidate_key, role_key)` returns per-requirement breakdown with coverage status
- **market/repo.py** — added CRUD helpers: `get_capabilities_for_candidate()`, `delete_capabilities_for_candidate()`, `get_match_explanations()`, `delete_match_explanations()`, `get_evidence_for_subject()`
- **market/cli.py** — added `hunt market candidate-model --profile <name>` and `hunt market match --profile <name>` commands
- **market/web/router.py** — added `GET /api/market/candidate/{profile}` and `GET /api/market/match/{profile}` endpoints

#### Implemented (Stage 4 — UI panels, extended reporting, dialogue evaluation)

- **market/web/router.py** — added `GET /api/market/gap/{profile}/{role}` (gap analysis), `GET /api/market/dialogue/sessions` (session list), `GET /api/market/dialogue/sessions/{id}` (session detail with turns and assessments); company demand data (`top_companies`) added to overview and `/market` page
- **market/web/templates/market.html** — added 🏢 Top Companies panel between Rising Trends and Role Archetypes; added gap analysis and dialogue API links to the endpoints list
- **market/dialogue_eval.py** — `DialogueEvaluator` base class + `RuleBasedDialogueEvaluator` (keyword-based scoring of depth, breadth, ambiguity tolerance, adaptation speed, reasoning consistency) + `FakeDialogueEvaluator` (deterministic, for tests); `UNCERTAINTY_PROMPT_TEMPLATES` (per-`AssessmentType` prompt templates for uncertainty-reduction questions); `generate_probing_questions()` picks low-confidence capabilities and generates targeted follow-up prompts
- **market/dialogue.py** — added `get_all_sessions()` helper (returns all sessions newest-first)
- **market/cli.py** — added `hunt market dialogue-list` (list all sessions) and `hunt market dialogue-evaluate --evaluator rule-based|fake` (evaluate un-assessed sessions, persist assessments, idempotent)
- **reporting/report.py** — `_collect_market_section()` collects entity/edge/event counts, top skills, rising trends, and opportunity matches into the daily report; `_render_markdown()` renders Market Intelligence section with skills table, rising trends, and opportunity matches

#### Operational Integration (pipeline, web, CLI, scoring)

- **market/pipeline.py** — `run_market_pipeline(session, extractor, profile, candidate_key)` chains all 7 market steps sequentially (ingest → extract → graph → trends → role-model → candidate-model → match); commits after each step for partial progress; returns summary dict with counts; shared by CLI and web
- **market/cli.py** — added `hunt market run-all --extractor heuristic|openai|fake --profile <name>` single-command full pipeline execution; loads user profile from `user_profile.yml` when available
- **web/routers/run.py** — added `POST /api/run/market` endpoint; runs `run_market_pipeline()` as a background task via `TaskManager` with SSE progress; auto-selects extractor (fake in mock mode, OpenAI if key available, heuristic otherwise); loads user profile for candidate model
- **web/templates/run.html** — added 📊 Market Analysis card with "Analyse Market" button alongside existing Discover/Score/Apply/Pipeline cards
- **matching/scoring.py** — added `compute_market_boost(session, job_title, candidate_key)` that looks up the best-matching role archetype for a job title and returns opportunity signals (`market_score`, `trend_boost`, `role_key`, `hard_gaps`, `learnable_gaps`); safe to call when market tables are empty (returns neutral values)

#### Future (Stage 5+ — extended dialogue, outcome learning)

- Outcome-trained causal success prediction
- GitHub-derived evidence and career trajectory parsing
- Autonomous social probing (explicitly out of scope)
- Fairness-aware reranking and ESCO integration

## Build & Run

```bash
uv sync                                    # install all deps (including dev group)
uv run hunt --help                         # CLI help
uv run hunt --mock --dry-run run --profile default   # offline test of full pipeline
uv run hunt --real --dry-run serve          # web GUI at localhost:8000
```

## Testing

All tests run **fully offline** — no API keys or internet. Run with:

```bash
uv run pytest -q                # quick
uv run pytest -v                # verbose
uv run pytest tests/test_web.py # single file
uv run pytest -k "test_upsert" # pattern match
```

Key test patterns:
- **Fake implementations** for all external services: `FakeEmbedder` (fixed similarity), `FakeLLMEvaluator` (deterministic scores), `FakeProfileGenerator`, `FakeMarketExtractor`, `FakeDialogueEvaluator`, `FakeNotifier` — found in same modules as real implementations (e.g., `matching/embeddings.py`, `matching/llm_eval.py`, `profile/generator.py`, `market/extract.py`, `market/dialogue_eval.py`, `notifications/email.py`)
- **In-memory SQLite** via `get_memory_engine()` from `db/repo.py` (uses `StaticPool` for thread safety with FastAPI `TestClient`)
- **Mock LinkedIn** — local HTTP server serving HTML fixtures from `linkedin/mock_site/fixtures/`; toggled via `--mock` flag
- **Web tests** use `TestClient` with pre-injected `app.state.engine` and `app.state.dotenv_path = tmp_path / ".env"` — see `test_web.py` `test_app` fixture for the pattern
- Tests use `tmp_path` for data directories, never touch real `data/` or `.env`

## Conventions

- **Async pipeline, sync DB**: `run_pipeline()` is `async def`; SQLAlchemy sessions are synchronous. Playwright calls are async. Synchronous scoring calls use `await asyncio.to_thread()` to keep the event loop free for SSE progress streaming.
- **SSE progress streaming**: `TaskManager` installs a log handler on the `job_hunter` root logger; `_broadcast()` is thread-safe via `call_soon_threadsafe`. All synchronous pipeline work (scoring loops, market pipeline, report generation) MUST run in `asyncio.to_thread()` so SSE events stream in real time. See `web/routers/run.py` for the pattern.
- **Provider pattern**: External services (embeddings, LLM eval, profile generation, market extraction, dialogue evaluation, email notifications) use base class + real + fake implementations in the same file. Add new providers by subclassing `Embedder`, `LLMEvaluator`, `ProfileGenerator`, `MarketExtractor`, `DialogueEvaluator`, or `BaseNotifier`.
- **All enums are `str, enum.Enum`** dual-inheriting for JSON serialization (`JobStatus`, `Decision`, `ApplicationResult`, `LogLevel`, `PipelineMode`, `MarketEventType`, `EntityType`, `EdgeType`, etc.).
- **Config layering**: `.env` → env vars → CLI flags. `load_settings()` merges them; `None` CLI values are filtered out so defaults aren't overridden.
- **Settings persistence**: `PUT /api/settings` updates `app.state.settings` in memory AND calls `save_settings_env()` to write through to `.env` via `python-dotenv` `set_key()`. The `.env` path is stored on `app.state.dotenv_path` (set to `tmp_path / ".env"` in tests to avoid polluting the real file).
- **Job dedup**: SHA-256 hash of `(external_id, title, company)` via `utils/hashing.py`. `upsert_job()` checks hash before insert.
- **Market idempotency**: `UNIQUE(event_type, job_hash)` for events; `UNIQUE(event_id, extractor_version)` for extractions; `UNIQUE(entity_type, canonical_name)` for entities; `UNIQUE(src_entity_id, dst_entity_id, edge_type)` for edges. Re-running any stage produces no duplicates.
- **Web DI**: Dependencies (`get_db`, `get_settings`, `get_task_manager`, `get_current_user`, `get_effective_settings`) read from `request.app.state` — set during lifespan or pre-injected in tests. `get_effective_settings()` overlays per-user values from the `User` row onto global `AppSettings`.
- **Authentication**: JWT-based sessions via `access_token` cookie (7-day expiry). `login_required_middleware` redirects unauthenticated page requests to `/login` and returns 401 for API calls. Public paths: `/login`, `/register`, `/api/auth/`, `/api/health`, `/static/`, `/favicon.ico`. Admin panel protected by a separate `admin_token` cookie (1-hour, standalone password gate via `require_admin`).
- **Multi-user**: Each user has a per-user data directory (`data/users/<user_id>/`). Per-user settings (OpenAI key, runtime flags, email config) stored on the `User` row; NULL means inherit global `AppSettings`. `get_effective_settings()` merges these overlays. First registered user is auto-promoted to admin. Account settings (email, display name, password) managed at `/account`.
- **Scheduling**: `PipelineScheduler` wraps APScheduler's `AsyncIOScheduler`. Requires a running event loop; tests must be `@pytest.mark.asyncio async`. Config persisted to YAML via `model_dump(mode="json")` (not plain `model_dump()` — avoids Python enum YAML tags).
- **Logging**: All loggers namespaced under `job_hunter.*` (e.g., `job_hunter.matching.scoring`, `job_hunter.market.graph.builder`, `job_hunter.scheduling`). Configured via `utils/logging.py`.
- **DO NOT modify existing tables** (`jobs`, `scores`, `application_attempts`) when extending market layer.

## Planning Documents

- **`agentic_data/AIJobHunter_Coding_Agent_Plan.md`** — the single authoritative execution plan for the Market Intelligence MVP and Phase 2 roadmap. Includes DB schema, file structure, implementation phases, task breakdown, and testing plan.
- **`agentic_data/AIJobHunter_Market_Intelligence_Plan.md`** — original detailed execution reference (superseded, kept as historical context).

## Data Files

Runtime data lives in `data/` (configurable via `--data-dir` or `JOBHUNTER_DATA_DIR`):
- `job_hunter.db` — SQLite database (includes both application and market tables)
- `user_profile.yml` / `profiles.yml` — YAML config (Pydantic models serialized)
- `cookies.json` — LinkedIn session cookies
- `schedule.yml` — scheduler configuration (time, days, pipeline mode)
- `schedule_history.yml` — last 100 scheduled run records
- `reports/` — daily Markdown + JSON reports

