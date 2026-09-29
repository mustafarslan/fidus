"""`fidus` command-line interface."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from fidus import __version__
from fidus.errors import FidusError
from fidus.log import setup_logging

app = typer.Typer(
    name="fidus",
    help="Fidus: an autonomous documentation agent that keeps a textbook-style docs repo in sync "
    "with your codebases through reviewed pull requests.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
state_app = typer.Typer(
    help="Inspect or adjust Fidus's run state (.fidus/state.json).", no_args_is_help=True
)
app.add_typer(state_app, name="state")
console = Console(stderr=True)

ConfigOpt = Annotated[Path, typer.Option("--config", "-c", help="Path to fidus.yaml.")]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")]


def _version(value: bool) -> None:
    if value:
        typer.echo(f"fidus {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")
    ] = False,
) -> None:
    pass


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise typer.BadParameter(f"--since must be an ISO date/time, got {value!r}") from e
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _chapters(value: str | None) -> set[str] | None:
    return {c.strip() for c in value.split(",") if c.strip()} if value else None


def _fail(e: FidusError) -> None:
    console.print(f"[bold red]error:[/] {e}")
    raise typer.Exit(e.exit_code)


# ---------------------------------------------------------------------------------------------
@app.command()
def run(
    config: ConfigOpt = Path("fidus.yaml"),
    mode: Annotated[str, typer.Option(help="auto | sync | audit | full")] = "auto",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="No GitHub writes; write artifacts to --output.")
    ] = False,
    output: Annotated[Path, typer.Option(help="Dry-run output directory.")] = Path("fidus-out"),
    provider: Annotated[
        str | None, typer.Option(help="Override llm.provider (e.g. 'fake').")
    ] = None,
    since: Annotated[
        str | None, typer.Option(help="Process changes merged after this ISO time.")
    ] = None,
    chapters: Annotated[
        str | None, typer.Option(help="Comma-separated chapter ids to limit to.")
    ] = None,
    no_push: Annotated[
        bool, typer.Option("--no-push", help="Commit locally but do not push/open a PR.")
    ] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Nightly sync: update chapters affected by merged PRs (+ periodic audit) and open/update the PR."""
    setup_logging(verbose)
    if mode not in ("auto", "sync", "audit", "full"):
        raise typer.BadParameter("--mode must be auto, sync, audit or full")
    from fidus.pipeline.run import run as run_pipeline
    from fidus.pipeline.session import Options

    try:
        report = run_pipeline(
            Options(
                config,
                dry_run=dry_run,
                output=output,
                provider=provider,
                since=_parse_since(since),
                chapters=_chapters(chapters),
                no_push=no_push,
            ),
            mode,  # type: ignore[arg-type]
        )
    except FidusError as e:
        _fail(e)
        return
    console.print(report.summary_markdown())
    if report.failed and not report.noop:
        raise typer.Exit(3)


@app.command()
def bootstrap(
    config: ConfigOpt = Path("fidus.yaml"),
    chapters: Annotated[
        str | None, typer.Option(help="Comma-separated chapter ids to write.")
    ] = None,
    resume: Annotated[bool, typer.Option(help="Skip chapters whose file already exists.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    output: Annotated[Path, typer.Option()] = Path("fidus-out"),
    provider: Annotated[str | None, typer.Option()] = None,
    no_push: Annotated[bool, typer.Option("--no-push")] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Write every chapter of the outline from scratch and open a bootstrap PR."""
    setup_logging(verbose)
    from fidus.pipeline.bootstrap import bootstrap as run_bootstrap
    from fidus.pipeline.session import Options

    try:
        report = run_bootstrap(
            Options(
                config,
                dry_run=dry_run,
                output=output,
                provider=provider,
                chapters=_chapters(chapters),
                no_push=no_push,
            ),
            resume=resume,
        )
    except FidusError as e:
        _fail(e)
        return
    console.print(report.summary_markdown())
    if report.failed:
        raise typer.Exit(3)


@app.command()
def init(
    config: ConfigOpt = Path("fidus.yaml"),
    source: Annotated[
        list[str] | None,
        typer.Option("--source", "-s", help="owner/repo or local path (repeatable)."),
    ] = None,
    provider: Annotated[str | None, typer.Option(help="anthropic | gemini | openai | fake")] = None,
    model: Annotated[str | None, typer.Option(help="Model id.")] = None,
    base_url: Annotated[str | None, typer.Option(help="OpenAI-compatible endpoint URL.")] = None,
    docs_dir: Annotated[str, typer.Option(help="Docs directory inside this repo.")] = "docs",
    title: Annotated[str | None, typer.Option(help="Book title.")] = None,
    write_workflow: Annotated[
        bool, typer.Option("--write-workflow", help="Also write .github/workflows/fidus.yml.")
    ] = False,
    auth: Annotated[
        str,
        typer.Option(
            help="Workflow auth: app (GitHub App, recommended) or github-token (zero setup)."
        ),
    ] = "app",
    config_only: Annotated[
        bool, typer.Option("--config-only", help="Only write fidus.yaml; skip the outline.")
    ] = False,
    non_interactive: Annotated[bool, typer.Option("--non-interactive", "-y")] = False,
    force: Annotated[bool, typer.Option(help="Overwrite existing files.")] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Create fidus.yaml and have the agent propose fidus.outline.yaml."""
    setup_logging(verbose)
    from fidus.cli.init_cmd import run_init

    try:
        run_init(
            config=config,
            sources=source or [],
            provider=provider,
            model=model,
            base_url=base_url,
            docs_dir=docs_dir,
            title=title,
            write_workflow=write_workflow,
            auth=auth,
            config_only=config_only,
            interactive=not non_interactive and sys.stdin.isatty(),
            force=force,
            console=console,
        )
    except FidusError as e:
        _fail(e)


@app.command()
def validate(
    config: ConfigOpt = Path("fidus.yaml"),
    offline: Annotated[
        bool, typer.Option(help="Skip cloning sources (schema checks only).")
    ] = False,
    strict: Annotated[bool, typer.Option(help="Treat warnings as errors.")] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Check fidus.yaml and the outline; report coverage, missing and orphaned chapters."""
    setup_logging(verbose)
    from fidus.cli.validate_cmd import run_validate

    try:
        warnings = run_validate(config, offline=offline, console=console)
    except FidusError as e:
        _fail(e)
        return
    if warnings and strict:
        raise typer.Exit(2)


@app.command()
def doctor(
    config: ConfigOpt = Path("fidus.yaml"),
    ping: Annotated[
        bool, typer.Option(help="Send a tiny request to the LLM (tests tool calling).")
    ] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Diagnose setup: git, tokens, repository access, LLM provider and key."""
    setup_logging(verbose)
    from fidus.cli.doctor_cmd import run_doctor

    ok = run_doctor(config, ping=ping, console=console)
    if not ok:
        raise typer.Exit(1)


@app.command("setup-app")
def setup_app(
    org: Annotated[
        str | None,
        typer.Option(help="Create the App in this organization (default: your account)."),
    ] = None,
    docs_repo: Annotated[
        str | None,
        typer.Option(help="owner/name of the docs repo: credentials are saved there with gh."),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(help="App name (default: <owner>-fidus); must be unique on GitHub."),
    ] = None,
    github_url: Annotated[
        str, typer.Option(help="GitHub web URL (GitHub Enterprise Server: your host).")
    ] = "https://github.com",
    api_url: Annotated[str, typer.Option(help="GitHub API URL.")] = "https://api.github.com",
    no_browser: Annotated[
        bool, typer.Option("--no-browser", help="Print the URL instead of opening it.")
    ] = False,
    no_gh: Annotated[
        bool, typer.Option("--no-gh", help="Don't use gh; write the key to a file instead.")
    ] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Create the Fidus GitHub App in one click (GitHub App manifest flow) and store its credentials."""
    setup_logging(verbose)
    from fidus.cli.setup_app import run_setup_app

    try:
        run_setup_app(
            org=org,
            docs_repo=docs_repo,
            name=name,
            github_url=github_url,
            api_url=api_url,
            open_browser=not no_browser,
            use_gh=not no_gh,
            out_dir=Path.cwd(),
            echo=console.print,
        )
    except FidusError as e:
        _fail(e)


@state_app.command("show")
def state_show(
    ref: Annotated[
        str | None, typer.Option(help="Git ref to read from (default: working tree).")
    ] = None,
    config: ConfigOpt = Path("fidus.yaml"),
) -> None:
    """Print the run state."""
    from fidus.cli.state_cmd import show

    try:
        show(config, ref, console)
    except FidusError as e:
        _fail(e)


@state_app.command("set-cursor")
def state_set_cursor(
    repo: Annotated[str, typer.Option(help="Source key: owner/repo, or local:<alias>.")],
    to: Annotated[
        str, typer.Option(help="ISO time (or 'now'): changes merged up to here are skipped.")
    ],
    config: ConfigOpt = Path("fidus.yaml"),
) -> None:
    """Move a source's cursor (e.g. to skip a rejected batch). Commit the file afterwards."""
    from fidus.cli.state_cmd import set_cursor

    try:
        set_cursor(config, repo, to, console)
    except FidusError as e:
        _fail(e)


@state_app.command("clear-retry")
def state_clear_retry(
    chapter: Annotated[str | None, typer.Option(help="Chapter id to act on.")] = None,
    all_: Annotated[bool, typer.Option("--all", help="Act on every retry entry.")] = False,
    unquarantine: Annotated[
        bool, typer.Option(help="Keep the entry but reset attempts so it is retried next run.")
    ] = False,
    config: ConfigOpt = Path("fidus.yaml"),
) -> None:
    """Drop retry entries, or lift a quarantine (--unquarantine). Commit the file afterwards."""
    from fidus.cli.state_cmd import clear_retry

    try:
        clear_retry(config, chapter, all_, unquarantine, console)
    except FidusError as e:
        _fail(e)


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
