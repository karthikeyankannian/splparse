"""Convert an AST back into SPL text, optionally pretty-printed."""
from __future__ import annotations

import contextvars
import re
from functools import singledispatch, wraps
from typing import List

# True while printing a search-command expression: comparisons are written
# compactly (index=main) and hyphenated field names need no quoting.
_SEARCH_CTX: contextvars.ContextVar[bool] = contextvars.ContextVar("spl_search_ctx", default=False)
_SEARCH_FIELD = re.compile(r"^[^\s\"'`|()\[\],=<>!]+$")

from . import nodes as N
from .parser import PREC

_SAFE_FIELD = re.compile(r"^[A-Za-z_*][A-Za-z0-9_.:*{}@<>]*$")


def _name(s: str) -> str:
    """Quote a field name / rename target when it would not survive re-lexing."""
    rx = _SEARCH_FIELD if _SEARCH_CTX.get() else _SAFE_FIELD
    return s if rx.match(s) else _quote(s)


def _in_search(fn):
    @wraps(fn)
    def wrapper(n):
        tok = _SEARCH_CTX.set(True)
        try:
            return fn(n)
        finally:
            _SEARCH_CTX.reset(tok)
    return wrapper


def _out_of_search(fn):
    @wraps(fn)
    def wrapper(n):
        tok = _SEARCH_CTX.set(False)
        try:
            return fn(n)
        finally:
            _SEARCH_CTX.reset(tok)
    return wrapper


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _field(f: N.Expr) -> str:
    return to_spl(f)


def _needs_parens(child: N.Expr, parent_op: str, right_side: bool) -> bool:
    if isinstance(child, N.Binary):
        cp, pp = PREC.get(child.op, 99), PREC.get(parent_op, 99)
        if cp < pp:
            return True
        if cp == pp and right_side and parent_op in ("-", "/", "%"):
            return True
        return False
    return False


@singledispatch
def to_spl(node) -> str:
    raise TypeError(f"Cannot print {type(node).__name__}")


# ------------------------------------------------------------- expressions
@to_spl.register
def _(n: N.Literal) -> str:
    if n.kind == "string":
        return _quote(str(n.value))
    if n.kind == "bool":
        return "true()" if n.value else "false()"
    return str(n.value)


@to_spl.register
def _(n: N.Field) -> str:
    if _SEARCH_CTX.get():
        return n.name if _SEARCH_FIELD.match(n.name) else _quote(n.name)
    if n.quoted or not _SAFE_FIELD.match(n.name):
        return f"'{n.name}'"
    return n.name


@to_spl.register
def _(n: N.FunctionCall) -> str:
    return f"{n.name}({', '.join(to_spl(a) for a in n.args)})"


@to_spl.register
def _(n: N.Unary) -> str:
    inner = to_spl(n.operand)
    if isinstance(n.operand, N.Binary):
        inner = f"({inner})"
    return f"NOT {inner}" if n.op == "NOT" else f"{n.op}{inner}"


@to_spl.register
def _(n: N.Binary) -> str:
    left = to_spl(n.left)
    right = to_spl(n.right)
    if _needs_parens(n.left, n.op, False):
        left = f"({left})"
    if _needs_parens(n.right, n.op, True):
        right = f"({right})"
    if n.op == "AND" and n.implicit:
        return f"{left} {right}"
    if _SEARCH_CTX.get() and n.op in ("=", "!=", "<", ">", "<=", ">="):
        return f"{left}{n.op}{right}"
    return f"{left} {n.op} {right}"


@to_spl.register
def _(n: N.InExpr) -> str:
    vals = ", ".join(to_spl(v) for v in n.values)
    s = f"{to_spl(n.subject)} IN ({vals})"
    return f"NOT {s}" if n.negated else s


@to_spl.register
@_out_of_search
def _(n: N.Subsearch) -> str:
    return "[" + _pipeline_inline(n.pipeline) + "]"


@to_spl.register
def _(n: N.MacroCall) -> str:
    if n.raw:
        return n.raw
    return f"`{n.name}({', '.join(n.args)})`" if n.args else f"`{n.name}`"


# ---------------------------------------------------------- command parts
@to_spl.register
def _(n: N.KeyValue) -> str:
    return f"{n.key}={to_spl(n.value)}"


@to_spl.register
def _(n: N.Positional) -> str:
    s = to_spl(n.value)
    return f"{s} AS {_name(n.alias)}" if n.alias else s


@to_spl.register
def _(n: N.ByClause) -> str:
    return "BY " + ", ".join(_field(f) for f in n.fields)


@to_spl.register
def _(n: N.Assignment) -> str:
    t = n.target if _SAFE_FIELD.match(n.target) else f"'{n.target}'"
    return f"{t} = {to_spl(n.value)}"


@to_spl.register
def _(n: N.Aggregation) -> str:
    s = to_spl(n.func)
    return f"{s} AS {_name(n.alias)}" if n.alias else s


@to_spl.register
def _(n: N.SortKey) -> str:
    f = _field(n.field)
    if n.func:
        f = f"{n.func}({f})"
    return ("-" if n.descending else "") + f


@to_spl.register
def _(n: N.RenamePair) -> str:
    return f"{_field(n.source)} AS {_name(n.target)}"


# ---------------------------------------------------------------- commands
@to_spl.register
def _(n: N.GenericCommand) -> str:
    return " ".join([n.name] + [to_spl(a) for a in n.args])


@to_spl.register
def _(n: N.MacroCommand) -> str:
    return " ".join([to_spl(n.macro)] + [to_spl(a) for a in n.args])


@to_spl.register
@_in_search
def _(n: N.SearchCommand) -> str:
    body = to_spl(n.expr) if n.expr is not None else ""
    if n.implicit:
        return body
    return f"search {body}".rstrip()


@to_spl.register
def _(n: N.WhereCommand) -> str:
    return f"where {to_spl(n.expr)}"


@to_spl.register
def _(n: N.EvalCommand) -> str:
    return "eval " + ", ".join(to_spl(a) for a in n.assignments)


@to_spl.register
def _(n: N.StatsCommand) -> str:
    parts: List[str] = [n.name]
    pre = [o for o in n.options if o.key not in ("over",)]
    # options written before aggregations (span=, prestats=, datamodel= ...)
    parts += [to_spl(o) for o in pre if o.key != "datamodel"]
    if n.aggregations:
        parts.append(", ".join(to_spl(a) for a in n.aggregations))
    dm = [o for o in n.options if o.key == "datamodel"]
    if dm:
        parts.append("from " + to_spl(dm[0]))
    if n.where is not None:
        tok = _SEARCH_CTX.set(True)
        try:
            parts.append("where " + to_spl(n.where))
        finally:
            _SEARCH_CTX.reset(tok)
    over = [o for o in n.options if o.key == "over"]
    if over:
        parts.append("over " + to_spl(over[0].value))
    if n.by:
        parts.append(to_spl(n.by))
    return " ".join(parts)


@to_spl.register
def _(n: N.FieldsCommand) -> str:
    sign = "- " if n.remove else ""
    return f"fields {sign}" + ", ".join(_field(f) for f in n.fields)


@to_spl.register
def _(n: N.TableCommand) -> str:
    return "table " + ", ".join(_field(f) for f in n.fields)


@to_spl.register
def _(n: N.RenameCommand) -> str:
    return "rename " + ", ".join(to_spl(p) for p in n.pairs)


@to_spl.register
def _(n: N.SortCommand) -> str:
    parts = ["sort"]
    if n.limit is not None:
        parts.append(str(n.limit))
    parts += [to_spl(o) for o in n.options]
    parts.append(", ".join(to_spl(k) for k in n.keys))
    return " ".join(p for p in parts if p)


@to_spl.register
def _(n: N.HeadCommand) -> str:
    parts = ["head"]
    if n.count is not None:
        parts.append(str(n.count))
    if n.condition is not None:
        parts.append(to_spl(n.condition))
    parts += [to_spl(o) for o in n.options]
    return " ".join(parts)


@to_spl.register
def _(n: N.RexCommand) -> str:
    parts = ["rex"] + [to_spl(o) for o in n.options] + [_quote(n.pattern or "")]
    return " ".join(parts)


@to_spl.register
def _(n: N.LookupCommand) -> str:
    parts = ["lookup"] + [to_spl(o) for o in n.options] + [n.table]
    parts.append(", ".join(_pair(p) for p in n.input_fields))
    if n.output_mode:
        parts.append(n.output_mode)
        parts.append(", ".join(_pair(p) for p in n.output_fields))
    return " ".join(p for p in parts if p)


def _pair(p: N.RenamePair) -> str:
    src = _field(p.source)
    return src if src == p.target else f"{src} AS {_name(p.target)}"


@to_spl.register
def _(n: N.DedupCommand) -> str:
    parts = ["dedup"]
    if n.count is not None:
        parts.append(str(n.count))
    parts.append(", ".join(_field(f) for f in n.fields))
    parts += [to_spl(o) for o in n.options]
    if n.sort_by:
        parts.append("sortby " + " ".join(to_spl(k) for k in n.sort_by))
    return " ".join(p for p in parts if p)


@to_spl.register
def _(n: N.TopCommand) -> str:
    parts = [n.name] + [to_spl(o) for o in n.options]
    parts.append(", ".join(_field(f) for f in n.fields))
    if n.by:
        parts.append(to_spl(n.by))
    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------- pipeline
def _pipeline_inline(p: N.Pipeline) -> str:
    out: List[str] = []
    for i, c in enumerate(p.commands):
        s = to_spl(c)
        if i == 0 and isinstance(c, N.SearchCommand) and c.implicit:
            out.append(s)
        elif i == 0:
            out.append("| " + s)
        else:
            out.append(" | " + s)
    return "".join(out)


@to_spl.register
def _(n: N.Pipeline) -> str:
    return _pipeline_inline(n)


@to_spl.register
def _(n: N.Query) -> str:
    return _pipeline_inline(n.pipeline)


def format(query, indent: str = "", one_line: bool = False) -> str:
    """Pretty-print a parsed query (or a source string) one command per line."""
    if isinstance(query, str):
        from .parser import parse
        query = parse(query)
    if one_line:
        return to_spl(query)
    lines: List[str] = []
    for i, c in enumerate(query.pipeline.commands):
        s = to_spl(c)
        if i == 0 and isinstance(c, N.SearchCommand) and c.implicit:
            lines.append(indent + s)
        else:
            lines.append(indent + "| " + s)
    return "\n".join(lines)
