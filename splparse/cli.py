"""Command-line interface: ``splparse parse|fmt|lint|tokens``."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__, lint, parse, tokenize
from .errors import SPLSyntaxError
from .printer import format as fmt


def _read(arg: str) -> str:
    if arg == "-":
        return sys.stdin.read()
    return arg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="splparse", description="Parse, format and lint Splunk SPL.")
    ap.add_argument("--version", action="version", version=f"splparse {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("parse", help="print the AST as JSON")
    p.add_argument("query", help="SPL text, or '-' to read stdin")
    p.add_argument("--compact", action="store_true")

    f = sub.add_parser("fmt", help="pretty-print one command per line")
    f.add_argument("query")
    f.add_argument("--one-line", action="store_true")

    l = sub.add_parser("lint", help="report problems")
    l.add_argument("query")
    l.add_argument("--json", action="store_true")

    t = sub.add_parser("tokens", help="dump the token stream")
    t.add_argument("query")

    a = ap.parse_args(argv)
    src = _read(a.query).strip()
    try:
        if a.cmd == "tokens":
            for tok in tokenize(src):
                print(f"{tok.start.line}:{tok.start.column:<4} {tok.kind.name:<9} {tok.text}")
            return 0
        q = parse(src)
        if a.cmd == "parse":
            print(json.dumps(q.to_dict(), indent=None if a.compact else 2))
        elif a.cmd == "fmt":
            print(fmt(q, one_line=a.one_line))
        elif a.cmd == "lint":
            diags = lint(q)
            if a.json:
                print(json.dumps([d.__dict__ for d in diags], indent=2))
            else:
                for d in diags:
                    print(d)
                if not diags:
                    print("No issues found.")
            return 1 if any(d.severity == "error" for d in diags) else 0
    except SPLSyntaxError as e:
        print(f"syntax error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
