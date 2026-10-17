"""Abstract syntax tree node types for SPL.

Every node is a frozen-ish dataclass with an optional ``span`` (start, end
character offsets into the original source).  ``to_dict()`` produces a
JSON-serialisable representation with a ``type`` key on every node.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Dict, List, Optional, Tuple

Span = Tuple[int, int]


@dataclass
class Node:
    span: Optional[Span] = field(default=None, kw_only=True, repr=False, compare=False)

    @property
    def type(self) -> str:
        return self.__class__.__name__

    def children(self) -> List["Node"]:
        out: List[Node] = []
        for f in fields(self):
            if f.name == "span":
                continue
            v = getattr(self, f.name)
            if isinstance(v, Node):
                out.append(v)
            elif isinstance(v, list):
                out.extend(x for x in v if isinstance(x, Node))
        return out

    def walk(self):
        """Depth-first pre-order traversal."""
        yield self
        for c in self.children():
            yield from c.walk()

    def to_dict(self) -> Dict[str, Any]:
        return _to_dict(self)


def _to_dict(v: Any) -> Any:
    if isinstance(v, Node):
        d: Dict[str, Any] = {"type": v.type}
        for f in fields(v):
            if f.name == "span":
                continue
            d[f.name] = _to_dict(getattr(v, f.name))
        if v.span is not None:
            d["span"] = list(v.span)
        return d
    if isinstance(v, list):
        return [_to_dict(x) for x in v]
    if is_dataclass(v):  # pragma: no cover - defensive
        return {f.name: _to_dict(getattr(v, f.name)) for f in fields(v)}
    return v


# ----------------------------------------------------------------- expressions
@dataclass
class Expr(Node):
    pass


@dataclass
class Literal(Expr):
    """A constant. ``kind`` is 'string', 'number', 'bool' or 'term'.

    'term' is a bare, unquoted search value such as ``web*`` or ``-24h@h``.
    """
    value: Any
    kind: str = "term"


@dataclass
class Field(Expr):
    name: str
    quoted: bool = False      # was written as 'field name'


@dataclass
class FunctionCall(Expr):
    name: str
    args: List[Expr] = field(default_factory=list)


@dataclass
class Unary(Expr):
    op: str                   # 'NOT', '-', '+'
    operand: Expr


@dataclass
class Binary(Expr):
    """Binary operation.

    ``op`` is one of: AND OR XOR = == != < > <= >= LIKE . + - * / %
    ``implicit`` marks an AND that was written as plain juxtaposition
    (``index=main error`` == ``index=main AND error``).
    """
    op: str
    left: Expr
    right: Expr
    implicit: bool = False


@dataclass
class InExpr(Expr):
    subject: Expr
    values: List[Expr]
    negated: bool = False


@dataclass
class Subsearch(Expr):
    pipeline: "Pipeline"


@dataclass
class MacroCall(Expr):
    name: str
    args: List[str] = field(default_factory=list)
    raw: str = ""


# ------------------------------------------------------------- command pieces
@dataclass
class KeyValue(Node):
    """``key=value`` option on a command."""
    key: str
    value: Expr


@dataclass
class Positional(Node):
    """A bare argument, optionally aliased with AS."""
    value: Expr
    alias: Optional[str] = None


@dataclass
class ByClause(Node):
    fields: List[Expr]


@dataclass
class Assignment(Node):
    """``target = expr`` as used by eval."""
    target: str
    value: Expr


@dataclass
class Aggregation(Node):
    """``func(arg) AS alias`` as used by stats-family commands."""
    func: Expr                 # FunctionCall or Field (bare "count")
    alias: Optional[str] = None


@dataclass
class SortKey(Node):
    field: Expr
    descending: bool = False
    func: Optional[str] = None   # auto, str, ip, num


@dataclass
class RenamePair(Node):
    source: Expr
    target: str


# ------------------------------------------------------------------- commands
@dataclass
class Command(Node):
    name: str
    raw: str = field(default="", repr=False, compare=False)


@dataclass
class GenericCommand(Command):
    """Fallback for commands without a dedicated grammar."""
    args: List[Node] = field(default_factory=list)   # KeyValue | Positional | ByClause | Subsearch


@dataclass
class MacroCommand(Command):
    """A macro used in command position: ``| `my_macro(x)` ``."""
    macro: Optional[MacroCall] = None
    args: List[Node] = field(default_factory=list)


@dataclass
class SearchCommand(Command):
    expr: Optional[Expr] = None
    implicit: bool = False     # leading search with no explicit "search" keyword


@dataclass
class WhereCommand(Command):
    expr: Optional[Expr] = None


@dataclass
class EvalCommand(Command):
    assignments: List[Assignment] = field(default_factory=list)


@dataclass
class StatsCommand(Command):
    """stats, tstats, eventstats, streamstats, chart, timechart, sistats..."""
    aggregations: List[Aggregation] = field(default_factory=list)
    by: Optional[ByClause] = None
    options: List[KeyValue] = field(default_factory=list)
    where: Optional[Expr] = None         # tstats ... where <search>
    from_: Optional[str] = None          # tstats ... from datamodel=X


@dataclass
class FieldsCommand(Command):
    fields: List[Expr] = field(default_factory=list)
    remove: bool = False


@dataclass
class TableCommand(Command):
    fields: List[Expr] = field(default_factory=list)


@dataclass
class RenameCommand(Command):
    pairs: List[RenamePair] = field(default_factory=list)


@dataclass
class SortCommand(Command):
    keys: List[SortKey] = field(default_factory=list)
    limit: Optional[int] = None
    options: List[KeyValue] = field(default_factory=list)


@dataclass
class HeadCommand(Command):
    count: Optional[int] = None
    condition: Optional[Expr] = None
    options: List[KeyValue] = field(default_factory=list)


@dataclass
class RexCommand(Command):
    pattern: Optional[str] = None
    options: List[KeyValue] = field(default_factory=list)   # field=, max_match=, mode=sed


@dataclass
class LookupCommand(Command):
    table: str = ""
    input_fields: List[RenamePair] = field(default_factory=list)   # lookup_field AS event_field
    output_fields: List[RenamePair] = field(default_factory=list)
    output_mode: Optional[str] = None     # OUTPUT / OUTPUTNEW
    options: List[KeyValue] = field(default_factory=list)


@dataclass
class DedupCommand(Command):
    fields: List[Expr] = field(default_factory=list)
    count: Optional[int] = None
    options: List[KeyValue] = field(default_factory=list)
    sort_by: List[SortKey] = field(default_factory=list)


@dataclass
class TopCommand(Command):
    """top / rare."""
    fields: List[Expr] = field(default_factory=list)
    by: Optional[ByClause] = None
    options: List[KeyValue] = field(default_factory=list)


@dataclass
class Pipeline(Node):
    commands: List[Command] = field(default_factory=list)


@dataclass
class Query(Node):
    pipeline: Pipeline
    source: str = field(default="", repr=False, compare=False)

    @property
    def commands(self) -> List[Command]:
        return self.pipeline.commands
