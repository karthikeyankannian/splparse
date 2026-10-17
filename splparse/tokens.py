"""Tokenizer for Splunk's Search Processing Language (SPL)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Iterator, List

from .errors import SPLSyntaxError


class TokenKind(Enum):
    WORD = auto()      # bare word: field name, command name, wildcard term, time modifier
    NUMBER = auto()    # 42, 3.14, -7, 1e3
    STRING = auto()    # "double quoted string"
    QFIELD = auto()    # 'single quoted field name'
    MACRO = auto()     # `macro_name(arg1, arg2)`
    PIPE = auto()
    LPAREN = auto()
    RPAREN = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    COMMA = auto()
    EQ = auto()        # =
    EQEQ = auto()      # ==
    NEQ = auto()       # !=
    LT = auto()
    GT = auto()
    LE = auto()
    GE = auto()
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    PERCENT = auto()
    DOT = auto()       # standalone "." (eval concatenation)
    BANG = auto()      # standalone "!"
    EOF = auto()


@dataclass(frozen=True)
class Position:
    offset: int
    line: int
    column: int

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.line}:{self.column}"


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    text: str          # raw source text
    value: str         # decoded value (strings un-escaped, macros stripped of backticks)
    start: Position
    end: Position

    def is_word(self, *names: str, ci: bool = True) -> bool:
        if self.kind is not TokenKind.WORD:
            return False
        return (self.value.lower() in [n.lower() for n in names]) if ci else self.value in names

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Token({self.kind.name}, {self.text!r} @{self.start})"


# Characters that may appear inside a bare word once it has started.
_WORD_BODY = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:*@$#{}?~\\")
# Characters after which a leading "-", "+" or "/" is glued onto a word
# (so that earliest=-24h@h, sort -count and source=/var/log/* tokenize sensibly).
_WORD_START_CONTEXT = set(" \t\r\n=(,[")

_SIMPLE = {
    "|": TokenKind.PIPE,
    "(": TokenKind.LPAREN,
    ")": TokenKind.RPAREN,
    "[": TokenKind.LBRACKET,
    "]": TokenKind.RBRACKET,
    ",": TokenKind.COMMA,
    "+": TokenKind.PLUS,
    "-": TokenKind.MINUS,
    "*": TokenKind.STAR,
    "/": TokenKind.SLASH,
    "%": TokenKind.PERCENT,
    ".": TokenKind.DOT,
}


class Lexer:
    """Converts SPL source text into a list of :class:`Token` objects.

    Design notes
    ------------
    SPL is not context free at the lexical level: ``a-b`` is a single search
    term in ``search`` but a subtraction in ``eval``.  The lexer keeps words
    greedy (favouring search semantics) and the expression parser re-splits
    words containing ``-`` when it sees them in an arithmetic context.
    """

    def __init__(self, source: str):
        self.src = source
        self.pos = 0
        self.line = 1
        self.col = 1

    # ------------------------------------------------------------------ utils
    def _peek(self, k: int = 0) -> str:
        i = self.pos + k
        return self.src[i] if i < len(self.src) else ""

    def _advance(self, n: int = 1) -> str:
        s = self.src[self.pos:self.pos + n]
        for ch in s:
            if ch == "\n":
                self.line += 1
                self.col = 1
            else:
                self.col += 1
        self.pos += n
        return s

    def _position(self) -> Position:
        return Position(self.pos, self.line, self.col)

    def _prev_char(self) -> str:
        return self.src[self.pos - 1] if self.pos > 0 else ""

    def _is_word_char(self, ch: str) -> bool:
        return bool(ch) and (ch in _WORD_BODY or ch.isalpha())

    # ------------------------------------------------------------------ main
    def tokenize(self) -> List[Token]:
        return list(self)

    def __iter__(self) -> Iterator[Token]:
        while True:
            self._skip_ws_and_comments()
            start = self._position()
            ch = self._peek()
            if ch == "":
                yield Token(TokenKind.EOF, "", "", start, start)
                return

            if ch == '"':
                yield self._string(start)
            elif ch == "'":
                yield self._qfield(start)
            elif ch == "`":
                yield self._macro(start)
            elif ch == "=":
                if self._peek(1) == "=":
                    self._advance(2)
                    yield Token(TokenKind.EQEQ, "==", "==", start, self._position())
                else:
                    self._advance()
                    yield Token(TokenKind.EQ, "=", "=", start, self._position())
            elif ch == "!":
                if self._peek(1) == "=":
                    self._advance(2)
                    yield Token(TokenKind.NEQ, "!=", "!=", start, self._position())
                else:
                    self._advance()
                    yield Token(TokenKind.BANG, "!", "!", start, self._position())
            elif ch == "<" and self._peek(1) == "<":
                yield self._word(start)          # foreach template: <<FIELD>>
            elif ch == "<":
                if self._peek(1) == "=":
                    self._advance(2)
                    yield Token(TokenKind.LE, "<=", "<=", start, self._position())
                else:
                    self._advance()
                    yield Token(TokenKind.LT, "<", "<", start, self._position())
            elif ch == ">":
                if self._peek(1) == "=":
                    self._advance(2)
                    yield Token(TokenKind.GE, ">=", ">=", start, self._position())
                else:
                    self._advance()
                    yield Token(TokenKind.GT, ">", ">", start, self._position())
            elif ch in "-+/" and self._word_leader_context() and (
                self._is_word_char(self._peek(1)) or (ch == "/" and self._peek(1) in "/*")
            ):
                yield self._word(start)
            elif ch == "*" and self._is_word_char(self._peek(1)) and self._word_leader_context():
                yield self._word(start)          # leading wildcard: *error
            elif ch in _SIMPLE:
                self._advance()
                yield Token(_SIMPLE[ch], ch, ch, start, self._position())
            elif self._is_word_char(ch):
                yield self._word(start)
            else:
                raise SPLSyntaxError(f"Unexpected character {ch!r}", start, self.src)

    def _word_leader_context(self) -> bool:
        prev = self._prev_char()
        return prev == "" or prev in _WORD_START_CONTEXT

    # ------------------------------------------------------------------ pieces
    def _skip_ws_and_comments(self) -> None:
        while True:
            ch = self._peek()
            if ch and ch in " \t\r\n":
                self._advance()
            elif self.src.startswith("```", self.pos):
                end = self.src.find("```", self.pos + 3)
                if end == -1:
                    raise SPLSyntaxError("Unterminated ``` comment", self._position(), self.src)
                self._advance(end + 3 - self.pos)
            else:
                return

    def _string(self, start: Position) -> Token:
        self._advance()  # opening quote
        out: List[str] = []
        while True:
            ch = self._peek()
            if ch == "":
                raise SPLSyntaxError("Unterminated string literal", start, self.src)
            if ch == "\\" and self._peek(1) in ('"', "\\"):
                out.append(self._peek(1))
                self._advance(2)
                continue
            if ch == "\\":
                out.append(ch)
                self._advance()
                continue
            if ch == '"':
                self._advance()
                break
            out.append(self._advance())
        end = self._position()
        return Token(TokenKind.STRING, self.src[start.offset:end.offset], "".join(out), start, end)

    def _qfield(self, start: Position) -> Token:
        self._advance()
        out: List[str] = []
        while True:
            ch = self._peek()
            if ch == "":
                raise SPLSyntaxError("Unterminated single-quoted field name", start, self.src)
            if ch == "'":
                self._advance()
                break
            out.append(self._advance())
        end = self._position()
        return Token(TokenKind.QFIELD, self.src[start.offset:end.offset], "".join(out), start, end)

    def _macro(self, start: Position) -> Token:
        self._advance()
        depth = 0
        out: List[str] = []
        while True:
            ch = self._peek()
            if ch == "":
                raise SPLSyntaxError("Unterminated macro invocation", start, self.src)
            if ch == "`" and depth == 0:
                self._advance()
                break
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            elif ch == '"':
                # skip quoted strings inside macro args wholesale
                out.append(self._advance())
                while self._peek() not in ('"', ""):
                    if self._peek() == "\\":
                        out.append(self._advance())
                    out.append(self._advance())
            out.append(self._advance())
        end = self._position()
        return Token(TokenKind.MACRO, self.src[start.offset:end.offset], "".join(out), start, end)

    def _word(self, start: Position) -> Token:
        first = self._advance()
        out = [first]
        if first == "<":            # <<FIELD>> template token
            out.append(self._advance())
            while self._peek() and not self.src.startswith(">>", self.pos):
                out.append(self._advance())
            if self.src.startswith(">>", self.pos):
                out.append(self._advance(2))
        while True:
            ch = self._peek()
            if ch == "<" and self._peek(1) == "<":
                end = self.src.find(">>", self.pos)
                if end == -1:
                    break
                out.append(self._advance(end + 2 - self.pos))
            elif self._is_word_char(ch):
                out.append(self._advance())
            elif ch in "-+" and self._is_word_char(self._peek(1)) and self._word_can_absorb_sign(out):
                out.append(self._advance())
            elif ch == "/" and first == "/" :
                out.append(self._advance())
            elif ch == "/" and self._peek(1) == "*" and "/" in out:
                out.append(self._advance())
            else:
                break
        text = "".join(out)
        end = self._position()
        kind = TokenKind.NUMBER if _is_number(text) else TokenKind.WORD
        return Token(kind, text, text, start, end)

    @staticmethod
    def _word_can_absorb_sign(out: List[str]) -> bool:
        """``foo-bar`` and ``-24h@h`` stay one word; ``x-1`` does not."""
        last = out[-1]
        if out[0] in "-+" or "@" in out:
            return True  # time modifier such as -1d@d or +2h
        return last.isalpha() or last == "_"


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def tokenize(source: str) -> List[Token]:
    """Tokenize *source* and return the token list (terminated by an EOF token)."""
    return Lexer(source).tokenize()
