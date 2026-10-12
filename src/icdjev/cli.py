"""``icdjev`` command line: inspect a WHO file without any model or network access."""

from __future__ import annotations

import argparse
import json
import sys

from .catalogue import Catalogue
from .errors import IcdJevError


def _node(c: Catalogue, code: str) -> dict:
    n = c.get(code)
    return {
        "code": n.code,
        "kind": n.kind,
        "label": n.full_label,
        "assignable": n.assignable,
        "virtual": n.virtual,
        "usage": n.usage,
        "path": [p.code for p in c.path(code)],
        "children": n.children,
        "extensions": list(n.extensions),
        "inclusions": [r.text for r in n.inclusions],
        "exclusions": [
            {"from": s, "text": r.text, "targets": [x.raw for x in r.refs]}
            for s, r in c.ancestors_rules(code)["exclusion"]
        ],
        "notes": [{"kind": r.kind, "text": r.text} for r in n.notes],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="icdjev", description=__doc__)
    p.add_argument("--file", "-f", required=True, help="WHO ICD-10 ClaML .xml you downloaded")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="edition + counts")
    s = sub.add_parser("show", help="one code with path, inclusions, inherited exclusions")
    s.add_argument("code")
    t = sub.add_parser("tree", help="print a subtree")
    t.add_argument("code", nargs="?")
    t.add_argument("--depth", type=int, default=2)
    q = sub.add_parser("search", help="lexical search (no model)")
    q.add_argument("query")
    q.add_argument("-n", type=int, default=8)
    a = p.parse_args(argv)
    try:
        c = Catalogue.load(a.file)
        if a.cmd == "info":
            print(
                json.dumps(
                    {
                        "edition": c.edition.__dict__
                        if hasattr(c.edition, "__dict__")
                        else {k: getattr(c.edition, k) for k in c.edition.__slots__},
                        "stats": c.stats(),
                        "warnings": c.warnings,
                    },
                    indent=2,
                )
            )
        elif a.cmd == "show":
            print(json.dumps(_node(c, a.code), indent=2, ensure_ascii=False))
        elif a.cmd == "tree":

            def walk(code, d, ind=""):
                n = c.get(code)
                mark = {"dagger": " +", "aster": " *"}.get(n.usage or "", "")
                print(f"{ind}{n.code}{mark}  {n.label}{'  (virtual)' if n.virtual else ''}")
                if d > 0:
                    for k in n.children:
                        walk(k, d - 1, ind + "  ")

            for r in [c.get(a.code)] if a.code else c.chapters:
                walk(r.code, a.depth)
        elif a.cmd == "search":
            for n in c.search(a.query, a.n):
                print(f"{n.code:8} {n.full_label}")
    except IcdJevError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
