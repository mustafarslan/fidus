from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from fidus.config.loader import load_config, load_outline, parse_yaml_text
from fidus.config.models import FidusConfig
from fidus.config.outline import Outline
from fidus.errors import ConfigError
from tests.conftest import OUTLINE_YAML


def test_defaults_and_alias_derivation() -> None:
    cfg = FidusConfig.model_validate({"sources": [{"repo": "acme/api-server"}, {"path": "../web"}]})
    assert cfg.aliases == ["api-server", "web"]
    assert cfg.llm.provider == "anthropic"
    assert cfg.llm.resolved_api_key_env == "ANTHROPIC_API_KEY"
    assert cfg.sources[0].key == "acme/api-server"
    assert cfg.sources[1].key == "local:web"
    assert cfg.llm.temperature is None


@pytest.mark.parametrize(
    "sources",
    [
        [{"repo": "acme/api", "path": "../x"}],  # both
        [{}],  # neither
        [{"repo": "not-a-slug"}],
        [{"repo": "a/b", "alias": "Bad Alias"}],
        [{"repo": "a/x", "alias": "dup"}, {"repo": "b/y", "alias": "dup"}],
    ],
)
def test_invalid_sources(sources: list[dict[str, str]]) -> None:
    with pytest.raises(ValidationError):
        FidusConfig.model_validate({"sources": sources})


def test_unknown_keys_rejected() -> None:
    with pytest.raises(ValidationError, match="extra"):
        FidusConfig.model_validate({"sources": [{"repo": "a/b"}], "llm": {"modle": "x"}})


def test_docs_dir_must_be_relative() -> None:
    with pytest.raises(ValidationError):
        FidusConfig.model_validate({"sources": [{"repo": "a/b"}], "docs": {"dir": "../elsewhere"}})


def test_outline_valid(outline: Outline) -> None:
    assert [n.number for n in outline.numbered()] == ["1.1", "1.2", "2.1"]
    assert [c.id for c in outline.dependents("data-model")] == ["auth"]
    assert outline.number_of("auth") == "2.1"


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda d: d["parts"][0]["chapters"][1].update(id="overview"), "duplicate chapter id"),
        (
            lambda d: d["parts"][0]["chapters"][1].update(path="part-1/01-overview.md"),
            "duplicate chapter path",
        ),
        (lambda d: d["parts"][0]["chapters"][0].update(prerequisites=["auth"]), "earlier"),
        (lambda d: d["parts"][0]["chapters"][0].update(path="../escape.md"), "'..'"),
        (lambda d: d["parts"][0]["chapters"][0].update(path="/abs.md"), "relative"),
        (lambda d: d["parts"][0]["chapters"][0].update(path=".hidden/x.md"), "hidden"),
        (lambda d: d["parts"][0]["chapters"][0].update(sources=["no-alias/**"]), "alias"),
        (lambda d: d["parts"][0]["chapters"][0].update(id="Not Kebab"), "kebab"),
    ],
)
def test_outline_invalid(mutate: object, match: str) -> None:
    data = parse_yaml_text(OUTLINE_YAML)
    mutate(data)  # type: ignore[operator]
    with pytest.raises(ValidationError, match=match):
        Outline.model_validate(data)


def test_outline_alias_cross_check(tmp_path: Path) -> None:
    (tmp_path / "fidus.yaml").write_text("sources: [{repo: acme/other, alias: other}]\n")
    (tmp_path / "fidus.outline.yaml").write_text(OUTLINE_YAML)
    cfg = load_config(tmp_path / "fidus.yaml")
    with pytest.raises(ConfigError, match="unknown alias 'app'"):
        load_outline(cfg, tmp_path / "fidus.yaml")


def test_load_config_errors_are_friendly(tmp_path: Path) -> None:
    p = tmp_path / "fidus.yaml"
    with pytest.raises(ConfigError, match="not found"):
        load_config(p)
    p.write_text("sources: []\n")
    with pytest.raises(ConfigError, match="sources"):
        load_config(p)
    p.write_text("sources: [unclosed\n")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(p)
