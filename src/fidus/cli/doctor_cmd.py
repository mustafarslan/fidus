"""`fidus doctor`: diagnose a setup without changing anything."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

from rich.console import Console

from fidus.config.loader import load_config
from fidus.errors import FidusError
from fidus.github.auth import docs_repo_slug, docs_token, source_token
from fidus.github.client import GitHubClient
from fidus.github.pulls import GitHubAPI

SDK_MODULE = {"anthropic": "anthropic", "gemini": "google.genai", "openai": "openai"}
EXTRA = {"anthropic": "anthropic", "gemini": "gemini", "openai": "openai"}


def docs_access_checks(
    api: GitHubAPI, slug: str, source_repos: list[str]
) -> list[tuple[bool | None, str]]:
    """Can this token push to the docs repo, and (for App tokens) is the App installed on every
    repo Fidus needs? Returns (ok, message) pairs; ok=None means a warning."""
    out: list[tuple[bool | None, str]] = []
    try:
        repo = api.repo(slug)
    except FidusError as e:
        return [(False, f"docs repo {slug}: {e}")]
    installed: dict[str, dict[str, bool]] | None
    try:  # only GitHub App installation tokens can call this endpoint
        found: dict[str, dict[str, bool]] = {}
        for page in api.c.paginate("/installation/repositories"):
            for r in page.get("repositories", []):
                found[r["full_name"].lower()] = r.get("permissions") or {}
        installed = found
    except FidusError:
        installed = None  # a PAT or GITHUB_TOKEN
    perms = repo.get("permissions") or (installed or {}).get(slug.lower()) or {}
    if perms.get("push"):
        out.append((True, f"docs repo {slug}: token can push"))
    elif perms:
        out.append(
            (
                False,
                f"docs repo {slug}: token cannot push; grant Contents: Read and write "
                "(and Pull requests: Read and write)",
            )
        )
    else:
        out.append((None, f"docs repo {slug} reachable, but push access could not be verified"))
    if installed is not None:
        missing = [r for r in [slug, *source_repos] if r.lower() not in installed]
        if missing:
            out.append(
                (
                    False,
                    "GitHub App is not installed on: "
                    + ", ".join(missing)
                    + " (App settings → Install App → add the repositories)",
                )
            )
        else:
            out.append((True, f"GitHub App installed on all {1 + len(source_repos)} repositories"))
    return out


def run_doctor(config: Path, *, ping: bool, console: Console) -> bool:
    ok = True

    def good(msg: str) -> None:
        console.print(f"[green]✓[/] {msg}")

    def bad(msg: str) -> None:
        nonlocal ok
        ok = False
        console.print(f"[red]✗[/] {msg}")

    def warn(msg: str) -> None:
        console.print(f"[yellow]![/] {msg}")

    good(f"python {sys.version.split()[0]}")
    good("git found") if shutil.which("git") else bad("git not found on PATH")

    try:
        cfg = load_config(config)
        good(f"{config} is valid")
    except FidusError as e:
        bad(str(e))
        return False

    token = docs_token()
    if token:
        good("GitHub token present (FIDUS_GITHUB_TOKEN / GH_TOKEN / GITHUB_TOKEN)")
    else:
        warn("no GitHub token; only public repos and --dry-run will work")
    api = GitHubAPI(GitHubClient(token, cfg.github.api_url))
    try:
        for src in cfg.sources:
            if not src.repo:
                p = (config.resolve().parent / (src.path or "")).resolve()
                good(f"source {src.name}: local path {p}") if p.is_dir() else bad(
                    f"source {src.name}: {p} missing"
                )
                continue
            s_api = (
                api
                if source_token(src) == token
                else GitHubAPI(GitHubClient(source_token(src), cfg.github.api_url))
            )
            try:
                repo = s_api.repo(src.repo)
                s_api.c.get(f"/repos/{src.repo}/pulls", state="closed", per_page=1)
                good(
                    f"source {src.name}: {src.repo} readable (default branch {repo['default_branch']})"
                )
            except FidusError as e:
                bad(
                    f"source {src.name}: cannot read {src.repo}: {e} — is the GitHub App installed on it?"
                )
        slug = docs_repo_slug()
        if slug:
            for status, msg in docs_access_checks(
                api, slug, [x.repo for x in cfg.sources if x.repo]
            ):
                (good if status is True else warn if status is None else bad)(msg)
        else:
            warn("docs repo unknown locally (set FIDUS_DOCS_REPO=owner/name to check it)")
    finally:
        api.c.close()

    prov = cfg.llm.provider
    if prov == "fake":
        good("llm provider: fake (offline)")
        return ok
    module = SDK_MODULE[prov]
    if importlib.util.find_spec(module.split(".")[0]) is None:
        bad(f"SDK for {prov} not installed: pip install 'fidus[{EXTRA[prov]}]'")
        return False
    good(f"SDK for {prov} installed")
    from fidus.llm.base import resolve_api_key

    key = resolve_api_key(cfg.llm, required=False)
    if key:
        good(f"API key found in ${cfg.llm.resolved_api_key_env}")
    elif prov == "openai" and cfg.llm.base_url:
        warn("no API key set (fine for local servers such as Ollama)")
    else:
        bad(f"API key missing: set ${cfg.llm.resolved_api_key_env}")
        return False
    if ping:
        ok = _ping(cfg, console) and ok
    return ok


def _ping(cfg: object, console: Console) -> bool:
    from fidus.config.models import FidusConfig
    from fidus.llm.base import make_provider
    from fidus.llm.types import ToolSpec, user

    assert isinstance(cfg, FidusConfig)
    tool = ToolSpec(
        "echo",
        "Echo a word back.",
        {"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"]},
    )
    try:
        provider = make_provider(cfg.llm)
        c = provider.complete(
            system="You are a test harness.",
            messages=[user("Call the echo tool with word='fidus'.")],
            tools=[tool],
            max_tokens=200,
            temperature=0.0,
        )
    except FidusError as e:
        console.print(f"[red]✗[/] LLM call failed: {e}")
        return False
    if any(tc.name == "echo" for tc in c.message.tool_calls):
        console.print(
            f"[green]✓[/] {cfg.llm.provider}/{cfg.llm.model} answered with a tool call "
            f"({c.usage.input_tokens}+{c.usage.output_tokens} tokens)"
        )
        return True
    console.print(
        f"[yellow]![/] model replied without a tool call: {c.message.text[:120]!r}. "
        "The default `llm.tool_protocol: auto` falls back to the JSON protocol when that happens; "
        "set `json` to use it always."
    )
    return False
