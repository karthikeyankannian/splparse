"""splparse MCP server.

Exposes the parser, formatter, linter and analysis helpers as MCP tools so an
AI client (Claude Desktop, Claude Code, Cursor, ...) can validate and inspect
SPL *before* handing it to Splunk's own MCP server to execute.

Run:   splparse-mcp                 (stdio transport, the default for desktop clients)
       splparse-mcp --http 8765     (streamable HTTP on localhost:8765/mcp)

Requires the optional dependency:  pip install "splparse[mcp]"
"""
from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List

try:  # MCP SDK 2.x
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # pragma: no cover
    try:  # MCP SDK 1.x
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        sys.stderr.write('splparse-mcp needs the MCP SDK: pip install "splparse[mcp]"\n')
        raise

from . import __version__, analysis, lint as _lint, nodes as N, parse as _parse
from .errors import SPLSyntaxError
from .printer import format as _format
from .registry import KNOWN_COMMANDS

mcp = FastMCP(
    "splparse",
    instructions=(
        "Tools for working with Splunk SPL text. Call validate_spl or lint_spl on any "
        "search before executing it with a Splunk query tool; use format_spl to tidy a "
        "search for display; parse_spl returns the full AST as JSON."
    ),
)


def _error(e: SPLSyntaxError) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": e.message,
        "line": e.position.line if e.position else None,
        "column": e.position.column if e.position else None,
    }


@mcp.tool()
def validate_spl(spl: str) -> Dict[str, Any]:
    """Check whether an SPL search parses. Returns ok=true, or ok=false with the
    error message and line/column. Cheap; call before running any generated search."""
    try:
        q = _parse(spl)
    except SPLSyntaxError as e:
        return _error(e)
    return {"ok": True, "commands": [c.name for c in q.commands]}


@mcp.tool()
def lint_spl(spl: str) -> Dict[str, Any]:
    """Parse and lint an SPL search. Returns syntax errors, plus diagnostics such as
    unknown commands/functions, leading wildcards, index=* and expensive commands."""
    try:
        q = _parse(spl)
    except SPLSyntaxError as e:
        return _error(e)
    diags = _lint(q)
    return {
        "ok": not any(d.severity == "error" for d in diags),
        "diagnostics": [
            {"rule": d.rule, "severity": d.severity, "message": d.message,
             "snippet": spl[d.span[0]:d.span[1]] if d.span else None}
            for d in diags
        ],
    }


@mcp.tool()
def format_spl(spl: str, one_line: bool = False) -> Dict[str, Any]:
    """Pretty-print an SPL search, one command per line (or a single canonical line)."""
    try:
        q = _parse(spl)
    except SPLSyntaxError as e:
        return _error(e)
    return {"ok": True, "formatted": _format(q, one_line=one_line)}


@mcp.tool()
def parse_spl(spl: str) -> Dict[str, Any]:
    """Return the full abstract syntax tree of an SPL search as JSON
    (pipeline -> commands -> expressions, with source spans)."""
    try:
        q = _parse(spl)
    except SPLSyntaxError as e:
        return _error(e)
    return {"ok": True, "ast": q.to_dict()}


@mcp.tool()
def analyze_spl(spl: str) -> Dict[str, Any]:
    """Summarise what a search touches: indexes, fields referenced, commands used,
    initial-search filters, and whether it contains subsearches or macros."""
    try:
        q = _parse(spl)
    except SPLSyntaxError as e:
        return _error(e)
    filters = [{"field": b.left.name, "op": b.op, "value": getattr(b.right, "value", None)}
               for b in analysis.search_filters(q) if isinstance(b.left, N.Field)]
    return {
        "ok": True,
        "indexes": sorted(analysis.indexes(q)),
        "fields": sorted(analysis.referenced_fields(q)),
        "commands": analysis.commands_used(q),
        "initial_filters": filters,
        "has_subsearch": any(isinstance(n, N.Subsearch) for n in q.walk()),
        "macros": sorted({n.name for n in q.walk() if isinstance(n, N.MacroCall)}),
        "generating_first_command": not (isinstance(q.commands[0], N.SearchCommand) and q.commands[0].implicit)
        if q.commands else None,
    }


@mcp.tool()
def list_spl_commands() -> List[str]:
    """List the SPL command names splparse recognises (useful for grounding generation)."""
    return sorted(KNOWN_COMMANDS)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="splparse-mcp", description=f"splparse {__version__} MCP server")
    ap.add_argument("--http", metavar="PORT", type=int, help="serve streamable HTTP on this port instead of stdio")
    a = ap.parse_args(argv)
    if a.http:
        if hasattr(mcp, "settings"):      # SDK 1.x
            mcp.settings.port = a.http
            mcp.run(transport="streamable-http")
        else:                             # SDK 2.x
            mcp.run(transport="streamable-http", port=a.http)
    else:
        mcp.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
