"""Load fidus.yaml and fidus.outline.yaml with friendly error messages."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from fidus.config.models import FidusConfig
from fidus.config.outline import Outline
from fidus.errors import ConfigError

_M = TypeVar("_M", bound=BaseModel)


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.width = 100
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _plain(obj: Any) -> Any:
    """Convert ruamel CommentedMap/Seq into plain dicts/lists for pydantic."""
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


def read_yaml(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as fh:
            return _plain(_yaml().load(fh))
    except FileNotFoundError as e:
        raise ConfigError(f"{path} not found") from e
    except YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML: {e}") from e


def parse_yaml_text(text: str) -> Any:
    try:
        return _plain(_yaml().load(io.StringIO(text)))
    except YAMLError as e:
        raise ConfigError(f"invalid YAML: {e}") from e


def dump_yaml(data: Any, header: str | None = None) -> str:
    buf = io.StringIO()
    if header:
        for line in header.strip("\n").splitlines():
            buf.write(f"# {line}".rstrip() + "\n")
        buf.write("\n")
    _yaml().dump(data, buf)
    return buf.getvalue()


def _update_map(target: Any, new: dict[str, Any]) -> None:
    """Update a round-trip map in place: keys keep their position and comments."""
    for key in [k for k in target if k not in new]:
        del target[key]
    for key, value in new.items():
        if target.get(key) != value:
            target[key] = value


def merge_outline_yaml(existing_text: str, new: dict[str, Any]) -> str:
    """Apply a new outline onto the existing fidus.outline.yaml text, keeping comments and
    formatting of everything that did not change (parts and chapters are matched by id)."""
    y = _yaml()
    doc = y.load(io.StringIO(existing_text))
    if not isinstance(doc, dict) or not isinstance(doc.get("parts"), list):
        return dump_yaml(new)
    old_parts = {p.get("id"): p for p in doc["parts"] if isinstance(p, dict)}
    for key in ("version", "title", "description"):
        if key in new and doc.get(key) != new[key]:
            doc[key] = new[key]
    merged_parts = []
    for part in new.get("parts", []):
        old = old_parts.get(part["id"])
        if old is None:
            merged_parts.append(part)
            continue
        old_chapters = {c.get("id"): c for c in old.get("chapters") or [] if isinstance(c, dict)}
        chapters = []
        for ch in part.get("chapters", []):
            prev = old_chapters.get(ch["id"])
            if prev is None:
                chapters.append(ch)
            else:
                _update_map(prev, ch)
                chapters.append(prev)
        _update_map(
            old, {k: v for k, v in part.items() if k != "chapters"} | {"chapters": old["chapters"]}
        )
        old["chapters"][:] = chapters
        merged_parts.append(old)
    doc["parts"][:] = merged_parts
    buf = io.StringIO()
    y.dump(doc, buf)
    return buf.getvalue()


def format_validation_error(source: str, err: ValidationError) -> str:
    lines = [f"{source} is invalid:"]
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"]) or "(root)"
        lines.append(f"  - {loc}: {e['msg']}")
    return "\n".join(lines)


def _validate(model: type[_M], data: Any, source: str) -> _M:
    if data is None:
        raise ConfigError(f"{source} is empty")
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise ConfigError(format_validation_error(source, e)) from e


def load_config(path: Path) -> FidusConfig:
    return _validate(FidusConfig, read_yaml(path), str(path))


def outline_path(cfg: FidusConfig, config_path: Path) -> Path:
    return (config_path.parent / cfg.docs.outline).resolve()


def load_outline(cfg: FidusConfig, config_path: Path) -> Outline:
    path = outline_path(cfg, config_path)
    outline = _validate(Outline, read_yaml(path), str(path))
    try:
        outline.check_against_aliases(cfg.aliases, cfg.docs.index_file)
    except ValueError as e:
        raise ConfigError(f"{path}: {e}") from e
    return outline


def parse_outline_text(text: str, cfg: FidusConfig) -> Outline:
    outline = _validate(Outline, parse_yaml_text(text), "outline")
    try:
        outline.check_against_aliases(cfg.aliases, cfg.docs.index_file)
    except ValueError as e:
        raise ConfigError(str(e)) from e
    return outline
