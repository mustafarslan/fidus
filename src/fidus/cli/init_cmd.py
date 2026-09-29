"""`fidus init` implementation: write fidus.yaml, then propose the outline."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

import typer
from rich.console import Console

from fidus.config.loader import dump_yaml, load_config
from fidus.errors import ConfigError

DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    "gemini": "gemini-3.8-flash",
    "openai": "gpt-6-sol",
    "fake": "heuristic-fake",
}
KEY_ENVS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
}

WORKFLOW_TEMPLATES = {"app": "workflow.yml", "github-token": "workflow-github-token.yml"}

CONFIG_HEADER = """\
Fidus configuration. Reference: https://github.com/mustafarslan/fidus/blob/master/docs/configuration.md
Secrets never go in this file: the LLM key is read from the env var named in llm.api_key_env,
and the GitHub token from FIDUS_GITHUB_TOKEN (set by the GitHub Action).
"""


def _source_entry(value: str) -> dict[str, object]:
    value = value.strip()
    if re.fullmatch(r"[\w.-]+/[\w.-]+", value) and not Path(value).exists():
        return {
            "repo": value,
            "include": ["**"],
            "exclude": ["**/test/**", "**/tests/**", "**/*.test.*"],
        }
    return {"path": value, "include": ["**"]}


def build_config(
    sources: list[str], provider: str, model: str, base_url: str | None, docs_dir: str
) -> dict[str, object]:
    llm: dict[str, object] = {"provider": provider, "model": model}
    if provider in KEY_ENVS:
        llm["api_key_env"] = KEY_ENVS[provider]
    if base_url:
        llm["base_url"] = base_url
    return {
        "version": 1,
        "docs": {"dir": docs_dir, "outline": "fidus.outline.yaml"},
        "sources": [_source_entry(s) for s in sources],
        "llm": llm,
        "schedule": {"audit": {"enabled": True, "every_days": 7}},
        "sync": {"branch": "fidus/sync", "labels": ["documentation", "fidus"], "reviewers": []},
    }


def run_init(
    *,
    config: Path,
    sources: list[str],
    provider: str | None,
    model: str | None,
    base_url: str | None,
    docs_dir: str,
    title: str | None,
    write_workflow: bool,
    auth: str = "app",
    config_only: bool,
    interactive: bool,
    force: bool,
    console: Console,
) -> None:
    # --force regenerates the outline/workflow; the config is only rewritten if new sources are given.
    if config.exists() and not (force and sources):
        console.print(f"Using existing {config}")
    else:
        if not sources and interactive:
            raw = typer.prompt("Source repositories (owner/repo or local paths, comma-separated)")
            sources = [s for s in (x.strip() for x in raw.split(",")) if s]
        if not sources:
            raise ConfigError("at least one --source is required")
        if provider is None:
            provider = (
                typer.prompt("LLM provider (anthropic, gemini, openai)", default="anthropic")
                if interactive
                else "anthropic"
            )
        if provider not in DEFAULT_MODELS:
            raise ConfigError(f"unknown provider {provider!r}")
        if model is None:
            model = (
                typer.prompt("Model", default=DEFAULT_MODELS[provider])
                if interactive
                else DEFAULT_MODELS[provider]
            )
        if provider == "openai" and base_url is None and interactive:
            base_url = (
                typer.prompt(
                    "OpenAI-compatible base URL (blank for api.openai.com; e.g. http://localhost:11434/v1)",
                    default="",
                    show_default=False,
                )
                or None
            )
        data = build_config(sources, provider, model, base_url, docs_dir)
        config.write_text(dump_yaml(data, header=CONFIG_HEADER), encoding="utf-8")
        console.print(f"[green]wrote[/] {config}")
    cfg = load_config(config)  # validates what we just wrote
    if auth not in WORKFLOW_TEMPLATES:
        raise ConfigError(f"--auth must be one of: {', '.join(WORKFLOW_TEMPLATES)}")

    if write_workflow:
        wf = config.parent / ".github" / "workflows" / "fidus.yml"
        if wf.exists() and not force:
            console.print(f"{wf} exists; skipping (use --force)")
        else:
            wf.parent.mkdir(parents=True, exist_ok=True)
            text = (
                resources.files("fidus.templates")
                .joinpath(WORKFLOW_TEMPLATES[auth])
                .read_text(encoding="utf-8")
            )
            key_env = cfg.llm.resolved_api_key_env or "OPENAI_API_KEY"
            wf.write_text(text.replace("ANTHROPIC_API_KEY", key_env), encoding="utf-8")
            console.print(f"[green]wrote[/] {wf}")

    if config_only:
        return
    outline_file = config.parent / cfg.docs.outline
    if outline_file.exists() and not force:
        console.print(
            f"{outline_file} exists; skipping outline proposal (use --force to regenerate)"
        )
        return
    from fidus.pipeline.init import propose_outline
    from fidus.pipeline.session import Options

    console.print("Analysing sources and proposing an outline (this can take a few minutes)…")
    outline, path = propose_outline(Options(config), title=title)
    n = sum(1 for _ in outline.chapters())
    console.print(
        f"[green]wrote[/] {path}: {len(outline.parts)} parts, {n} chapters.\n"
        "Next: review/edit the outline, run `fidus validate`, then `fidus bootstrap`."
    )
