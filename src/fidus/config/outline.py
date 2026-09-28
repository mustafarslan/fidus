"""Schema for fidus.outline.yaml: the single authority over the textbook's structure."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from posixpath import normpath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Chapter(_Strict):
    id: str
    title: str
    path: str
    level: Literal["fundamentals", "intermediate", "advanced"] = "intermediate"
    summary: str = ""
    sources: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        if not ID_RE.fullmatch(v):
            raise ValueError(f"chapter id {v!r} must be kebab-case ({ID_RE.pattern})")
        return v

    @field_validator("path")
    @classmethod
    def _path_safe(cls, v: str) -> str:
        if v.startswith("/") or "\\" in v or not v.endswith(".md"):
            raise ValueError(f"chapter path {v!r} must be relative, use '/', and end in .md")
        raw = v[2:] if v.startswith("./") else v
        norm = normpath(raw)
        if norm != raw or norm.startswith(".."):
            raise ValueError(f"chapter path {v!r} must not contain '..' or redundant segments")
        if any(part.startswith(".") for part in norm.split("/")):
            raise ValueError(f"chapter path {v!r} must not contain hidden segments")
        return norm

    @field_validator("sources")
    @classmethod
    def _sources_have_alias(cls, v: list[str]) -> list[str]:
        for s in v:
            if ":" not in s:
                raise ValueError(
                    f"source glob {s!r} must be prefixed with a source alias, e.g. 'api:src/**'"
                )
        return v


class Part(_Strict):
    id: str
    title: str
    chapters: list[Chapter] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        if not ID_RE.fullmatch(v):
            raise ValueError(f"part id {v!r} must be kebab-case")
        return v


@dataclass(frozen=True)
class NumberedChapter:
    number: str  # "2.1"
    part: Part
    chapter: Chapter


class Outline(_Strict):
    version: Literal[1] = 1
    title: str
    description: str = ""
    parts: list[Part] = Field(min_length=1)

    @model_validator(mode="after")
    def _cross_checks(self) -> Outline:
        ids: set[str] = set()
        paths: set[str] = set()
        part_ids: set[str] = set()
        for part in self.parts:
            if part.id in part_ids:
                raise ValueError(f"duplicate part id {part.id!r}")
            part_ids.add(part.id)
            for ch in part.chapters:
                if ch.id in ids:
                    raise ValueError(f"duplicate chapter id {ch.id!r}")
                if ch.path in paths:
                    raise ValueError(f"duplicate chapter path {ch.path!r}")
                for pre in ch.prerequisites:
                    if pre not in ids:
                        raise ValueError(
                            f"chapter {ch.id!r} lists prerequisite {pre!r}, which must be an "
                            "existing chapter that appears earlier in the outline"
                        )
                ids.add(ch.id)
                paths.add(ch.path)
        return self

    def chapters(self) -> Iterator[Chapter]:
        for part in self.parts:
            yield from part.chapters

    def numbered(self) -> Iterator[NumberedChapter]:
        for pi, part in enumerate(self.parts, start=1):
            for ci, ch in enumerate(part.chapters, start=1):
                yield NumberedChapter(f"{pi}.{ci}", part, ch)

    def get(self, chapter_id: str) -> Chapter:
        for ch in self.chapters():
            if ch.id == chapter_id:
                return ch
        raise KeyError(chapter_id)

    def number_of(self, chapter_id: str) -> str:
        for n in self.numbered():
            if n.chapter.id == chapter_id:
                return n.number
        raise KeyError(chapter_id)

    def dependents(self, chapter_id: str) -> list[Chapter]:
        """Chapters that list `chapter_id` as a prerequisite."""
        return [c for c in self.chapters() if chapter_id in c.prerequisites]

    def check_against_aliases(self, aliases: list[str], index_file: str | None) -> None:
        """Cross-validate with the config. Raises ValueError on the first problem."""
        for ch in self.chapters():
            for glob in ch.sources:
                alias = glob.split(":", 1)[0]
                if alias not in aliases:
                    raise ValueError(
                        f"chapter {ch.id!r} source {glob!r} uses unknown alias {alias!r}; "
                        f"known: {', '.join(aliases)}"
                    )
            if index_file and ch.path == index_file:
                raise ValueError(
                    f"chapter {ch.id!r} path collides with docs.index_file {index_file!r}"
                )
