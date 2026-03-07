"""CLI entry point — ``hunt`` command with subcommands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich import print as rprint

from job_hunter.config.loader import load_profiles, load_settings, load_user_profile, save_profiles, save_user_profile
from job_hunter.config.models import LogLevel
from job_hunter.db.repo import get_engine, init_db
from job_hunter.utils.logging import setup_logging

app = typer.Typer(
    name="hunt",
    help="AI Job Hunter — discover, score, and apply to LinkedIn jobs.",
    add_completion=False,
)


# ---------------------------------------------------------------------------
# Global state passed through typer.Context
# ---------------------------------------------------------------------------

class _State:
    """Mutable bag attached to ``typer.Context.obj`` by the callback."""
    def __init__(self) -> None:
        from job_hunter.config.models import AppSettings
        self.settings: AppSettings = AppSettings()


@app.callback()
def main(
    ctx: typer.Context,
    mock: Annotated[bool, typer.Option("--mock", help="Use mock LinkedIn site")] = False,
    real: Annotated[bool, typer.Option("--real", help="Use real LinkedIn (requires cookies)")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Run without submitting applications")] = False,
    headless: Annotated[bool, typer.Option("--headless/--no-headless", help="Browser headless mode")] = True,
    slowmo_ms: Annotated[int, typer.Option("--slowmo-ms", help="Slow-motion delay (ms)")] = 0,
    data_dir: Annotated[Optional[Path], typer.Option("--data-dir", help="Path to data directory")] = None,
    log_level: Annotated[LogLevel, typer.Option("--log-level", help="Log verbosity")] = LogLevel.INFO,
) -> None:
    """Global options shared by all subcommands."""
    state = _State()
    state.settings = load_settings(
        mock=mock or (not real and None),
        dry_run=dry_run or None,
        headless=headless,
        slowmo_ms=slowmo_ms,
        data_dir=data_dir,
        log_level=log_level,
    )
    setup_logging(state.settings.log_level.value)
    ctx.ensure_object(dict)
    ctx.obj["state"] = state


def _get_state(ctx: typer.Context) -> _State:
    return ctx.obj["state"]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command()
def init(ctx: typer.Context) -> None:
    """Initialise the database and data directory."""
    state = _get_state(ctx)
    data_dir = state.settings.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "reports").mkdir(exist_ok=True)

    engine = get_engine(data_dir)
    init_db(engine)
    rprint(f"[green]✓[/green] Database initialised at {data_dir / 'job_hunter.db'}")


@app.command()
def login(ctx: typer.Context) -> None:
    """Open a browser for manual LinkedIn login and save cookies."""
    import asyncio

    from job_hunter.linkedin.session import LinkedInSession

    state = _get_state(ctx)
    cookies_path = state.settings.data_dir / "cookies.json"

    session = LinkedInSession(cookies_path=cookies_path)

    rprint("[bold]Opening browser for LinkedIn login…[/bold]")
    rprint("Please log in manually. The browser will close automatically once login is detected.")

    asyncio.run(
        session.login(
            headless=False,  # Always visible for manual login
            slowmo_ms=state.settings.slowmo_ms,
        )
    )

    rprint(f"[green]✓[/green] Cookies saved to {cookies_path}")


@app.command()
def profile(
    ctx: typer.Context,
    resume: Annotated[Optional[Path], typer.Option("--resume", "-r", help="Path to resume PDF", exists=True, dir_okay=False)] = None,
    linkedin: Annotated[Optional[str], typer.Option("--linkedin", "-l", help="LinkedIn profile URL or path to PDF")] = None,
    show: Annotated[bool, typer.Option("--show", help="Display the current user profile")] = False,
) -> None:
    """Generate search profiles from your resume and LinkedIn profile."""
    state = _get_state(ctx)
    data_dir = state.settings.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    user_profile_path = data_dir / "user_profile.yml"
    profiles_path = data_dir / "profiles.yml"

    if show:
        if not user_profile_path.exists():
            rprint("[red]✗[/red] No user profile found. Run [bold]hunt profile --resume your_cv.pdf[/bold] first.")
            raise typer.Exit(1)
        user_prof = load_user_profile(user_profile_path)
        rprint("\n[bold cyan]User Profile[/bold cyan]")
        rprint(f"  Name:        {user_prof.name}")
        rprint(f"  Title:       {user_prof.title}")
        rprint(f"  Summary:     {user_prof.summary}")
        rprint(f"  Skills:      {', '.join(user_prof.skills)}")
        rprint(f"  Experience:  {user_prof.experience_years} years")
        rprint(f"  Seniority:   {user_prof.seniority_level}")
        rprint(f"  Locations:   {', '.join(user_prof.preferred_locations)}")
        rprint(f"  Roles:       {', '.join(user_prof.desired_roles)}")
        rprint(f"  Education:   {', '.join(user_prof.education)}")
        rprint(f"  Languages:   {', '.join(user_prof.languages)}")

        if profiles_path.exists():
            search_profiles = load_profiles(profiles_path)
            rprint(f"\n[bold cyan]Search Profiles[/bold cyan] ({len(search_profiles)})")
            for sp in search_profiles:
                rprint(f"\n  [bold]{sp.name}[/bold]")
                rprint(f"    Keywords:    {', '.join(sp.keywords)}")
                rprint(f"    Location:    {sp.location}")
                rprint(f"    Remote:      {sp.remote}")
                rprint(f"    Seniority:   {', '.join(sp.seniority)}")
                rprint(f"    Fit score ≥: {sp.min_fit_score}")
                rprint(f"    Similarity ≥:{sp.min_similarity}")
                rprint(f"    Max/day:     {sp.max_applications_per_day}")
        return

    # Generation mode — resume is required
    if resume is None:
        rprint("[red]✗[/red] --resume is required when generating a profile.")
        raise typer.Exit(1)

    from job_hunter.profile.extract import extract_texts
    from job_hunter.profile.generator import OpenAIProfileGenerator, ProfileGenerator

    rprint("[bold]Extracting text from sources…[/bold]")
    extracted = extract_texts(resume, linkedin, headless=state.settings.headless)
    rprint(f"  Extracted {len(extracted):,} characters")

    api_key = state.settings.openai_api_key
    if not api_key:
        rprint("[red]✗[/red] JOBHUNTER_OPENAI_API_KEY is not set. Export it and try again.")
        raise typer.Exit(1)

    generator: ProfileGenerator = OpenAIProfileGenerator(api_key=api_key)

    rprint("[bold]Generating profile via LLM…[/bold]")
    result = generator.generate(extracted)

    # Check for overwrite
    if profiles_path.exists():
        if not typer.confirm(f"\n{profiles_path} already exists. Overwrite?", default=False):
            rprint("[yellow]Aborted.[/yellow]")
            raise typer.Exit(0)

    save_user_profile(result.user_profile, user_profile_path)
    save_profiles(result.search_profiles, profiles_path)

    rprint(f"\n[green]✓[/green] User profile saved to {user_profile_path}")
    rprint(f"[green]✓[/green] {len(result.search_profiles)} search profile(s) saved to {profiles_path}")

    # Show summary
    rprint(f"\n[bold cyan]{result.user_profile.name}[/bold cyan] — {result.user_profile.title}")
    rprint(f"  {result.user_profile.summary}")
    rprint(f"  Skills: {', '.join(result.user_profile.skills[:10])}")
    rprint(f"  Search profiles: {', '.join(sp.name for sp in result.search_profiles)}")


@app.command()
def discover(
    ctx: typer.Context,
    profile: Annotated[str, typer.Option("--profile", "-p", help="Search-profile name")] = "default",
) -> None:
    """Discover fresh LinkedIn jobs for *profile*."""
    import asyncio

    from job_hunter.db.models import Job
    from job_hunter.db.repo import get_engine, make_session, upsert_job
    from job_hunter.linkedin.discover import discover_jobs

    state = _get_state(ctx)
    settings = state.settings

    rprint(f"[bold]Discovering jobs[/bold] (profile={profile}, mock={settings.mock})")

    # Load search profile params for real discovery
    keywords: list[str] = []
    location = ""
    remote = False
    seniority: list[str] = []
    profiles_path = settings.data_dir / "profiles.yml"
    if profiles_path.exists():
        profs = load_profiles(profiles_path)
        matching = [p for p in profs if p.name == profile]
        if matching:
            keywords = matching[0].keywords
            location = matching[0].location
            remote = matching[0].remote
            seniority = matching[0].seniority

    job_dicts = asyncio.run(
        discover_jobs(
            profile_name=profile,
            mock=settings.mock,
            headless=settings.headless,
            slowmo_ms=settings.slowmo_ms,
            cookies_path=str(settings.data_dir / "cookies.json"),
            keywords=keywords,
            location=location,
            remote=remote,
            seniority=seniority,
        )
    )

    if not job_dicts:
        rprint("[yellow]No jobs discovered.[/yellow]")
        return

    # Persist to DB
    engine = get_engine(settings.data_dir)
    init_db(engine)
    session = make_session(engine)

    new_count = 0
    for jd in job_dicts:
        job = Job(**jd)
        upsert_job(session, job)
        new_count += 1

    session.commit()
    rprint(f"[green]✓[/green] Discovered {new_count} job(s) and saved to database")


@app.command()
def score(
    ctx: typer.Context,
    profile: Annotated[str, typer.Option("--profile", "-p", help="Search-profile name")] = "default",
) -> None:
    """Compute fit-scores for discovered jobs."""
    from job_hunter.config.loader import load_user_profile as _load_up
    from job_hunter.db.models import Job, JobStatus, Score
    from job_hunter.db.repo import get_engine, make_session, save_score
    from job_hunter.matching.embeddings import Embedder, FakeEmbedder, OpenAIEmbedder
    from job_hunter.matching.llm_eval import FakeLLMEvaluator, LLMEvaluator, OpenAILLMEvaluator
    from job_hunter.matching.scoring import compute_score, decide_job_status, decision_to_db

    state = _get_state(ctx)
    settings = state.settings

    # --- Load resume text ---
    resume_text = ""
    user_profile_path = settings.data_dir / "user_profile.yml"
    resume_txt_path = settings.data_dir / "resume.txt"

    if user_profile_path.exists():
        up = _load_up(user_profile_path)
        # Build a textual representation of the user profile for scoring
        resume_text = (
            f"{up.name}\n{up.title}\n{up.summary}\n"
            f"Skills: {', '.join(up.skills)}\n"
            f"Experience: {up.experience_years} years\n"
            f"Education: {', '.join(up.education)}\n"
            f"Desired roles: {', '.join(up.desired_roles)}\n"
        )
    elif resume_txt_path.exists():
        resume_text = resume_txt_path.read_text(encoding="utf-8")

    if not resume_text.strip():
        rprint("[red]✗[/red] No resume data found. Run [bold]hunt profile[/bold] first, or place a resume.txt in the data dir.")
        raise typer.Exit(1)

    # --- Load search profile thresholds ---
    profiles_path = settings.data_dir / "profiles.yml"
    min_fit_score = 75
    min_similarity = 0.35
    if profiles_path.exists():
        from job_hunter.config.loader import load_profiles as _load_profs
        profs = _load_profs(profiles_path)
        matching = [p for p in profs if p.name == profile]
        if matching:
            min_fit_score = matching[0].min_fit_score
            min_similarity = matching[0].min_similarity

    # --- Choose embedder + evaluator ---
    embedder: Embedder
    evaluator: LLMEvaluator

    if settings.mock:
        rprint("[bold]Scoring in mock mode[/bold] (using fake embedder + evaluator)")
        embedder = FakeEmbedder(fixed_similarity=0.5)
        evaluator = FakeLLMEvaluator(fit_score=80, decision="apply")
    else:
        api_key = settings.openai_api_key
        if not api_key:
            rprint("[red]✗[/red] JOBHUNTER_OPENAI_API_KEY is not set. Use --mock for testing or export the key.")
            raise typer.Exit(1)
        embedder = OpenAIEmbedder(api_key=api_key)
        evaluator = OpenAILLMEvaluator(api_key=api_key)

    # --- Score all unscored jobs ---
    engine = get_engine(settings.data_dir)
    init_db(engine)
    session = make_session(engine)

    from sqlalchemy import select
    all_jobs = session.execute(select(Job)).scalars().all()
    scored_hashes = set(
        row[0] for row in session.execute(select(Score.job_hash)).all()
    )
    unscored_jobs = [j for j in all_jobs if j.hash not in scored_hashes]
    if not unscored_jobs:
        rprint("[yellow]All jobs already scored.[/yellow]")
        return

    rprint(f"[bold]Scoring {len(unscored_jobs)} job(s)…[/bold]")

    scored = 0
    queued = 0
    skipped = 0
    review = 0

    for job in unscored_jobs:
        if not job.description_text:
            rprint(f"  [dim]Skipping {job.title} — no description[/dim]")
            continue
        result = compute_score(
            resume_text=resume_text,
            job_description=job.description_text,
            embedder=embedder,
            llm_evaluator=evaluator,
        )

        # Save score row
        score_row = Score(
            job_hash=job.hash,
            resume_id="default",
            embedding_similarity=result["embedding_similarity"],
            llm_fit_score=result["llm_fit_score"],
            missing_skills=result["missing_skills"],
            risk_flags=result["risk_flags"],
            decision=decision_to_db(result["decision"]),
        )
        save_score(session, score_row)

        # Update job status
        new_status = decide_job_status(
            easy_apply=job.easy_apply,
            fit_score=result["llm_fit_score"],
            similarity=result["embedding_similarity"],
            decision_str=result["decision"],
            min_fit_score=min_fit_score,
            min_similarity=min_similarity,
        )
        job.status = new_status
        scored += 1

        if new_status == JobStatus.QUEUED:
            queued += 1
        elif new_status == JobStatus.SKIPPED:
            skipped += 1
        elif new_status == JobStatus.REVIEW:
            review += 1

        rprint(
            f"  {job.title} @ {job.company}: "
            f"fit={result['llm_fit_score']} sim={result['embedding_similarity']:.2f} "
            f"→ [bold]{new_status.value}[/bold]"
        )

    session.commit()
    rprint(
        f"\n[green]✓[/green] Scored {scored} job(s): "
        f"{queued} queued, {skipped} skipped, {review} review"
    )


@app.command()
def apply(
    ctx: typer.Context,
    profile: Annotated[str, typer.Option("--profile", "-p", help="Search-profile name")] = "default",
) -> None:
    """Apply to qualified jobs via Easy Apply."""
    import asyncio

    from job_hunter.db.models import ApplicationAttempt, ApplicationResult, Job, JobStatus
    from job_hunter.db.repo import get_engine, get_jobs_by_status, make_session, save_attempt
    from job_hunter.linkedin.apply import apply_to_job
    from job_hunter.orchestration.policies import can_apply_today

    state = _get_state(ctx)
    settings = state.settings

    # Load daily cap from profile
    max_per_day = 25
    profiles_path = settings.data_dir / "profiles.yml"
    if profiles_path.exists():
        profs = load_profiles(profiles_path)
        matching = [p for p in profs if p.name == profile]
        if matching:
            max_per_day = matching[0].max_applications_per_day

    # Determine resume path
    resume_path = settings.data_dir / "resume.pdf"
    if not resume_path.exists():
        # Fall back to the test fixture for mock mode
        resume_path = Path("tests/fixtures/resume.txt")

    engine = get_engine(settings.data_dir)
    init_db(engine)
    session = make_session(engine)

    # Build form answers from user profile
    profile_form_answers: dict[str, str] = {}
    user_profile_dict: dict | None = None
    try:
        from job_hunter.config.loader import load_user_profile
        user_profile_path = settings.data_dir / "user_profile.yml"
        if user_profile_path.exists():
            user_profile = load_user_profile(user_profile_path)
            profile_form_answers = user_profile.build_form_answers()
            user_profile_dict = user_profile.model_dump()
    except Exception:
        pass

    queued_jobs = get_jobs_by_status(session, JobStatus.QUEUED)
    if not queued_jobs:
        rprint("[yellow]No queued jobs to apply to.[/yellow]")
        return

    rprint(
        f"[bold]Applying to {len(queued_jobs)} job(s)[/bold] "
        f"(mock={settings.mock}, dry_run={settings.dry_run}, max/day={max_per_day})"
    )

    applied = 0
    dry_runs = 0
    failed = 0
    blocked = 0
    applied_today = 0  # TODO: count from DB for today

    for job in queued_jobs:
        if not can_apply_today(applied_today=applied_today, max_per_day=max_per_day):
            rprint(f"[yellow]Daily cap reached ({max_per_day}). Stopping.[/yellow]")
            break

        # Build the job URL
        if settings.mock:
            from job_hunter.linkedin.mock_site import MockLinkedInServer
            server = MockLinkedInServer()
            base_url = server.start()
            job_url = f"{base_url}{job.url}"
        else:
            job_url = job.url

        try:
            rprint(f"  Applying: {job.title} @ {job.company} …")

            result = asyncio.run(
                apply_to_job(
                    job_url=job_url,
                    resume_path=str(resume_path),
                    dry_run=settings.dry_run,
                    headless=settings.headless,
                    slowmo_ms=settings.slowmo_ms,
                    mock=settings.mock,
                    form_answers=profile_form_answers,
                    openai_api_key=settings.openai_api_key,
                    user_profile=user_profile_dict,
                )
            )

            # Map result string to DB enum
            result_map = {
                "success": ApplicationResult.SUCCESS,
                "dry_run": ApplicationResult.DRY_RUN,
                "failed": ApplicationResult.FAILED,
                "blocked": ApplicationResult.BLOCKED,
                "already_applied": ApplicationResult.ALREADY_APPLIED,
            }
            db_result = result_map.get(result["result"], ApplicationResult.FAILED)

            # Save attempt
            attempt = ApplicationAttempt(
                job_hash=job.hash,
                started_at=result.get("started_at"),
                ended_at=result.get("ended_at"),
                result=db_result,
                failure_stage=result.get("failure_stage"),
                form_answers_json=result.get("form_answers", {}),
            )
            save_attempt(session, attempt)

            # Update job status
            status_map = {
                "success": JobStatus.APPLIED,
                "dry_run": JobStatus.APPLIED,
                "failed": JobStatus.FAILED,
                "blocked": JobStatus.BLOCKED,
                "already_applied": JobStatus.APPLIED,
            }
            job.status = status_map.get(result["result"], JobStatus.FAILED)

            if result["result"] == "success":
                applied += 1
                applied_today += 1
            elif result["result"] == "already_applied":
                applied += 1
                rprint(f"    [cyan]ALREADY APPLIED[/cyan] — previously applied to this job.")
            elif result["result"] == "dry_run":
                dry_runs += 1
            elif result["result"] == "blocked":
                blocked += 1
                rprint(f"    [red]BLOCKED[/red] — challenge detected, stopping.")
                session.commit()
                break
            else:
                failed += 1

            rprint(f"    → [bold]{result['result']}[/bold]")

        finally:
            if settings.mock:
                server.stop()

    session.commit()

    parts = []
    if applied:
        parts.append(f"{applied} applied")
    if dry_runs:
        parts.append(f"{dry_runs} dry-run")
    if failed:
        parts.append(f"{failed} failed")
    if blocked:
        parts.append(f"{blocked} blocked")

    rprint(f"\n[green]✓[/green] Done: {', '.join(parts) or 'nothing to do'}")


@app.command()
def run(
    ctx: typer.Context,
    profile: Annotated[str, typer.Option("--profile", "-p", help="Search-profile name")] = "default",
) -> None:
    """Run the full pipeline: discover → score → apply → report."""
    import asyncio

    from job_hunter.orchestration.pipeline import run_pipeline

    state = _get_state(ctx)
    settings = state.settings

    # Load profile thresholds
    min_fit_score = 75
    min_similarity = 0.35
    max_per_day = 25
    blacklist_companies: list[str] = []
    blacklist_titles: list[str] = []
    keywords: list[str] = []
    location = ""
    remote = False
    seniority: list[str] = []
    profiles_path = settings.data_dir / "profiles.yml"
    if profiles_path.exists():
        profs = load_profiles(profiles_path)
        matching = [p for p in profs if p.name == profile]
        if matching:
            min_fit_score = matching[0].min_fit_score
            min_similarity = matching[0].min_similarity
            max_per_day = matching[0].max_applications_per_day
            blacklist_companies = matching[0].blacklist_companies
            blacklist_titles = matching[0].blacklist_titles
            keywords = matching[0].keywords
            location = matching[0].location
            remote = matching[0].remote
            seniority = matching[0].seniority

    # Load resume text
    resume_text = ""
    user_profile_path = settings.data_dir / "user_profile.yml"
    if user_profile_path.exists():
        up = load_user_profile(user_profile_path)
        resume_text = (
            f"{up.name}\n{up.title}\n{up.summary}\n"
            f"Skills: {', '.join(up.skills)}\n"
            f"Experience: {up.experience_years} years\n"
            f"Education: {', '.join(up.education)}\n"
            f"Desired roles: {', '.join(up.desired_roles)}\n"
        )

    resume_path = str(settings.data_dir / "resume.pdf")
    if not Path(resume_path).exists():
        resume_path = "tests/fixtures/resume.txt"

    rprint(f"[bold]Running full pipeline[/bold] (profile={profile}, mock={settings.mock}, dry_run={settings.dry_run})")

    summary = asyncio.run(
        run_pipeline(
            profile_name=profile,
            mock=settings.mock,
            dry_run=settings.dry_run,
            headless=settings.headless,
            slowmo_ms=settings.slowmo_ms,
            data_dir=settings.data_dir,
            openai_api_key=settings.openai_api_key,
            min_fit_score=min_fit_score,
            min_similarity=min_similarity,
            max_applications_per_day=max_per_day,
            blacklist_companies=blacklist_companies,
            blacklist_titles=blacklist_titles,
            resume_text=resume_text,
            resume_path=resume_path,
            keywords=keywords,
            location=location,
            remote=remote,
            seniority=seniority,
        )
    )

    rprint("\n[bold cyan]Pipeline Summary[/bold cyan]")
    rprint(f"  Discovered: {summary['discovered']}")
    rprint(f"  Scored:     {summary['scored']}")
    rprint(f"  Queued:     {summary['queued']}")
    rprint(f"  Applied:    {summary['applied']}")
    rprint(f"  Dry-run:    {summary['dry_run']}")
    rprint(f"  Skipped:    {summary['skipped']}")
    rprint(f"  Review:     {summary['review']}")
    rprint(f"  Failed:     {summary['failed']}")
    rprint(f"  Blocked:    {summary['blocked']}")
    if summary.get("report_md_path"):
        rprint(f"\n[green]✓[/green] Report saved to {summary['report_md_path']}")


@app.command()
def report(
    ctx: typer.Context,
    date: Annotated[Optional[str], typer.Option("--date", help="Report date (YYYY-MM-DD)")] = None,
) -> None:
    """Generate a daily report."""
    from job_hunter.db.repo import get_engine, make_session
    from job_hunter.reporting.report import generate_report

    state = _get_state(ctx)
    settings = state.settings

    engine = get_engine(settings.data_dir)
    init_db(engine)
    session = make_session(engine)

    summary = generate_report(session=session, data_dir=settings.data_dir, date=date)

    rprint(f"\n[bold cyan]Report for {summary['date']}[/bold cyan]")
    rprint(f"  Total jobs: {summary['total_jobs']}")

    status_counts = summary.get("status_counts", {})
    for status, count in status_counts.items():
        rprint(f"  {status.capitalize()}: {count}")

    top_skills = summary.get("top_missing_skills", [])
    if top_skills:
        rprint("\n  [bold]Top missing skills:[/bold]")
        for item in top_skills:
            rprint(f"    - {item['skill']} ({item['count']})")

    rprint(f"\n[green]✓[/green] Markdown: {summary.get('md_path')}")
    rprint(f"[green]✓[/green] JSON:     {summary.get('json_path')}")


@app.command()
def serve(
    ctx: typer.Context,
    host: Annotated[str, typer.Option("--host", help="Bind host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Bind port")] = 8000,
    reload: Annotated[bool, typer.Option("--reload", help="Auto-reload on code changes")] = False,
) -> None:
    """Start the web GUI server."""
    import uvicorn

    from job_hunter.web.app import create_app

    state = _get_state(ctx)
    settings = state.settings

    rprint(f"[bold]Starting web server[/bold] at http://{host}:{port}")
    rprint(f"  mock={settings.mock}, dry_run={settings.dry_run}")

    web_app = create_app(settings)
    uvicorn.run(web_app, host=host, port=port, log_level="info")


