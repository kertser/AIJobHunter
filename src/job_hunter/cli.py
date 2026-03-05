"""CLI entry point — ``hunt`` command with subcommands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich import print as rprint

from job_hunter.config.loader import load_profiles, load_settings, load_user_profile, save_profiles, save_user_profile
from job_hunter.config.models import LogLevel
from job_hunter.db.repo import get_engine, init_db

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

    job_dicts = asyncio.run(
        discover_jobs(
            profile_name=profile,
            mock=settings.mock,
            headless=settings.headless,
            slowmo_ms=settings.slowmo_ms,
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
    _get_state(ctx)
    raise NotImplementedError("score is not yet implemented")


@app.command()
def apply(
    ctx: typer.Context,
    profile: Annotated[str, typer.Option("--profile", "-p", help="Search-profile name")] = "default",
) -> None:
    """Apply to qualified jobs via Easy Apply."""
    _get_state(ctx)
    raise NotImplementedError("apply is not yet implemented")


@app.command()
def run(
    ctx: typer.Context,
    profile: Annotated[str, typer.Option("--profile", "-p", help="Search-profile name")] = "default",
) -> None:
    """Run the full pipeline: discover → score → apply → report."""
    _get_state(ctx)
    raise NotImplementedError("run is not yet implemented")


@app.command()
def report(
    ctx: typer.Context,
    date: Annotated[Optional[str], typer.Option("--date", help="Report date (YYYY-MM-DD)")] = None,
) -> None:
    """Generate a daily report."""
    _get_state(ctx)
    raise NotImplementedError("report is not yet implemented")

