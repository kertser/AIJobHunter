"""CLI commands for the Market Intelligence subsystem.

Registered as a sub-app under ``hunt market …`` via :func:`typer.Typer.add_typer`.
"""

from __future__ import annotations

import typer
from rich import print as rprint

market_app = typer.Typer(
    name="market",
    help="Market Intelligence — ingest, extract, graph, and analyse.",
    add_completion=False,
)


def _get_state(ctx: typer.Context):
    return ctx.obj["state"]


# ---------------------------------------------------------------------------
# hunt market ingest
# ---------------------------------------------------------------------------


@market_app.command()
def ingest(ctx: typer.Context) -> None:
    """Create market events from existing discovered jobs."""
    state = _get_state(ctx)
    settings = state.settings

    from job_hunter.db.repo import get_engine, init_db, make_session
    from job_hunter.market.events import ingest_jobs

    engine = get_engine(settings.data_dir)
    init_db(engine)
    session = make_session(engine)

    created = ingest_jobs(session)
    session.commit()

    rprint(f"[green]✓[/green] Ingested {created} new market event(s)")


# ---------------------------------------------------------------------------
# hunt market extract
# ---------------------------------------------------------------------------


@market_app.command()
def extract(
    ctx: typer.Context,
    extractor: str = typer.Option(
        "heuristic", "--extractor", "-e",
        help="Extractor to use: heuristic | openai | local | fake",
    ),
) -> None:
    """Run signal extraction on un-extracted market events."""
    state = _get_state(ctx)
    settings = state.settings

    from job_hunter.db.repo import get_engine, init_db, make_session
    from job_hunter.market.extract import (
        FakeMarketExtractor,
        HeuristicExtractor,
        MarketExtractor,
        OpenAIMarketExtractor,
        run_extraction,
    )

    ext: MarketExtractor
    if extractor == "openai":
        api_key = settings.openai_api_key
        if not api_key:
            rprint("[red]✗[/red] JOBHUNTER_OPENAI_API_KEY is required for the OpenAI extractor.")
            raise typer.Exit(1)
        from job_hunter.llm_client import get_task_params
        tp = get_task_params(settings, "market_extract")
        ext = OpenAIMarketExtractor(api_key=api_key, temperature=tp.temperature, max_tokens=tp.max_tokens)
    elif extractor == "local":
        from job_hunter.llm_client import get_task_params
        tp = get_task_params(settings, "market_extract")
        ext = OpenAIMarketExtractor(
            api_key="local-no-key-needed",
            model=settings.local_llm_model or "local",
            base_url=settings.local_llm_url or "http://localhost:8080/v1",
            temperature=tp.temperature,
            max_tokens=tp.max_tokens,
        )
    elif extractor == "fake":
        ext = FakeMarketExtractor()
    else:
        ext = HeuristicExtractor()

    engine = get_engine(settings.data_dir)
    init_db(engine)
    session = make_session(engine)

    count = run_extraction(session, ext)
    session.commit()

    rprint(f"[green]✓[/green] Extracted {count} event(s) with {ext.version}")


# ---------------------------------------------------------------------------
# hunt market graph
# ---------------------------------------------------------------------------


@market_app.command()
def graph(ctx: typer.Context) -> None:
    """Normalise entities and build the evidence graph from extractions."""
    state = _get_state(ctx)
    settings = state.settings

    from job_hunter.db.repo import get_engine, init_db, make_session
    from job_hunter.market.graph.builder import build_graph

    engine = get_engine(settings.data_dir)
    init_db(engine)
    session = make_session(engine)

    summary = build_graph(session)
    session.commit()

    rprint(
        f"[green]✓[/green] Graph built: "
        f"{summary['extractions']} extraction(s), "
        f"{summary['entities']} entities, "
        f"{summary['evidence']} evidence records, "
        f"{summary['edges']} edges"
    )


# ---------------------------------------------------------------------------
# hunt market export
# ---------------------------------------------------------------------------


@market_app.command()
def export(
    ctx: typer.Context,
    fmt: str = typer.Option(
        "json", "--format", "-f",
        help="Export format: json | graphml",
    ),
) -> None:
    """Export the evidence graph to a file."""
    state = _get_state(ctx)
    settings = state.settings

    from job_hunter.db.repo import get_engine, init_db, make_session
    from job_hunter.market.graph.metrics import export_graphml, export_json

    engine = get_engine(settings.data_dir)
    init_db(engine)
    session = make_session(engine)

    out_dir = settings.data_dir / "market"
    if fmt == "graphml":
        path = export_graphml(session, out_dir / "graph.graphml")
    else:
        path = export_json(session, out_dir / "graph.json")

    rprint(f"[green]✓[/green] Exported graph to {path}")


# ---------------------------------------------------------------------------
# hunt market trends
# ---------------------------------------------------------------------------


@market_app.command()
def trends(
    ctx: typer.Context,
    bucket_days: int = typer.Option(7, "--bucket-days", help="Days per bucket"),
    num_buckets: int = typer.Option(4, "--num-buckets", help="Number of historical buckets"),
    top_n: int = typer.Option(15, "--top", "-n", help="Show top N entities"),
) -> None:
    """Compute and display trend summaries."""
    state = _get_state(ctx)
    settings = state.settings

    from job_hunter.db.repo import get_engine, init_db, make_session
    from job_hunter.market.trends.compute import compute_trends
    from job_hunter.market.trends.queries import get_latest_snapshots
    from job_hunter.market.db_models import MarketEntity

    engine = get_engine(settings.data_dir)
    init_db(engine)
    session = make_session(engine)

    result = compute_trends(session, bucket_days=bucket_days, num_buckets=num_buckets)
    session.commit()

    rprint(
        f"[green]✓[/green] Computed trends for {result['entities']} entities "
        f"({result['snapshots_created']} snapshots)"
    )

    # Display top trending entities
    snapshots = get_latest_snapshots(session, limit=top_n)
    if snapshots:
        rprint("\n[bold]Top entities by frequency:[/bold]")
        for snap in snapshots:
            entity = session.get(MarketEntity, snap.entity_id) if snap.entity_id else None
            name = entity.display_name if entity else "?"
            etype = entity.entity_type.value if entity else "?"
            arrow = "↑" if snap.momentum > 0 else ("↓" if snap.momentum < 0 else "→")
            rprint(
                f"  {name:30s} [{etype:8s}]  freq={snap.frequency:.0f}  "
                f"momentum={arrow}{abs(snap.momentum):.2f}  "
                f"novelty={snap.novelty:.1f}  burst={snap.burst:.2f}"
            )
    else:
        rprint("[yellow]No snapshots yet. Run ingest → extract → graph first.[/yellow]")


# ---------------------------------------------------------------------------
# hunt market report
# ---------------------------------------------------------------------------


@market_app.command()
def report(ctx: typer.Context) -> None:
    """Emit a market intelligence report (Markdown + JSON)."""
    state = _get_state(ctx)
    settings = state.settings

    from job_hunter.db.repo import get_engine, init_db, make_session
    from job_hunter.market.report import generate_market_report

    engine = get_engine(settings.data_dir)
    init_db(engine)
    session = make_session(engine)

    out_dir = settings.data_dir / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path, json_path = generate_market_report(session, out_dir)
    rprint(f"[green]✓[/green] Market report saved to {md_path}")


# ---------------------------------------------------------------------------
# hunt market role-model
# ---------------------------------------------------------------------------


@market_app.command("role-model")
def role_model_cmd(
    ctx: typer.Context,
    min_group: int = typer.Option(2, "--min-group", help="Min jobs per role family"),
    threshold: float = typer.Option(0.3, "--threshold", help="Min importance to keep"),
    normalizer: str = typer.Option(
        "heuristic", "--normalizer", "-n",
        help="Title normaliser: heuristic | openai | local | fake | legacy",
    ),
) -> None:
    """Build role archetypes from job extraction data."""
    state = _get_state(ctx)
    settings = state.settings

    from job_hunter.db.repo import get_engine, init_db, make_session
    from job_hunter.market.role_model import build_role_archetypes
    from job_hunter.market.title_normalizer import (
        FakeTitleNormalizer,
        HeuristicTitleNormalizer,
        OpenAITitleNormalizer,
        TitleNormalizer,
    )

    title_norm: TitleNormalizer | None = None
    if normalizer == "openai":
        api_key = settings.openai_api_key
        if not api_key:
            rprint("[red]✗[/red] JOBHUNTER_OPENAI_API_KEY is required for the OpenAI normaliser.")
            raise typer.Exit(1)
        from job_hunter.llm_client import get_task_params
        tp = get_task_params(settings, "title_normalize")
        title_norm = OpenAITitleNormalizer(api_key=api_key, temperature=tp.temperature, max_tokens=tp.max_tokens)
    elif normalizer == "local":
        from job_hunter.llm_client import get_task_params
        tp = get_task_params(settings, "title_normalize")
        title_norm = OpenAITitleNormalizer(
            api_key="local-no-key-needed",
            model=settings.local_llm_model or "local",
            base_url=settings.local_llm_url or "http://localhost:8080/v1",
            temperature=tp.temperature,
            max_tokens=tp.max_tokens,
        )
    elif normalizer == "fake":
        title_norm = FakeTitleNormalizer()
    elif normalizer == "legacy":
        title_norm = None  # use old _normalise_title fallback
    else:
        title_norm = HeuristicTitleNormalizer()

    engine = get_engine(settings.data_dir)
    init_db(engine)
    session = make_session(engine)

    result = build_role_archetypes(
        session,
        min_group_size=min_group,
        importance_threshold=threshold,
        title_normalizer=title_norm,
    )
    session.commit()

    rprint(
        f"[green]✓[/green] Built {result['roles_created']} role archetype(s) "
        f"with {result['requirements_created']} requirement(s)"
    )


# ---------------------------------------------------------------------------
# hunt market candidate-model
# ---------------------------------------------------------------------------


@market_app.command("candidate-model")
def candidate_model_cmd(
    ctx: typer.Context,
    profile: str = typer.Option(
        "default", "--profile", "-p",
        help="Candidate key (maps to user_profile.yml)",
    ),
) -> None:
    """Build candidate capability model from user profile."""
    state = _get_state(ctx)
    settings = state.settings

    from job_hunter.config.loader import load_user_profile
    from job_hunter.db.repo import get_engine, init_db, make_session
    from job_hunter.market.candidate_model import (
        build_candidate_capabilities,
        get_candidate_capabilities,
    )

    profile_path = settings.data_dir / "user_profile.yml"
    if not profile_path.exists():
        rprint("[red]✗[/red] No user_profile.yml found. Run `hunt profile` first.")
        raise typer.Exit(1)

    user_profile = load_user_profile(profile_path)

    engine = get_engine(settings.data_dir)
    init_db(engine)
    session = make_session(engine)

    result = build_candidate_capabilities(session, user_profile, candidate_key=profile)
    session.commit()

    rprint(
        f"[green]✓[/green] Built {result['capabilities_created']} candidate capabilities "
        f"({result['entities_resolved']} entities resolved)"
    )

    # Display top capabilities
    caps = get_candidate_capabilities(session, candidate_key=profile)
    if caps:
        rprint("\n[bold]Candidate capabilities:[/bold]")
        for cap in caps[:15]:
            rprint(
                f"  {cap['display_name']:30s} [{cap['entity_type']:8s}]  "
                f"prof={cap['proficiency_estimate']:.2f}  "
                f"conf={cap['confidence']:.2f}  "
                f"transfer={cap['transferability']:.2f}"
            )


# ---------------------------------------------------------------------------
# hunt market match
# ---------------------------------------------------------------------------


@market_app.command("match")
def match_cmd(
    ctx: typer.Context,
    profile: str = typer.Option(
        "default", "--profile", "-p",
        help="Candidate key to match",
    ),
) -> None:
    """Match candidate against role archetypes and show opportunities."""
    state = _get_state(ctx)
    settings = state.settings

    from job_hunter.config.loader import load_user_profile
    from job_hunter.db.repo import get_engine, init_db, make_session
    from job_hunter.market.candidate_model import build_candidate_capabilities
    from job_hunter.market.matching import match_candidate_to_roles
    from job_hunter.market.opportunity import score_opportunities, find_adjacent_roles

    profile_path = settings.data_dir / "user_profile.yml"
    if not profile_path.exists():
        rprint("[red]✗[/red] No user_profile.yml found. Run `hunt profile` first.")
        raise typer.Exit(1)

    user_profile = load_user_profile(profile_path)

    engine = get_engine(settings.data_dir)
    init_db(engine)
    session = make_session(engine)

    # Build capabilities if needed
    build_candidate_capabilities(session, user_profile, candidate_key=profile)
    session.commit()

    # Match
    matches = match_candidate_to_roles(session, candidate_key=profile)
    session.commit()

    if not matches:
        rprint("[yellow]No role archetypes found. Run `hunt market role-model` first.[/yellow]")
        raise typer.Exit(0)

    # Opportunities
    opps = score_opportunities(session, candidate_key=profile)

    rprint(f"\n[bold]Match results for '{profile}':[/bold]")
    for opp in opps:
        score_pct = f"{opp['opportunity_score']:.0%}"
        conf_pct = f"{opp['confidence']:.0%}"
        risk_pct = f"{opp['mismatch_risk']:.0%}"
        rprint(
            f"\n  [bold]{opp['role_key']}[/bold]  "
            f"score={score_pct}  confidence={conf_pct}  risk={risk_pct}"
        )
        if opp["hard_gaps"]:
            rprint(f"    [red]Hard gaps:[/red] {', '.join(opp['hard_gaps'])}")
        if opp["learnable_gaps"]:
            rprint(f"    [yellow]Learnable:[/yellow] {', '.join(opp['learnable_gaps'])}")

    # Adjacent roles
    adjacent = find_adjacent_roles(session, candidate_key=profile, top_n=5)
    if adjacent:
        rprint("\n[bold]Adjacent roles (reachable via skill graph):[/bold]")
        for adj in adjacent:
            rprint(
                f"  {adj['display_name']:30s}  "
                f"proximity={adj['proximity']:.2f}  hops={adj['hops']}"
            )


# ---------------------------------------------------------------------------
# hunt market dialogue-list
# ---------------------------------------------------------------------------


@market_app.command("dialogue-list")
def dialogue_list_cmd(ctx: typer.Context) -> None:
    """List all dialogue sessions."""
    state = _get_state(ctx)
    settings = state.settings

    from job_hunter.db.repo import get_engine, init_db, make_session
    from job_hunter.market.dialogue import get_all_sessions

    engine = get_engine(settings.data_dir)
    init_db(engine)
    session = make_session(engine)

    sessions = get_all_sessions(session)
    if not sessions:
        rprint("[yellow]No dialogue sessions found.[/yellow]")
        return

    rprint(f"\n[bold]Dialogue sessions ({len(sessions)}):[/bold]")
    for ds in sessions:
        ended = "ended" if ds.ended_at else "open"
        rprint(
            f"  {str(ds.id)[:8]}…  "
            f"{ds.subject_type.value}/{ds.subject_key}  "
            f"type={ds.session_type.value}  "
            f"started={ds.started_at:%Y-%m-%d %H:%M}  "
            f"[{ended}]"
        )


# ---------------------------------------------------------------------------
# hunt market dialogue-evaluate
# ---------------------------------------------------------------------------


@market_app.command("dialogue-evaluate")
def dialogue_evaluate_cmd(
    ctx: typer.Context,
    evaluator: str = typer.Option(
        "rule-based", "--evaluator", "-e",
        help="Evaluator to use: rule-based | fake",
    ),
) -> None:
    """Evaluate un-assessed dialogue sessions and persist assessments."""  # noqa: D401
    state = _get_state(ctx)
    settings = state.settings

    from job_hunter.db.repo import get_engine, init_db, make_session
    from job_hunter.market.dialogue import (
        add_assessment,
        get_all_sessions,
        get_assessments,
        get_turns,
    )
    from job_hunter.market.dialogue_eval import (
        DialogueEvaluator,
        FakeDialogueEvaluator,
        RuleBasedDialogueEvaluator,
    )
    from job_hunter.market.db_models import AssessmentType

    engine = get_engine(settings.data_dir)
    init_db(engine)
    session = make_session(engine)

    ev: DialogueEvaluator
    if evaluator == "fake":
        ev = FakeDialogueEvaluator()
    else:
        ev = RuleBasedDialogueEvaluator()

    sessions = get_all_sessions(session)
    total_assessed = 0

    for ds in sessions:
        existing = get_assessments(session, ds.id)
        if existing:
            continue  # already assessed

        turns = get_turns(session, ds.id)
        if not turns:
            continue

        turn_dicts = [
            {
                "speaker": t.speaker,
                "turn_index": t.turn_index,
                "prompt_text": t.prompt_text,
                "response_text": t.response_text,
            }
            for t in turns
        ]

        results = ev.evaluate(turn_dicts)
        for r in results:
            add_assessment(
                session,
                session_id=ds.id,
                assessment_type=AssessmentType(r["assessment_type"]),
                score=r["score"],
                confidence=r["confidence"],
                evidence_span=r.get("evidence_span", ""),
                assessor_version=ev.version,
            )
        total_assessed += 1

    session.commit()
    rprint(
        f"[green]✓[/green] Evaluated {total_assessed} session(s) "
        f"with {ev.version}"
    )


# ---------------------------------------------------------------------------
# hunt market run-all
# ---------------------------------------------------------------------------


@market_app.command("run-all")
def run_all_cmd(
    ctx: typer.Context,
    extractor: str = typer.Option(
        "heuristic", "--extractor", "-e",
        help="Extractor to use: heuristic | openai | local | fake",
    ),
    profile: str = typer.Option(
        "default", "--profile", "-p",
        help="Candidate key (maps to user_profile.yml)",
    ),
    normalizer: str = typer.Option(
        "heuristic", "--normalizer", "-n",
        help="Title normaliser: heuristic | openai | local | fake | legacy",
    ),
) -> None:
    """Run the full market pipeline: ingest → extract → graph → trends → role-model → candidate-model → match."""
    state = _get_state(ctx)
    settings = state.settings

    from job_hunter.db.repo import get_engine, init_db, make_session
    from job_hunter.market.extract import (
        FakeMarketExtractor,
        HeuristicExtractor,
        MarketExtractor,
        OpenAIMarketExtractor,
    )
    from job_hunter.market.pipeline import run_market_pipeline
    from job_hunter.market.title_normalizer import (
        FakeTitleNormalizer,
        HeuristicTitleNormalizer,
        OpenAITitleNormalizer,
        TitleNormalizer,
    )

    ext: MarketExtractor
    if extractor == "openai":
        api_key = settings.openai_api_key
        if not api_key:
            rprint("[red]✗[/red] JOBHUNTER_OPENAI_API_KEY is required for the OpenAI extractor.")
            raise typer.Exit(1)
        from job_hunter.llm_client import get_task_params
        tp = get_task_params(settings, "market_extract")
        ext = OpenAIMarketExtractor(api_key=api_key, temperature=tp.temperature, max_tokens=tp.max_tokens)
    elif extractor == "local":
        from job_hunter.llm_client import get_task_params
        tp = get_task_params(settings, "market_extract")
        ext = OpenAIMarketExtractor(
            api_key="local-no-key-needed",
            model=settings.local_llm_model or "local",
            base_url=settings.local_llm_url or "http://localhost:8080/v1",
            temperature=tp.temperature,
            max_tokens=tp.max_tokens,
        )
    elif extractor == "fake":
        ext = FakeMarketExtractor()
    else:
        ext = HeuristicExtractor()

    title_norm: TitleNormalizer | None = None
    if normalizer == "openai":
        api_key = settings.openai_api_key
        if not api_key:
            rprint("[red]✗[/red] JOBHUNTER_OPENAI_API_KEY is required for the OpenAI normaliser.")
            raise typer.Exit(1)
        from job_hunter.llm_client import get_task_params as _gtp
        _tp = _gtp(settings, "title_normalize")
        title_norm = OpenAITitleNormalizer(api_key=api_key, temperature=_tp.temperature, max_tokens=_tp.max_tokens)
    elif normalizer == "local":
        from job_hunter.llm_client import get_task_params as _gtp
        _tp = _gtp(settings, "title_normalize")
        title_norm = OpenAITitleNormalizer(
            api_key="local-no-key-needed",
            model=settings.local_llm_model or "local",
            base_url=settings.local_llm_url or "http://localhost:8080/v1",
            temperature=_tp.temperature,
            max_tokens=_tp.max_tokens,
        )
    elif normalizer == "fake":
        title_norm = FakeTitleNormalizer()
    elif normalizer == "legacy":
        title_norm = None
    else:
        title_norm = HeuristicTitleNormalizer()

    # Load user profile if available
    user_profile = None
    profile_path = settings.data_dir / "user_profile.yml"
    if profile_path.exists():
        from job_hunter.config.loader import load_user_profile
        user_profile = load_user_profile(profile_path)

    engine = get_engine(settings.data_dir)
    init_db(engine)
    session = make_session(engine)

    summary = run_market_pipeline(
        session,
        extractor=ext,
        profile=user_profile,
        candidate_key=profile,
        title_normalizer=title_norm,
    )

    rprint("\n[bold green]✓ Market pipeline complete[/bold green]")
    rprint(f"  Events created:  {summary['events_created']}")
    rprint(f"  Extractions:     {summary['extractions']}")
    graph = summary.get("graph", {})
    rprint(f"  Entities:        {graph.get('entities', 0)}")
    rprint(f"  Evidence:        {graph.get('evidence', 0)}")
    rprint(f"  Edges:           {graph.get('edges', 0)}")
    trends = summary.get("trends", {})
    rprint(f"  Trend snapshots: {trends.get('snapshots_created', 0)}")
    roles = summary.get("roles", {})
    rprint(f"  Role archetypes: {roles.get('roles_created', 0)}")
    if summary.get("capabilities"):
        caps = summary["capabilities"]
        rprint(f"  Capabilities:    {caps.get('capabilities_created', 0)}")
        rprint(f"  Matches:         {summary.get('matches', 0)}")
    else:
        rprint("  Candidate model: skipped (no user_profile.yml)")
