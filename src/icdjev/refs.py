"""Parsing of code references printed inside rubric text ("E12.-", "J70.2-J70.4")."""
from __future__ import annotations

import re

from .models import CodeRef

_CODE = r"[A-Z]\d{2}(?:\.\d+)?"
_DASH = "-–—"
_RANGE = re.compile(rf"^\s*({_CODE})\s*[{_DASH}]\s*({_CODE})")
_SINGLE = re.compile(rf"^\s*({_CODE})(\.-|-(?!\s*[A-Z]))?")


def parse_ref(text: str, code_attr: str | None = None, usage: str | None = None) -> CodeRef | None:
    """Parse one <Reference> into a :class:`CodeRef`; ``None`` if it holds no code.

    The visible text is preferred over the ``code`` attribute because the attribute
    is absent on ~70% of exclusion references and points at a parent in others.
    """
    text = " ".join((text or "").split())
    m = _RANGE.match(text)
    if m:
        return CodeRef(raw=text, start=m.group(1), end=m.group(2), usage=usage)
    m = _SINGLE.match(text)
    if m:
        return CodeRef(raw=text, start=m.group(1), end=m.group(1), wildcard=bool(m.group(2)),
                       usage=usage)
    if code_attr:
        return CodeRef(raw=text or code_attr, start=code_attr.split("-")[0],
                       end=code_attr.split("-")[-1], usage=usage)
    return None


def code_key(code: str) -> tuple[str, int, int]:
    """Total order over codes: letter, 2-digit number, decimal part (-1 if none)."""
    m = re.match(r"^([A-Z])(\d{2})(?:\.(\d+))?", code)
    if not m:
        return ("", -1, -1)
    return (m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else -1)
