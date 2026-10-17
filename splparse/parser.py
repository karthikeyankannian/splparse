"""Recursive-descent / Pratt parser for SPL."""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Set

from . import nodes as N
from .errors import SPLSyntaxError
from .registry import KNOWN_COMMANDS, STATS_FAMILY
from .tokens import Lexer, Token, TokenKind as K

_END_KINDS = (K.PIPE, K.RBRACKET, K.EOF)

_CMP_OPS: Dict[K, str] = {
    K.EQ: "=", K.EQEQ: "==", K.NEQ: "!=", K.LT: "<", K.GT: ">", K.LE: "<=", K.GE: ">=",
}
_ARITH_OPS: Dict[K, str] = {
    K.PLUS: "+", K.MINUS: "-", K.STAR: "*", K.SLASH: "/", K.PERCENT: "%", K.DOT: ".",
}

# Precedence table for eval/where expressions (higher binds tighter).
PREC = {
    "OR": 1, "XOR": 1,
    "AND": 2,
    "=": 4, "==": 4, "!=": 4, "<": 4, ">": 4, "<=": 4, ">=": 4, "LIKE": 4,
    ".": 5,
    "+": 6, "-": 6,
    "*": 7, "/": 7, "%": 7,
}
PREC_NOT = 3
PREC_UNARY = 8


class Parser:
    def __init__(self, source: str):
        self.src = source
        self.toks: List[Token] = Lexer(source).tokenize()
        self.i = 0

    # ----------------------------------------------------------------- cursor
    @property
    def cur(self) -> Token:
        return self.toks[self.i]

    def peek(self, k: int = 1) -> Token:
        j = min(self.i + k, len(self.toks) - 1)
        return self.toks[j]

    def advance(self) -> Token:
        t = self.toks[self.i]
        if t.kind is not K.EOF:
            self.i += 1
        return t

    def at(self, *kinds: K) -> bool:
        return self.cur.kind in kinds

    def at_word(self, *names: str, ci: bool = True) -> bool:
        return self.cur.is_word(*names, ci=ci)

    def at_end(self) -> bool:
        return self.cur.kind in _END_KINDS

    def expect(self, kind: K, what: Optional[str] = None) -> Token:
        if self.cur.kind is not kind:
            self.error(f"Expected {what or kind.name.lower()}, found {self._describe(self.cur)}")
        return self.advance()

    def error(self, msg: str, tok: Optional[Token] = None) -> None:
        tok = tok or self.cur
        raise SPLSyntaxError(msg, tok.start, self.src)

    @staticmethod
    def _describe(t: Token) -> str:
        return "end of input" if t.kind is K.EOF else repr(t.text)

    def _adjacent(self, a: Token, b: Token) -> bool:
        return a.end.offset == b.start.offset

    def _span(self, start: Token, end_tok: Optional[Token] = None) -> N.Span:
        end_tok = end_tok or self.toks[max(self.i - 1, 0)]
        return (start.start.offset, end_tok.end.offset)

    # ------------------------------------------------------------------- top
    def parse(self) -> N.Query:
        pipeline = self.parse_pipeline()
        if not self.at(K.EOF):
            self.error(f"Unexpected {self._describe(self.cur)}")
        return N.Query(pipeline=pipeline, source=self.src, span=(0, len(self.src)))

    def parse_pipeline(self, in_subsearch: bool = False) -> N.Pipeline:
        start = self.cur
        commands: List[N.Command] = []
        leading_pipe = self.at(K.PIPE)
        if leading_pipe:
            self.advance()
        first = True
        while not self.at(K.EOF, K.RBRACKET):
            if self.at(K.PIPE):
                self.error("Empty command before '|'")
            implicit = first and not leading_pipe
            if implicit and in_subsearch and self._looks_like_command():
                implicit = False   # [inputlookup x], [eval ...] inside foreach, appendpipe [stats ...]
            commands.append(self.parse_command(implicit_search=implicit))
            first = False
            if self.at(K.PIPE):
                self.advance()
                if self.at(K.EOF, K.RBRACKET):
                    self.error("Expected a command after '|'")
                continue
            break
        return N.Pipeline(commands=commands, span=self._span(start))

    def _looks_like_command(self) -> bool:
        t = self.cur
        if t.kind is K.MACRO:
            return True
        if t.kind is not K.WORD or t.value.lower() not in KNOWN_COMMANDS:
            return False
        nxt = self.peek().kind
        return nxt not in _CMP_OPS and nxt is not K.LPAREN

    # -------------------------------------------------------------- commands
    def parse_command(self, implicit_search: bool = False) -> N.Command:
        start = self.cur
        if implicit_search:
            expr = self._search_expr()
            return self._finish(N.SearchCommand(name="search", expr=expr, implicit=True), start)

        if self.at(K.MACRO):
            macro = self._macro()
            args: List[N.Node] = []
            while not self.at_end():
                args.append(N.Positional(value=self._value()))
            return self._finish(N.MacroCommand(name=macro.name, macro=macro, args=args), start)
        if not self.at(K.WORD):
            self.error(f"Expected a command name, found {self._describe(self.cur)}")
        name_tok = self.advance()
        name = name_tok.value.lower()

        handler: Callable[[str, Token], N.Command] = self._handlers().get(name, self._generic)
        if name in STATS_FAMILY:
            handler = self._stats
        return handler(name, start)

    def _handlers(self) -> Dict[str, Callable[[str, Token], N.Command]]:
        return {
            "search": self._search,
            "where": self._where,
            "eval": self._eval,
            "fields": self._fields,
            "table": self._table,
            "rename": self._rename,
            "sort": self._sort,
            "head": self._head,
            "rex": self._rex,
            "lookup": self._lookup,
            "dedup": self._dedup,
            "top": self._top,
            "rare": self._top,
        }

    def _finish(self, cmd: N.Command, start: Token) -> N.Command:
        cmd.span = self._span(start)
        cmd.raw = self.src[cmd.span[0]:cmd.span[1]]
        return cmd

    def _search(self, name: str, start: Token) -> N.Command:
        expr = None if self.at_end() else self._search_expr()
        return self._finish(N.SearchCommand(name=name, expr=expr), start)

    def _where(self, name: str, start: Token) -> N.Command:
        if self.at_end():
            self.error("where requires an expression")
        return self._finish(N.WhereCommand(name=name, expr=self.parse_expr()), start)

    def _eval(self, name: str, start: Token) -> N.Command:
        assigns: List[N.Assignment] = []
        while True:
            tstart = self.cur
            if not self.at(K.WORD, K.QFIELD, K.STRING):
                self.error(f"Expected a field name to assign, found {self._describe(self.cur)}")
            target = self.advance().value
            self.expect(K.EQ, "'='")
            value = self.parse_expr()
            assigns.append(N.Assignment(target=target, value=value, span=self._span(tstart)))
            if self.at(K.COMMA):
                self.advance()
                continue
            break
        if not self.at_end():
            self.error(f"Unexpected {self._describe(self.cur)} in eval")
        return self._finish(N.EvalCommand(name=name, assignments=assigns), start)

    def _stats(self, name: str, start: Token) -> N.Command:
        cmd = N.StatsCommand(name=name)
        while not self.at_end():
            if self.at_word("by", "groupby"):
                self.advance()
                cmd.by = self._by_clause(cmd.options)
            elif self.at_word("where") and name in ("tstats", "mstats"):
                self.advance()
                cmd.where = self._search_expr(stop_words={"by", "groupby", "from"})
            elif self.at_word("from") and name in ("tstats", "mstats"):
                self.advance()
                kv = self._key_value()
                cmd.from_ = _value_text(kv.value)
                cmd.options.append(kv)
            elif self.at_word("over") and name in ("chart", "sichart"):
                self.advance()
                cmd.options.append(N.KeyValue(key="over", value=self._field_name()))
            elif self._at_key_value():
                cmd.options.append(self._key_value())
            elif self.at(K.COMMA):
                self.advance()
            else:
                astart = self.cur
                func = self._primary()
                alias = self._optional_alias()
                cmd.aggregations.append(N.Aggregation(func=func, alias=alias, span=self._span(astart)))
        return self._finish(cmd, start)

    def _by_clause(self, options: List[N.KeyValue]) -> N.ByClause:
        start = self.cur
        fields: List[N.Expr] = []
        while not self.at_end():
            if self._at_key_value():
                options.append(self._key_value())
            elif self.at(K.COMMA):
                self.advance()
            elif self.at(K.WORD, K.QFIELD, K.STRING):
                fields.append(self._field_name())
            else:
                break
        if not fields:
            self.error("BY clause requires at least one field")
        return N.ByClause(fields=fields, span=self._span(start))

    def _fields(self, name: str, start: Token) -> N.Command:
        remove = False
        if self.at(K.MINUS):
            self.advance()
            remove = True
        elif self.at(K.PLUS):
            self.advance()
        elif self.at(K.WORD) and self.cur.value.startswith("-"):
            remove = True
            self._split_leading_sign()
        elif self.at(K.WORD) and self.cur.value.startswith("+"):
            self._split_leading_sign()
        return self._finish(N.FieldsCommand(name=name, fields=self._field_list(), remove=remove), start)

    def _split_leading_sign(self) -> None:
        """Turn the WORD token ``-foo`` into ``foo`` in place (sign already consumed)."""
        t = self.cur
        self.toks[self.i] = Token(t.kind, t.text[1:], t.value[1:],
                                  t.start.__class__(t.start.offset + 1, t.start.line, t.start.column + 1), t.end)

    def _table(self, name: str, start: Token) -> N.Command:
        return self._finish(N.TableCommand(name=name, fields=self._field_list()), start)

    def _rename(self, name: str, start: Token) -> N.Command:
        pairs: List[N.RenamePair] = []
        while not self.at_end():
            pstart = self.cur
            src = self._field_name()
            if not self.at_word("as"):
                self.error("rename requires 'AS'")
            self.advance()
            target = self._name_token().value
            pairs.append(N.RenamePair(source=src, target=target, span=self._span(pstart)))
            if self.at(K.COMMA):
                self.advance()
        return self._finish(N.RenameCommand(name=name, pairs=pairs), start)

    def _sort(self, name: str, start: Token) -> N.Command:
        cmd = N.SortCommand(name=name)
        if self.at(K.NUMBER) and not self.cur.text.startswith(("-", "+")):
            cmd.limit = int(float(self.advance().value))
        while not self.at_end():
            if self._at_key_value():
                cmd.options.append(self._key_value())
                continue
            if self.at(K.COMMA):
                self.advance()
                continue
            cmd.keys.append(self._sort_key())
        return self._finish(cmd, start)

    def _sort_key(self) -> N.SortKey:
        start = self.cur
        desc = False
        if self.at(K.MINUS):
            self.advance()
            desc = True
        elif self.at(K.PLUS):
            self.advance()
        elif self.at(K.WORD, K.NUMBER) and self.cur.text.startswith("-"):
            desc = True
            self._split_leading_sign()
        elif self.at(K.WORD, K.NUMBER) and self.cur.text.startswith("+"):
            self._split_leading_sign()
        func = None
        if self.at(K.WORD) and self.cur.value.lower() in ("auto", "str", "ip", "num") \
                and self.peek().kind is K.LPAREN:
            func = self.advance().value.lower()
            self.advance()
            fld = self._field_name()
            self.expect(K.RPAREN, "')'")
        else:
            fld = self._field_name()
        if self.at_word("desc", "d"):
            self.advance()
            desc = True
        elif self.at_word("asc", "a"):
            self.advance()
        return N.SortKey(field=fld, descending=desc, func=func, span=self._span(start))

    def _head(self, name: str, start: Token) -> N.Command:
        cmd = N.HeadCommand(name=name)
        while not self.at_end():
            if self.at(K.NUMBER) and cmd.count is None and cmd.condition is None:
                cmd.count = int(float(self.advance().value))
            elif self._at_key_value():
                cmd.options.append(self._key_value())
            elif cmd.condition is None:
                cmd.condition = self.parse_expr()
            else:
                self.error(f"Unexpected {self._describe(self.cur)} in head")
        return self._finish(cmd, start)

    def _rex(self, name: str, start: Token) -> N.Command:
        cmd = N.RexCommand(name=name)
        while not self.at_end():
            if self._at_key_value():
                cmd.options.append(self._key_value())
            elif self.at(K.STRING) and cmd.pattern is None:
                cmd.pattern = self.advance().value
            else:
                self.error(f"Unexpected {self._describe(self.cur)} in rex")
        if cmd.pattern is None:
            self.error("rex requires a quoted regular expression")
        return self._finish(cmd, start)

    def _lookup(self, name: str, start: Token) -> N.Command:
        cmd = N.LookupCommand(name=name)
        while self._at_key_value():
            cmd.options.append(self._key_value())
        cmd.table = self._name_token().value
        target = cmd.input_fields
        while not self.at_end():
            if self.at_word("output", "outputnew"):
                cmd.output_mode = self.advance().value.upper()
                target = cmd.output_fields
                continue
            if self.at(K.COMMA):
                self.advance()
                continue
            pstart = self.cur
            src = self._field_name()
            alias = self._optional_alias()
            target.append(N.RenamePair(source=src, target=alias or _value_text(src), span=self._span(pstart)))
        return self._finish(cmd, start)

    def _dedup(self, name: str, start: Token) -> N.Command:
        cmd = N.DedupCommand(name=name)
        if self.at(K.NUMBER):
            cmd.count = int(float(self.advance().value))
        while not self.at_end():
            if self.at_word("sortby"):
                self.advance()
                while not self.at_end() and not self._at_key_value():
                    if self.at(K.COMMA):
                        self.advance()
                        continue
                    cmd.sort_by.append(self._sort_key())
            elif self._at_key_value():
                cmd.options.append(self._key_value())
            elif self.at(K.COMMA):
                self.advance()
            else:
                cmd.fields.append(self._field_name())
        return self._finish(cmd, start)

    def _top(self, name: str, start: Token) -> N.Command:
        cmd = N.TopCommand(name=name)
        if self.at(K.NUMBER):
            t = self.advance()
            cmd.options.append(N.KeyValue(key="limit", value=N.Literal(float(t.value), "number")))
        while not self.at_end():
            if self.at_word("by"):
                self.advance()
                cmd.by = self._by_clause(cmd.options)
            elif self._at_key_value():
                cmd.options.append(self._key_value())
            elif self.at(K.COMMA):
                self.advance()
            else:
                cmd.fields.append(self._field_name())
        return self._finish(cmd, start)

    def _generic(self, name: str, start: Token) -> N.Command:
        args: List[N.Node] = []
        while not self.at_end():
            if self.at(K.COMMA):
                self.advance()
            elif self.at(K.LBRACKET):
                args.append(N.Positional(value=self._subsearch()))
            elif self.at_word("by"):
                self.advance()
                opts: List[N.KeyValue] = []
                by = self._by_clause(opts)
                args.append(by)
                args.extend(opts)
            elif self._at_key_value():
                args.append(self._key_value())
            else:
                pstart = self.cur
                expr = self.parse_expr(PREC["="])  # stop at comparison level: keep kv-ish things apart
                alias = self._optional_alias()
                args.append(N.Positional(value=expr, alias=alias, span=self._span(pstart)))
        return self._finish(N.GenericCommand(name=name, args=args), start)

    # ------------------------------------------------------------- helpers
    def _at_key_value(self) -> bool:
        return self.at(K.WORD, K.QFIELD, K.STRING) and self.peek().kind is K.EQ

    def _key_value(self) -> N.KeyValue:
        start = self.cur
        key = self.advance().value
        self.expect(K.EQ, "'='")
        value = self._value()
        return N.KeyValue(key=key, value=value, span=self._span(start))

    def _value(self) -> N.Expr:
        """A command option value: literal, field, parenthesised expr or subsearch."""
        t = self.cur
        if t.kind is K.STRING:
            self.advance()
            return N.Literal(t.value, "string", span=self._span(t))
        if t.kind is K.NUMBER:
            self.advance()
            return N.Literal(_num(t.value), "number", span=self._span(t))
        if t.kind in (K.WORD, K.QFIELD):
            self.advance()
            return N.Literal(t.value, "term", span=self._span(t))
        if t.kind is K.LPAREN:
            self.advance()
            e = self.parse_expr()
            self.expect(K.RPAREN, "')'")
            return e
        if t.kind is K.LBRACKET:
            return self._subsearch()
        if t.kind is K.MACRO:
            return self._macro()
        self.error(f"Expected a value, found {self._describe(t)}")
        raise AssertionError  # pragma: no cover

    def _name_token(self) -> Token:
        if not self.at(K.WORD, K.QFIELD, K.STRING, K.NUMBER):
            self.error(f"Expected a name, found {self._describe(self.cur)}")
        return self.advance()

    def _field_name(self) -> N.Expr:
        t = self._name_token()
        if t.kind is K.STRING:
            return N.Field(t.value, quoted=True, span=self._span(t))
        return N.Field(t.value, quoted=(t.kind is K.QFIELD), span=self._span(t))

    def _field_list(self) -> List[N.Expr]:
        fields: List[N.Expr] = []
        while not self.at_end():
            if self.at(K.COMMA):
                self.advance()
                continue
            fields.append(self._field_name())
        return fields

    def _optional_alias(self) -> Optional[str]:
        if self.at_word("as"):
            self.advance()
            return self._name_token().value
        return None

    def _subsearch(self) -> N.Subsearch:
        start = self.expect(K.LBRACKET, "'['")
        pipeline = self.parse_pipeline(in_subsearch=True)
        self.expect(K.RBRACKET, "']'")
        return N.Subsearch(pipeline=pipeline, span=self._span(start))

    def _macro(self) -> N.MacroCall:
        t = self.expect(K.MACRO, "macro")
        body = t.value
        name, args = body, []
        if "(" in body and body.endswith(")"):
            name = body[:body.index("(")]
            inner = body[body.index("(") + 1:-1]
            args = [a.strip() for a in _split_top_level(inner)] if inner.strip() else []
        return N.MacroCall(name=name.strip(), args=args, raw=t.text, span=self._span(t))

    # ------------------------------------------------------ search grammar
    def _search_expr(self, stop_words: Optional[Set[str]] = None) -> N.Expr:
        self._stop = {w.lower() for w in (stop_words or set())}
        try:
            return self._s_or()
        finally:
            self._stop = set()

    def _s_term_start(self) -> bool:
        t = self.cur
        if t.kind in (K.STRING, K.NUMBER, K.QFIELD, K.MACRO, K.LPAREN, K.LBRACKET, K.STAR):
            return True
        if t.kind is K.WORD:
            if t.value in ("AND", "OR"):
                return False
            if t.value.lower() in getattr(self, "_stop", set()):
                return False
            return True
        return False

    def _s_or(self) -> N.Expr:
        left = self._s_and()
        while self.at_word("OR", ci=False):
            self.advance()
            right = self._s_and()
            left = N.Binary("OR", left, right, span=_join(left, right))
        return left

    def _s_and(self) -> N.Expr:
        left = self._s_not()
        while True:
            if self.at_word("AND", ci=False):
                self.advance()
                implicit = False
            elif self._s_term_start():
                implicit = True
            else:
                break
            right = self._s_not()
            left = N.Binary("AND", left, right, implicit=implicit, span=_join(left, right))
        return left

    def _s_not(self) -> N.Expr:
        if self.at_word("NOT", ci=False):
            t = self.advance()
            operand = self._s_not()
            return N.Unary("NOT", operand, span=(t.start.offset, operand.span[1] if operand.span else t.end.offset))
        return self._s_primary()

    def _s_primary(self) -> N.Expr:
        t = self.cur
        if t.kind is K.LPAREN:
            self.advance()
            e = self._s_or()
            self.expect(K.RPAREN, "')'")
            return e
        if t.kind is K.LBRACKET:
            return self._subsearch()
        if t.kind is K.MACRO:
            return self._macro()
        if t.kind is K.STRING:
            self.advance()
            return N.Literal(t.value, "string", span=self._span(t))
        if t.kind is K.NUMBER:
            self.advance()
            return N.Literal(_num(t.value), "number", span=self._span(t))
        if t.kind is K.STAR:
            self.advance()
            return N.Literal("*", "term", span=self._span(t))
        if t.kind in (K.WORD, K.QFIELD):
            self.advance()
            nxt = self.cur
            if nxt.kind in _CMP_OPS:
                self.advance()
                value = self._s_value()
                fld = N.Field(t.value, quoted=(t.kind is K.QFIELD), span=self._span(t, t))
                return N.Binary(_CMP_OPS[nxt.kind], fld, value, span=(t.start.offset, value.span[1]))
            if nxt.is_word("IN") and self.peek().kind is K.LPAREN:
                self.advance()
                self.advance()
                values: List[N.Expr] = []
                while not self.at(K.RPAREN):
                    if self.at(K.COMMA):
                        self.advance()
                        continue
                    if self.at(K.EOF):
                        self.error("Unterminated IN (...) list")
                    values.append(self._s_value())
                end = self.advance()
                fld = N.Field(t.value, quoted=(t.kind is K.QFIELD), span=self._span(t, t))
                return N.InExpr(fld, values, span=(t.start.offset, end.end.offset))
            if nxt.kind is K.LPAREN and self._adjacent(t, nxt) and t.kind is K.WORD:
                # TERM(...), CASE(...) and friends
                self.advance()
                depth, parts = 1, []
                while depth:
                    if self.at(K.EOF):
                        self.error(f"Unterminated {t.value}(...)")
                    tok = self.advance()
                    if tok.kind is K.LPAREN:
                        depth += 1
                    elif tok.kind is K.RPAREN:
                        depth -= 1
                        if depth == 0:
                            break
                    parts.append(tok)
                raw = self.src[parts[0].start.offset:parts[-1].end.offset] if parts else ""
                return N.FunctionCall(t.value, [N.Literal(raw, "term")], span=self._span(t))
            return N.Literal(t.value, "term", span=self._span(t, t))
        self.error(f"Unexpected {self._describe(t)} in search expression")
        raise AssertionError  # pragma: no cover

    def _s_value(self) -> N.Expr:
        t = self.cur
        if t.kind is K.STRING:
            self.advance()
            return N.Literal(t.value, "string", span=self._span(t))
        if t.kind is K.NUMBER:
            self.advance()
            return N.Literal(_num(t.value), "number", span=self._span(t))
        if t.kind in (K.WORD, K.QFIELD):
            self.advance()
            return N.Literal(t.value, "term", span=self._span(t))
        if t.kind is K.LBRACKET:
            return self._subsearch()
        if t.kind is K.MACRO:
            return self._macro()
        if t.kind is K.STAR:
            self.advance()
            return N.Literal("*", "term", span=self._span(t))
        self.error(f"Expected a value, found {self._describe(t)}")
        raise AssertionError  # pragma: no cover

    # --------------------------------------------------- eval/where grammar
    def parse_expr(self, min_prec: int = 0) -> N.Expr:
        left = self._unary()
        while True:
            op, prec, consume = self._binop()
            if op is None or prec < min_prec:
                break
            if op == "IN":
                left = self._in_tail(left, negated=False)
                continue
            if consume:
                self.advance()
                right = self.parse_expr(prec + 1)
            else:
                # signed number glued to the previous operand, e.g. "x -1"
                t = self.advance()
                right = N.Literal(_num(t.value[1:]), "number", span=self._span(t))
            left = N.Binary(op, left, right, span=_join(left, right))
        return left

    def _binop(self):
        t = self.cur
        if t.kind in _CMP_OPS:
            return _CMP_OPS[t.kind], PREC[_CMP_OPS[t.kind]], True
        if t.kind in _ARITH_OPS:
            return _ARITH_OPS[t.kind], PREC[_ARITH_OPS[t.kind]], True
        if t.kind is K.NUMBER and t.text[0] in "-+" and len(t.text) > 1:
            op = t.text[0]
            return op, PREC[op], False
        if t.kind is K.WORD:
            if t.value in ("AND", "OR", "XOR") or t.value.lower() in ("and", "or", "xor"):
                op = t.value.upper()
                return op, PREC[op], True
            if t.value.lower() == "like":
                return "LIKE", PREC["LIKE"], True
            if t.value.upper() == "IN" and self.peek().kind is K.LPAREN:
                return "IN", PREC["="], True
        return None, -1, False

    def _in_tail(self, subject: N.Expr, negated: bool) -> N.Expr:
        self.advance()  # IN
        self.expect(K.LPAREN, "'('")
        values: List[N.Expr] = []
        while not self.at(K.RPAREN):
            if self.at(K.COMMA):
                self.advance()
                continue
            if self.at(K.EOF):
                self.error("Unterminated IN (...) list")
            values.append(self.parse_expr())
        end = self.advance()
        return N.InExpr(subject, values, negated=negated,
                        span=(subject.span[0] if subject.span else end.start.offset, end.end.offset))

    def _unary(self) -> N.Expr:
        t = self.cur
        if t.kind is K.BANG or (t.kind is K.WORD and t.value.upper() == "NOT"):
            self.advance()
            operand = self.parse_expr(PREC_NOT)
            return N.Unary("NOT", operand, span=(t.start.offset, operand.span[1] if operand.span else t.end.offset))
        if t.kind is K.MINUS:
            self.advance()
            operand = self.parse_expr(PREC_UNARY)
            return N.Unary("-", operand, span=(t.start.offset, operand.span[1] if operand.span else t.end.offset))
        if t.kind is K.PLUS:
            self.advance()
            return self.parse_expr(PREC_UNARY)
        return self._primary()

    def _primary(self) -> N.Expr:
        t = self.cur
        if t.kind is K.NUMBER:
            self.advance()
            return N.Literal(_num(t.value), "number", span=self._span(t))
        if t.kind is K.STRING:
            self.advance()
            return N.Literal(t.value, "string", span=self._span(t))
        if t.kind is K.QFIELD:
            self.advance()
            return N.Field(t.value, quoted=True, span=self._span(t))
        if t.kind is K.LPAREN:
            self.advance()
            e = self.parse_expr()
            self.expect(K.RPAREN, "')'")
            return e
        if t.kind is K.LBRACKET:
            return self._subsearch()
        if t.kind is K.MACRO:
            return self._macro()
        if t.kind is K.STAR:
            self.advance()
            return N.Field("*", span=self._span(t))
        if t.kind is K.WORD:
            self.advance()
            nxt = self.cur
            if nxt.kind is K.LPAREN and self._adjacent(t, nxt):
                self.advance()
                args: List[N.Expr] = []
                while not self.at(K.RPAREN):
                    if self.at(K.COMMA):
                        self.advance()
                        continue
                    if self.at(K.EOF):
                        self.error(f"Unterminated call to {t.value}()")
                    args.append(self.parse_expr())
                end = self.advance()
                return N.FunctionCall(t.value, args, span=(t.start.offset, end.end.offset))
            return self._word_as_expr(t)
        self.error(f"Unexpected {self._describe(t)} in expression")
        raise AssertionError  # pragma: no cover

    def _word_as_expr(self, t: Token) -> N.Expr:
        """A bare word in eval context.

        The lexer keeps ``a-b``, ``x*2`` and ``-x`` as single words because
        they are single terms in ``search``; in an eval/where expression they
        are arithmetic, so re-split them here (respecting precedence).
        """
        text = t.value
        if "@" in text or text == "*":
            return N.Field(text, span=self._span(t))
        expr = _resplit_arith(text)
        if expr is None:
            return N.Field(text, span=self._span(t))
        expr.span = self._span(t)
        return expr


# ------------------------------------------------------------------- utilities
def _num(s: str):
    f = float(s)
    return int(f) if f.is_integer() and "." not in s and "e" not in s.lower() else f


def _leaf(part: str) -> N.Expr:
    try:
        return N.Literal(_num(part), "number")
    except ValueError:
        return N.Field(part)


def _split_on(text: str, chars: str):
    parts, ops, buf = [], [], ""
    for ch in text:
        if ch in chars and buf:
            parts.append(buf)
            ops.append(ch)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return parts, ops


def _resplit_arith(text: str) -> Optional[N.Expr]:
    """``x*2+1`` -> (x * 2) + 1 ; ``-x`` -> -(x). None if nothing to split."""
    if not any(c in text for c in "-+*"):
        return None
    sign = None
    if text[0] in "-+":
        sign, text = text[0], text[1:]
        if not text:
            return None
    add_parts, add_ops = _split_on(text, "-+")
    terms: List[N.Expr] = []
    for part in add_parts:
        mul_parts, mul_ops = _split_on(part, "*")
        if not all(mul_parts):
            return None  # e.g. trailing wildcard "bytes*"
        e: N.Expr = _leaf(mul_parts[0])
        for op, p in zip(mul_ops, mul_parts[1:]):
            e = N.Binary(op, e, _leaf(p))
        terms.append(e)
    expr = terms[0]
    for op, e in zip(add_ops, terms[1:]):
        expr = N.Binary(op, expr, e)
    if sign == "-":
        expr = N.Unary("-", expr)
    if sign is None and len(terms) == 1 and not add_ops and isinstance(expr, N.Field):
        return None
    return expr


def _split_top_level(s: str) -> List[str]:
    out, depth, buf, quote = [], 0, "", False
    for ch in s:
        if ch == '"':
            quote = not quote
        if not quote:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            elif ch == "," and depth == 0:
                out.append(buf)
                buf = ""
                continue
        buf += ch
    out.append(buf)
    return out


def _join(a: N.Node, b: N.Node) -> Optional[N.Span]:
    if a.span and b.span:
        return (a.span[0], b.span[1])
    return None


def _value_text(e: N.Expr) -> str:
    if isinstance(e, N.Literal):
        return str(e.value)
    if isinstance(e, N.Field):
        return e.name
    return ""


def parse(source: str) -> N.Query:
    """Parse an SPL search string into a :class:`splparse.nodes.Query`."""
    return Parser(source).parse()


def parse_expression(source: str) -> N.Expr:
    """Parse a standalone eval/where expression."""
    p = Parser(source)
    e = p.parse_expr()
    if not p.at(K.EOF):
        p.error(f"Unexpected {p._describe(p.cur)}")
    return e
