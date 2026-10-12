"""Parser for WHO ICD-10 ClaML exports (the file users download from WHO themselves)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .errors import UnsupportedFormatError
from .models import CodeRef, Edition, Rubric
from .refs import parse_ref

NOTE_KINDS = {"definition", "coding-hint", "note", "text", "introduction", "footnote"}
_BLOCKY = {"Fragment", "Para", "ListItem", "Row", "Cell", "Term", "Item", "Include"}


def flatten(el: ET.Element) -> str:
    """Flatten label markup to text without fusing words.

    ElementTree's ``itertext`` glues ``<Reference>`` text onto the preceding word
    ("malnutrition-relatedE12.-"), so we insert separators ourselves.
    """
    parts: list[str] = []

    def walk(e: ET.Element) -> None:
        if e.text:
            parts.append(e.text)
        for c in e:
            if c.tag in _BLOCKY or c.tag == "Reference":
                parts.append(" ")
            walk(c)
            if c.tag in _BLOCKY or c.tag == "Reference":
                parts.append(" ")
            if c.tail:
                parts.append(c.tail)

    walk(el)
    return " ".join("".join(parts).split())


def _refs(label: ET.Element) -> tuple[CodeRef, ...]:
    out = []
    for r in label.iter("Reference"):
        ref = parse_ref("".join(r.itertext()), r.get("code"), r.get("usage"))
        if ref:
            out.append(ref)
    return tuple(out)


def _rubric(ru: ET.Element) -> Rubric:
    label = ru.find("Label")
    if label is None:
        return Rubric(kind=ru.get("kind", ""), text="")
    return Rubric(kind=ru.get("kind", ""), text=flatten(label), refs=_refs(label))


class RawClass:
    """Parsed-but-unlinked <Class>/<ModifierClass> content."""

    __slots__ = (
        "code",
        "exclude_modifiers",
        "kind",
        "label_refs",
        "long",
        "modified_by",
        "preferred",
        "rubrics",
        "subs",
        "super",
        "usage",
    )

    def __init__(self, el: ET.Element):
        self.code = el.get("code", "")
        self.kind = el.get("kind", "")
        self.usage = el.get("usage")
        self.rubrics = [_rubric(r) for r in el.findall("Rubric")]
        pref = el.find("Rubric[@kind='preferred']/Label")
        self.preferred = flatten(pref) if pref is not None else ""
        self.label_refs = _refs(pref) if pref is not None else ()
        # preferredLong is the official title when it differs from the short label
        long_ = el.find("Rubric[@kind='preferredLong']/Label")
        self.long = flatten(long_) if long_ is not None else None
        sup = el.find("SuperClass")
        self.super = sup.get("code") if sup is not None else None
        self.subs = [s.get("code") for s in el.findall("SubClass")]
        self.modified_by = [m.get("code") for m in el.findall("ModifiedBy")]
        self.exclude_modifiers = [m.get("code") for m in el.findall("ExcludeModifier")]


def _first_lang(root: ET.Element) -> str:
    label = root.find("Class/Rubric/Label")
    if label is None:
        return "en"
    return label.get("{http://www.w3.org/XML/1998/namespace}lang", "en")


def read(path: str | Path):
    """Return ``(edition, classes, modifier_classes)`` from a ClaML file."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        raise UnsupportedFormatError(f"{path} is not valid XML: {e}") from e
    if root.tag != "ClaML":
        raise UnsupportedFormatError(f"Not a ClaML file (root element is {root.tag!r}).")
    ident = root.find("Identifier")
    title = root.find("Title")
    if ident is None or title is None:
        raise UnsupportedFormatError("ClaML file has no Identifier/Title.")
    authority = ident.get("authority", "")
    if authority != "WHO" or not (ident.get("uid", "").startswith("icd10")):
        raise UnsupportedFormatError(
            f"Expected a WHO ICD-10 ClaML file; got authority={authority!r} "
            f"uid={ident.get('uid')!r}. ICD-10-CM and other formats are not supported."
        )
    edition = Edition(
        authority=authority,
        uid=ident.get("uid", ""),
        version=title.get("version", ""),
        title=(title.text or "").strip(),
        date=title.get("date", ""),
        language=_first_lang(root),
    )
    classes = [RawClass(c) for c in root.findall("Class")]
    mod_classes: dict[str, list[RawClass]] = {}
    for m in root.findall("ModifierClass"):
        rc = RawClass(m)
        rc.kind = "modifierclass"
        mod_classes.setdefault(m.get("modifier", ""), []).append(rc)
    return edition, classes, mod_classes
