"""A small, extensible linter for SPL searches."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from . import nodes as N
from .registry import EVAL_FUNCTIONS, KNOWN_COMMANDS, STATS_FUNCTIONS


@dataclass
class Diagnostic:
    rule: str
    message: str
    severity: str = "warning"        # error | warning | info
    span: Optional[N.Span] = None

    def __str__(self) -> str:
        loc = f"@{self.span[0]}-{self.span[1]} " if self.span else ""
        return f"[{self.severity}] {self.rule}: {loc}{self.message}"


Rule = Callable[[N.Query], List[Diagnostic]]
RULES: List[Rule] = []


def rule(fn: Rule) -> Rule:
    RULES.append(fn)
    return fn


@rule
def unknown_command(q: N.Query) -> List[Diagnostic]:
    out = []
    for node in q.walk():
        if isinstance(node, N.Command) and node.name.lower() not in KNOWN_COMMANDS:
            out.append(Diagnostic("unknown-command", f"'{node.name}' is not a known SPL command "
                                  "(custom command or typo?)", "error", node.span))
    return out


@rule
def unknown_eval_function(q: N.Query) -> List[Diagnostic]:
    out = []
    for cmd in q.commands:
        if isinstance(cmd, (N.EvalCommand, N.WhereCommand)):
            for node in cmd.walk():
                if isinstance(node, N.FunctionCall) and node.name.lower() not in EVAL_FUNCTIONS:
                    out.append(Diagnostic("unknown-eval-function",
                                          f"'{node.name}' is not a known eval function", "error", node.span))
    return out


@rule
def unknown_stats_function(q: N.Query) -> List[Diagnostic]:
    out = []
    for cmd in q.commands:
        if isinstance(cmd, N.StatsCommand):
            for agg in cmd.aggregations:
                f = agg.func
                name = f.name if isinstance(f, (N.FunctionCall, N.Field)) else ""
                base = name.lower()
                if base.startswith(("p", "perc", "exactperc", "upperperc")) and base.rstrip("0123456789.") in ("p", "perc", "exactperc", "upperperc"):
                    continue
                if base and base not in STATS_FUNCTIONS:
                    out.append(Diagnostic("unknown-stats-function",
                                          f"'{name}' is not a known statistical function", "error", agg.span))
    return out


@rule
def leading_wildcard(q: N.Query) -> List[Diagnostic]:
    out = []
    for cmd in q.commands:
        if isinstance(cmd, N.SearchCommand) and cmd.expr is not None:
            for node in cmd.expr.walk():
                if isinstance(node, N.Literal) and node.kind == "term" \
                        and str(node.value).startswith("*") and str(node.value) != "*":
                    out.append(Diagnostic("leading-wildcard",
                                          f"'{node.value}' starts with a wildcard; leading wildcards "
                                          "cannot use the index and are slow", "warning", node.span))
    return out


@rule
def missing_index(q: N.Query) -> List[Diagnostic]:
    if not q.commands:
        return []
    first = q.commands[0]
    if not isinstance(first, N.SearchCommand):
        return []
    if first.expr is None:
        return [Diagnostic("missing-index", "Search has no terms at all", "warning", first.span)]
    for node in first.expr.walk():
        if isinstance(node, N.Binary) and node.op == "=" and isinstance(node.left, N.Field) \
                and node.left.name.lower() == "index":
            if isinstance(node.right, N.Literal) and str(node.right.value) == "*":
                return [Diagnostic("wildcard-index", "index=* searches every index; "
                                   "name the indexes you need", "warning", node.span)]
            return []
        if isinstance(node, N.InExpr) and isinstance(node.subject, N.Field) \
                and node.subject.name.lower() == "index":
            return []
        if isinstance(node, (N.MacroCall, N.Subsearch)):
            return []  # can't see inside; don't nag
    return [Diagnostic("missing-index", "Initial search does not restrict index=; "
                       "add one for performance", "info", first.span)]


@rule
def expensive_commands(q: N.Query) -> List[Diagnostic]:
    advice = {
        "join": "join runs a subsearch (50k row limit) and is slow; prefer stats ... by, or lookup",
        "transaction": "transaction is memory heavy; stats with earliest/latest/values is usually enough",
        "append": "append runs a subsearch; consider multisearch or stats for large result sets",
    }
    out = []
    for cmd in q.commands:
        if cmd.name.lower() in advice:
            out.append(Diagnostic(f"expensive-{cmd.name.lower()}", advice[cmd.name.lower()], "info", cmd.span))
    return out


@rule
def search_mid_pipeline(q: N.Query) -> List[Diagnostic]:
    out = []
    for cmd in q.commands[1:]:
        if isinstance(cmd, N.SearchCommand) and cmd.expr is not None:
            # comparisons on numeric fields after stats are better served by where
            ops = {n.op for n in cmd.expr.walk() if isinstance(n, N.Binary)}
            if ops & {"<", ">", "<=", ">="}:
                out.append(Diagnostic("search-vs-where", "numeric comparison after the initial search: "
                                      "'where' is clearer and supports field-to-field comparison", "info", cmd.span))
    return out


@rule
def eval_assign_with_double_equals(q: N.Query) -> List[Diagnostic]:
    out = []
    for cmd in q.commands:
        if isinstance(cmd, N.EvalCommand):
            for a in cmd.assignments:
                if isinstance(a.value, N.Binary) and a.value.op == "=" :
                    out.append(Diagnostic("eval-single-equals",
                                          f"'{a.target} = ... = ...': comparison inside eval uses '==' "
                                          "(single '=' is assignment); check intent", "warning", a.span))
    return out


def lint(query, rules: Optional[List[Rule]] = None) -> List[Diagnostic]:
    """Run lint rules over a Query (or a source string)."""
    if isinstance(query, str):
        from .parser import parse
        query = parse(query)
    diags: List[Diagnostic] = []
    for r in rules or RULES:
        diags.extend(r(query))
    diags.sort(key=lambda d: (d.span or (0, 0))[0])
    return diags
