# AIJobHunter Capability Reconstruction Plan

## Summary

Extend AIJobHunter from a keyword-heavy job discovery and application tool into a staged capability-reconstruction system that infers labor-market structure from noisy signals and produces more realistic opportunity recommendations.

The implementation must preserve the current `discover -> score -> apply -> report` workflow and add a parallel inference stack:

`signals -> events -> extraction -> evidence graph -> candidate model + role model -> probabilistic matching -> opportunity discovery`

This first implementation should include candidate-side modeling, contradiction-aware confidence handling, and a foundation for diagnostic dialogue, while still shipping in safe increments. The system should start from existing repo capabilities and data rather than depending on new external platforms or risky automation.

Interview, question-response, and other dialogue-derived signals should be structurally supported from the start, but they should become first-class modeled inputs in Phase 2 rather than blocking the initial market-graph foundation.

## Key Changes

### 1. Add a market and inference subsystem

Create a new `job_hunter.market` package with these modules:

- `events`
  - convert existing jobs into normalized market events
  - keep the shape extensible for future event types such as company posts, comments, and career transitions
- `extract`
  - extract explicit skills, inferred skills, tasks, problems, tools, and context from events
- `normalize`
  - canonicalize entities, aliases, and phrasing variants
- `evidence`
  - store evidence records rather than raw direct assertions
  - model `subject -> evidence -> entity` so claims retain provenance and confidence
- `graph`
  - build and refresh the heterogeneous graph
- `candidate_model`
  - derive an initial candidate capability model from existing user profile and resume-derived data
- `role_model`
  - derive role-demand models from jobs and employer-side signals
- `matching`
  - compare candidate and role models probabilistically
- `trends`
  - compute temporal demand shifts and novelty metrics
- `dialogue`
  - define interfaces and storage for future uncertainty-reduction prompts without enabling outbound social automation

### 2. Introduce an event and evidence data model

Add new tables while keeping current `jobs`, `scores`, and `application_attempts` behavior unchanged.

Minimum new tables:

- `market_events`
  - event id
  - event type
  - source type
  - job hash or future source reference
  - company
  - title
  - raw text
  - published at
  - collected at
- `market_extractions`
  - event id
  - extractor version
  - status
  - explicit skills
  - inferred skills
  - tasks
  - problems
  - tools
  - context
- `market_entities`
  - entity id
  - entity type: `skill`, `task`, `problem`, `tool`, `role`, `company`
  - canonical name
  - display name
- `market_aliases`
  - alias text
  - target entity id
- `market_evidence`
  - evidence id
  - subject type: `candidate`, `role`, `company`, `job`
  - subject key
  - entity id
  - evidence type
  - polarity
  - confidence
  - source excerpt
  - observed at
- `market_edges`
  - src entity id
  - dst entity id
  - edge type such as `co_occurs_with`, `supports`, `requires`, `used_with`, `transition_to`
  - weight
  - count
  - first seen
  - last seen
- `market_snapshots`
  - bucket start
  - entity or edge key
  - frequency
  - momentum
  - novelty
  - burst
- `dialogue_sessions`
  - session id
  - subject type: `candidate`, `role`, `company`
  - subject key
  - session type: `candidate_interview`, `manager_interview`, `role_clarification`, `diagnostic_qna`
  - source
  - started at
  - ended at
- `dialogue_turns`
  - turn id
  - session id
  - speaker
  - turn index
  - prompt text
  - response text
  - timestamp
- `dialogue_assessments`
  - session id
  - assessment type: `problem_decomposition`, `learning_velocity`, `ambiguity_tolerance`, `adaptation_speed`, `reasoning_consistency`
  - score
  - confidence
  - evidence span
  - assessor version
- `candidate_capabilities`
  - candidate key
  - entity id
  - proficiency estimate
  - confidence
  - recency
  - transferability
  - supporting evidence count
  - contradicting evidence count
- `role_requirements`
  - role key
  - entity id
  - importance
  - confidence
  - learnability
  - supporting evidence count
- `match_explanations`
  - candidate key
  - role key or job hash
  - success score
  - confidence
  - learning upside
  - mismatch risk
  - hard gaps
  - soft gaps
  - learnable gaps
  - explanation payload

Implementation constraints:

- Keep SQLite and SQLAlchemy as the persistence layer.
- Add migration support for existing databases.
- Import market ORM models during DB initialization so new tables are created with the rest of the schema.

### 3. Make tasks and problems first-class signals

Extraction and graph construction must treat tasks and problems as primary signals alongside skills.

Extraction output contract:

- explicit skills
- inferred skills
- tasks as verb-phrase units
- problems or constraints
- tools
- context such as industry, seniority, company stage, and environment clues

Defaults:

- deterministic extraction first
- optional LLM enrichment behind a typed interface
- every extracted claim stores evidence text and confidence
- reruns are idempotent per `event_id + extractor_version`

### 4. Add contradiction and uncertainty handling

Contradiction handling is part of the MVP, not a future enhancement.

Required behavior:

- candidate and role models track support and opposition separately
- conflicting evidence lowers confidence rather than being averaged blindly
- matching outputs include uncertainty and mismatch risk
- explanations identify which evidence strengthens or weakens the estimate

Initial contradiction sources:

- mismatch between job title and extracted tasks
- mismatch between claimed candidate skills and resume/profile evidence
- mismatch between written requirements and recurring task/problem patterns across similar jobs

### 5. Build a staged candidate model from existing repo data

Do not block on external systems like GitHub, peer recommendations, or behavioral telemetry.

Candidate model v1 should use data the repo already supports:

- `user_profile.yml`
- generated resume text or structured profile fields
- desired roles
- skills
- experience
- education
- industry and location preferences

The candidate model should produce:

- explicit capability evidence from current profile data
- adjacent-role possibilities via graph proximity
- conservative confidence estimates when evidence is sparse

Reserve but do not implement yet:

- GitHub-derived evidence
- career trajectory parsing beyond existing profile fields
- peer recommendations
- structured diagnostic dialogue scoring beyond the Phase 2 rollout

### 6. Add role reconstruction on top of discovered jobs

Role models must be derived from observed demand patterns rather than trusting raw titles.

Role reconstruction should:

- cluster repeated task and problem patterns across similar roles
- separate tool mentions from underlying work
- compute recurring requirement bundles by company and role family
- infer when a title likely hides a different practical role shape

This becomes the basis for:

- hidden adjacent-role suggestions
- more realistic fit estimation
- company demand summaries

### 7. Add probabilistic matching outputs

Add a new matching layer that complements current application scoring rather than replacing it immediately.

Required output shape:

- `success_score`
- `confidence`
- `learning_upside`
- `mismatch_risk`
- `hard_gaps`
- `soft_gaps`
- `learnable_gaps`
- `reason_summary`

Default MVP logic:

- score graph coverage of candidate capabilities against role requirements
- add adjacency boost for near-neighbor capabilities
- add trend boost from demand momentum
- reduce confidence where evidence is sparse or contradictory
- penalize large hard gaps more than learnable gaps

Out of scope for first delivery:

- outcome-trained causal success prediction
- fairness-aware reranking
- full professional graph reconstruction from external sources

### 8. Extend the CLI in explicit stages

Add a `market` command group.

Commands:

- `hunt market ingest`
  - create market events from existing jobs
- `hunt market extract`
  - run signal extraction
- `hunt market graph`
  - normalize entities and build the evidence graph
- `hunt market trends`
  - compute and print trend summaries
- `hunt market candidate-model --profile <name>`
  - build the candidate capability view from existing profile data
- `hunt market role-model`
  - build role archetypes from job data
- `hunt market match --profile <name>`
  - generate probabilistic opportunities and gap analysis
- `hunt market report`
  - emit a market and capability report artifact without changing current daily report behavior

Compatibility rules:

- `hunt run` stays backward-compatible
- market commands are additive and opt-in first
- later integration into `hunt run` is optional and should not block current apply behavior on market failures

### 9. Add API and dashboard support

Add a dedicated market and capability area in FastAPI and the web UI.

Minimum routes:

- `/market`
- `/api/market/overview`
- `/api/market/trends`
- `/api/market/entities`
- `/api/market/roles`
- `/api/market/companies/{company}`
- `/api/market/candidate/{profile}`
- `/api/market/match/{profile}`

Minimum UI sections:

- market overview
- emerging skills, tasks, and problems
- role archetypes
- company demand patterns
- candidate capability profile
- adjacent roles
- opportunity recommendations
- explanation panel with confidence, upside, and risks

Implementation rule:

- keep the current dashboard intact
- add new pages and views rather than redesigning the existing interface

### 10. Add a dialogue foundation, but keep active probing out of scope

The architecture should support future diagnostic dialogue, but the first implementation must not automate public interaction on LinkedIn or similar platforms.

Include now:

- dialogue schema and storage
- prompt templates for future uncertainty-reduction questions
- optional local/manual evaluation hooks for later use

Explicitly exclude now:

- autonomous commenting on LinkedIn posts
- outbound social interaction automation
- live public probing behavior

## Implementation Order

### Stage 1: Market graph foundation

- Add market ORM models, migrations, and repo helpers.
- Build `market ingest`, `market extract`, and `market graph`.
- Support only job-derived events at first.
- Ensure idempotency and offline testability.

### Stage 2: Trends and role reconstruction

- Add trend snapshot computation.
- Add role archetype reconstruction from task/problem bundles.
- Add first-class interview and question-derived signals as evidence inputs.
- Add `dialogue_sessions`, `dialogue_turns`, and `dialogue_assessments`.
- Support candidate interview notes, recruiter or manager clarifications, and diagnostic Q&A transcripts as structured evidence.
- Expose trends and role summaries through CLI and API.

### Stage 3: Candidate model and probabilistic matching

- Build candidate capability projection from existing profile data.
- Incorporate Phase 2 dialogue assessments into candidate and role models.
- Add contradiction-aware confidence scoring.
- Add opportunity recommendations, adjacent roles, and gap analysis.

### Stage 4: UI and reporting

- Add market and capability pages and APIs.
- Extend reports with market and matching sections when data exists.
- Preserve current report behavior when no market build has been run.

### Stage 5: Dialogue-ready architecture

- Add dialogue data structures and evaluation hooks.
- Keep outbound interaction disabled and clearly out of scope for this phase.

## Tests

Add offline tests for:

- market DB initialization and migration from existing DBs
- event ingest idempotency
- deterministic extraction of skills, tasks, problems, and tools
- entity normalization and alias resolution
- graph edge aggregation and evidence storage
- trend metrics across multiple dates
- role reconstruction from repeated job patterns
- candidate capability generation from seeded profile data
- ingestion and storage of dialogue sessions and turns
- dialogue-derived assessments for reasoning, learning speed, and adaptation traits
- contradiction handling and confidence reduction
- match output shape and ranking stability
- CLI commands under mock fixtures
- API endpoints and dashboard rendering with seeded market data

Use seeded mock job descriptions and fake extractors or evaluators so the full test suite remains offline.

## Acceptance Criteria

The implementation is complete when:

- existing job data can be converted into market events, structured extractions, evidence records, and graph entities
- tasks and problems are visible alongside skills in stored outputs and UI summaries
- role models are derived from observed demand patterns, not only titles
- candidate models can be built from current profile and resume-derived data
- match outputs include success score, confidence, learning upside, and mismatch risk
- contradiction-aware confidence handling is implemented
- CLI, API, and dashboard expose market trends and recommendation outputs
- the new pipeline remains additive and does not break current discover/score/apply behavior
- all new behavior is covered by offline tests

## Assumptions and Defaults

- The implementation target is the repo in `aijobhunter_repo`.
- New work is additive; existing automation logic should not be rewritten.
- SQLite remains the storage engine for this phase.
- Jobs are the only required event source for the first implementation, even though the architecture must allow future posts, comments, and transitions.
- Interview, manager-question, and candidate-response signals are explicitly supported in the schema now, but become operationally important starting in Phase 2.
- Candidate modeling in this phase is evidence-based but lightweight, using current profile artifacts rather than full behavioral telemetry.
- Diagnostic dialogue is architected now, interview and Q&A evidence are added in Phase 2, and active social probing is deferred.
- Fairness-aware ranking, ESCO integration, and outcome-trained success prediction are future extensions, not required for first delivery.
