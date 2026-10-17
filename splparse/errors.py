"""Exceptions raised by splparse."""
from __future__ import annotations

from typing import Optional


class SPLError(Exception):
    """Base class for all splparse errors."""


class SPLSyntaxError(SPLError):
    """Raised when the source cannot be tokenized or parsed."""

    def __init__(self, message: str, position=None, source: Optional[str] = None):
        self.message = message
        self.position = position
        self.source = source
        super().__init__(self.__str__())

    def __str__(self) -> str:
        if self.position is None:
            return self.message
        msg = f"{self.message} (line {self.position.line}, column {self.position.column})"
        if self.source:
            line = self.source.splitlines()[self.position.line - 1] if self.source.splitlines() else ""
            caret = " " * (self.position.column - 1) + "^"
            msg += f"\n  {line}\n  {caret}"
        return msg
