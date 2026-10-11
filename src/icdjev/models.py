"""Plain data types describing one ICD-10 edition.

Everything here is derived from the user-supplied WHO ClaML file; nothing is bundled.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

NodeKind = Literal["chapter", "block", "category"]


@dataclass(frozen=True, slots=True)
class Edition:
    """Identity of the loaded classification file."""

    authority: str
    uid: str
    version: str
    title: str
    date: str
    language: str


@dataclass(frozen=True, slots=True)
class CodeRef:
    """A pointer to another code, code range or subtree found inside a rubric.

    ``raw`` is the text exactly as printed (``"E12.-"``, ``"J70.2-J70.4"``).
    ``start``/``end`` are the normalised bounds (``end == start`` for a single code).
    ``wildcard`` is True for ``"E12.-"`` style (every subdivision of E12).
    """

    raw: str
    start: str
    end: str
    wildcard: bool = False
    usage: str | None = None  # "dagger" | "aster" | None

    @property
    def is_range(self) -> bool:
        return self.start != self.end


@dataclass(frozen=True, slots=True)
class Rubric:
    """One piece of classification text attached to a node."""

    kind: str  # inclusion, exclusion, definition, coding-hint, note, text, ...
    text: str
    refs: tuple[CodeRef, ...] = ()

    @property
    def has_machine_target(self) -> bool:
        return bool(self.refs)


@dataclass(slots=True)
class Node:
    """A chapter, block, category or modifier-derived (virtual) subdivision.

    Treat as read-only; the :class:`~icdjev.catalogue.Catalogue` owns construction.
    """

    code: str
    kind: NodeKind
    label: str
    full_label: str
    long_label: str | None = None
    parent: str | None = None
    children: list[str] = field(default_factory=list)
    inclusions: tuple[Rubric, ...] = ()
    exclusions: tuple[Rubric, ...] = ()
    notes: tuple[Rubric, ...] = ()  # definition, coding-hint, note, text, introduction, footnote
    links: tuple[CodeRef, ...] = ()  # dagger/asterisk pointers found in label/inclusions
    usage: str | None = None  # "dagger" | "aster" | None
    virtual: bool = False  # created from a ModifierClass, not an explicit <Class>
    modifier: str | None = None  # modifier code that produced a virtual node
    extensions: tuple[str, ...] = ()  # optional 5th-character modifier codes (not expanded)
    excluded_modifiers: tuple[str, ...] = ()
    assignable: bool = False
