"""The in-memory, modifier-aware ICD-10 catalogue built from a user-supplied WHO file."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from . import claml
from .errors import UnknownCodeError
from .models import CodeRef, Edition, Node, Rubric
from .refs import code_key

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {
    "of",
    "the",
    "and",
    "or",
    "in",
    "to",
    "a",
    "an",
    "with",
    "without",
    "other",
    "nos",
    "not",
    "elsewhere",
    "classified",
    "unspecified",
    "due",
    "by",
    "for",
}


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


class Catalogue:
    """A single ICD-10 edition. Build with :meth:`load`."""

    def __init__(self, edition: Edition):
        self.edition = edition
        self._nodes: dict[str, Node] = {}
        self._roots: list[str] = []
        self.warnings: list[str] = []
        self._index: dict[str, set[str]] | None = None

    # ------------------------------------------------------------------ build
    @classmethod
    def load(cls, path: str | Path) -> Catalogue:
        edition, classes, mod_classes = claml.read(path)
        cat = cls(edition)
        by_code = {c.code: c for c in classes}
        for rc in classes:
            cat._add(rc)
        for rc in classes:
            parent = by_code.get(rc.super) if rc.super else None
            node = cat._nodes[rc.code]
            if parent is None:
                cat._roots.append(rc.code)
            else:
                node.parent = parent.code
        # explicit children in file order
        for rc in classes:
            cat._nodes[rc.code].children = [s for s in rc.subs if s in cat._nodes]
        cat._expand_modifiers(classes, mod_classes)
        cat._finalise()
        return cat

    def _add(
        self,
        rc: claml.RawClass,
        *,
        virtual: bool = False,
        modifier: str | None = None,
        code: str | None = None,
        label: str | None = None,
    ) -> Node:
        buckets: dict[str, list[Rubric]] = {"inclusion": [], "exclusion": [], "notes": []}
        for r in rc.rubrics:
            if r.kind == "inclusion":
                buckets["inclusion"].append(r)
            elif r.kind == "exclusion":
                buckets["exclusion"].append(r)
            elif r.kind in claml.NOTE_KINDS:
                buckets["notes"].append(r)
        links = tuple(ref for ref in rc.label_refs if ref.usage)
        node = Node(
            code=code or rc.code,
            kind=rc.kind if rc.kind in ("chapter", "block") else "category",
            label=label or rc.preferred,
            full_label=label or rc.preferred,
            long_label=rc.long,
            inclusions=tuple(buckets["inclusion"]),
            exclusions=tuple(buckets["exclusion"]),
            notes=tuple(buckets["notes"]),
            links=links,
            usage=rc.usage,
            virtual=virtual,
            modifier=modifier,
            excluded_modifiers=tuple(rc.exclude_modifiers),
        )
        self._nodes[node.code] = node
        return node

    def _expand_modifiers(self, classes, mod_classes) -> None:
        """Turn ``ModifiedBy`` into virtual children (4th character) or extensions (5th).

        * ``.N`` modifier classes (suffix ``_4``) become real child nodes: E11 -> E11.0 ... E11.9.
        * digit modifier classes (suffix ``_5``) are *optional* 5th characters; they are
          recorded on the assignable nodes as ``extensions`` and not expanded in v0.1.
        * block-level ``ModifiedBy`` (external causes, V01-Y98) is counted, not expanded.
        """
        unexpanded_blocks = 0
        for rc in classes:
            node = self._nodes[rc.code]
            for mcode in rc.modified_by:
                mcs = mod_classes.get(mcode, [])
                if not mcs:
                    self.warnings.append(f"{rc.code}: modifier {mcode} has no classes")
                    continue
                if node.kind == "block":
                    unexpanded_blocks += 1
                    continue
                if all(m.code.startswith(".") for m in mcs):
                    if node.children:  # explicit children already exist; nothing to add
                        continue
                    for m in mcs:
                        vcode = node.code + m.code
                        v = self._add(
                            m, virtual=True, modifier=mcode, code=vcode, label=m.preferred
                        )
                        v.parent = node.code
                        v.usage = m.usage
                        v.full_label = f"{node.label}, {m.preferred.lower()}"
                        node.children.append(vcode)
                else:
                    ext = tuple(f"{m.code}={m.preferred}" for m in mcs)
                    targets = [self._nodes[c] for c in node.children] or [node]
                    for t in targets:
                        t.extensions = ext
        if unexpanded_blocks:
            self.warnings.append(
                f"{unexpanded_blocks} block-level ModifiedBy entries (external-cause chapters) "
                "are not expanded in this version."
            )

    def _finalise(self) -> None:
        for n in self._nodes.values():
            n.assignable = n.kind == "category" and not n.children
            if n.kind == "category" and n.parent and not n.virtual:
                p = self._nodes.get(n.parent)
                if (
                    p
                    and p.kind == "category"
                    and n.full_label == n.label
                    and not n.label.lower().startswith(p.label.lower())
                ):
                    n.full_label = f"{p.label}: {n.label}"

    # ------------------------------------------------------------------ access
    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, code: str) -> bool:
        return code in self._nodes

    def get(self, code: str) -> Node:
        try:
            return self._nodes[code]
        except KeyError:
            raise UnknownCodeError(f"{code!r} is not in edition {self.edition.uid}") from None

    @property
    def chapters(self) -> list[Node]:
        return [self._nodes[c] for c in self._roots]

    def children(self, code: str | None = None) -> list[Node]:
        """Children of ``code`` (``None`` -> the chapters). Includes modifier-derived nodes."""
        if code is None:
            return self.chapters
        return [self._nodes[c] for c in self.get(code).children]

    def path(self, code: str) -> list[Node]:
        out, cur = [], self.get(code)
        while cur is not None:
            out.append(cur)
            cur = self._nodes.get(cur.parent) if cur.parent else None
        return out[::-1]

    def ancestors_rules(self, code: str) -> dict[str, list[tuple[str, Rubric]]]:
        """Exclusions/notes inherited from every ancestor (chapter and block rules apply
        to everything below them, but the file only stores them on the ancestor)."""
        out: dict[str, list[tuple[str, Rubric]]] = {"exclusion": [], "notes": []}
        for n in self.path(code):
            out["exclusion"] += [(n.code, r) for r in n.exclusions]
            out["notes"] += [(n.code, r) for r in n.notes]
        return out

    def exclusion_targets(self, code: str, *, inherited: bool = True) -> list[tuple[str, CodeRef]]:
        """Machine-resolvable exclusions as ``(source_node, ref)``; free-text ones are omitted."""
        rules = (
            self.ancestors_rules(code)["exclusion"]
            if inherited
            else [(code, r) for r in self.get(code).exclusions]
        )
        return [(src, ref) for src, r in rules for ref in r.refs]

    def free_text_exclusions(self, code: str) -> list[tuple[str, Rubric]]:
        return [(s, r) for s, r in self.ancestors_rules(code)["exclusion"] if not r.refs]

    def paired(self, code: str) -> list[CodeRef]:
        """Dagger/asterisk partners printed with this node (the manifestation or etiology)."""
        node = self.get(code)
        if not node.usage:
            return []
        refs = list(node.links)
        for r in node.inclusions:
            refs += [x for x in r.refs if x.usage]
        return refs

    def in_range(self, ref: CodeRef, code: str) -> bool:
        """True if ``code`` is covered by ``ref`` (single code, wildcard, or range)."""
        k, lo, hi = code_key(code), code_key(ref.start), code_key(ref.end)
        if not all(key[0] for key in (k, lo, hi)):
            return False
        if ref.is_range:
            # Include descendants of the upper bound without crossing its prefix.
            upper = (k[0], k[1], k[2][: len(hi[2])])
            return lo <= k and upper <= hi
        if ref.wildcard or not lo[2]:
            return k[:2] == lo[:2] and k[2].startswith(lo[2])
        return k == lo

    def validate(self, code: str) -> tuple[bool, str]:
        """Deterministic check for any code a model proposes."""
        if code not in self._nodes:
            return False, "not in this edition"
        n = self._nodes[code]
        if n.kind != "category":
            return False, f"{n.kind}s are navigation nodes, not assignable codes"
        if n.children:
            return False, "needs a more specific subdivision: " + ", ".join(n.children[:12])
        return True, "ok"

    # ------------------------------------------------------------------ search
    def _build_index(self) -> dict[str, set[str]]:
        idx: dict[str, set[str]] = {}
        for n in self._nodes.values():
            if n.kind != "category":
                continue
            text = " ".join([n.full_label, n.long_label or ""] + [r.text for r in n.inclusions])
            for t in set(_tokens(text)):
                idx.setdefault(t, set()).add(n.code)
        return idx

    def search(self, query: str, limit: int = 10, assignable_only: bool = False) -> list[Node]:
        """Plain lexical search over labels and inclusion terms (no model, no network)."""
        if self._index is None:
            self._index = self._build_index()
        scores: Counter[str] = Counter()
        for t in _tokens(query):
            for c in self._index.get(t, ()):
                scores[c] += 1
        ranked = sorted(scores, key=lambda c: (-scores[c], len(self._nodes[c].label), c))
        nodes = [self._nodes[c] for c in ranked]
        if assignable_only:
            nodes = [n for n in nodes if n.assignable]
        return nodes[:limit]

    def stats(self) -> dict[str, int]:
        kinds = Counter(n.kind for n in self._nodes.values())
        return {
            **kinds,
            "virtual": sum(n.virtual for n in self._nodes.values()),
            "assignable": sum(n.assignable for n in self._nodes.values()),
            "total": len(self._nodes),
        }
