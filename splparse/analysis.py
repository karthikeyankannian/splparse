"""Small static-analysis helpers over a parsed query."""
from __future__ import annotations

from typing import List, Set

from . import nodes as N


def commands_used(query: N.Query, include_subsearches: bool = True) -> List[str]:
    """Command names in pipeline order (subsearch commands included by default)."""
    out: List[str] = []
    src = query.walk() if include_subsearches else iter(query.commands)
    for n in src:
        if isinstance(n, N.Command):
            out.append(n.name.lower())
    return out


def referenced_fields(query: N.Query) -> Set[str]:
    """Every field name the search reads or writes (best effort)."""
    names: Set[str] = set()
    for n in query.walk():
        if isinstance(n, N.Field) and n.name != "*":
            names.add(n.name)
        elif isinstance(n, N.Assignment):
            names.add(n.target)
        elif isinstance(n, N.RenamePair):
            names.add(n.target)
        elif isinstance(n, N.Aggregation) and n.alias:
            names.add(n.alias)
        elif isinstance(n, N.Positional) and n.alias:
            names.add(n.alias)
    return names


def search_filters(query: N.Query) -> List[N.Binary]:
    """All ``field=value``-style comparisons in the initial search (for index/sourcetype inventories)."""
    first = query.commands[0] if query.commands else None
    if not isinstance(first, N.SearchCommand) or first.expr is None:
        return []
    return [n for n in first.expr.walk()
            if isinstance(n, N.Binary) and n.op in ("=", "!=") and isinstance(n.left, N.Field)]


def indexes(query: N.Query) -> Set[str]:
    """Index names referenced by the initial search (``index=x`` or ``index IN (...)``)."""
    out: Set[str] = set()
    first = query.commands[0] if query.commands else None
    if not isinstance(first, N.SearchCommand) or first.expr is None:
        return out
    for n in first.expr.walk():
        if isinstance(n, N.Binary) and n.op == "=" and isinstance(n.left, N.Field) \
                and n.left.name.lower() == "index" and isinstance(n.right, N.Literal):
            out.add(str(n.right.value))
        elif isinstance(n, N.InExpr) and isinstance(n.subject, N.Field) and n.subject.name.lower() == "index":
            out.update(str(v.value) for v in n.values if isinstance(v, N.Literal))
    return out
