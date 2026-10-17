"""Package init."""
from __future__ import annotations

from .errors import SPLError, SPLSyntaxError
from .parser import parse, parse_expression
from .tokens import tokenize
from .printer import format as format, to_spl
from .lint import lint, Diagnostic
from . import nodes, analysis

__version__ = "0.1.0"
__all__ = [
    "parse", "parse_expression", "tokenize", "format", "to_spl", "lint",
    "Diagnostic", "SPLError", "SPLSyntaxError", "nodes", "analysis", "__version__",
]
