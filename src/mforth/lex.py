"""mforth lexer.

Tokenizes `.fs` Forth source into a stream of `Token` values that carry full
(file, line, col) source locations. Used by both the host REPL and the mlog
AOT compiler; per the design, source locations propagate end-to-end so the
LSP can map diagnostics back to characters in the user's buffer.

The dialect is pragmatic Forth (no POSTPONE/IMMEDIATE/DOES>/EXECUTE), so
tokenization is whitespace-delimited with a few special cases the parser
needs to see explicitly:

* `:` and `;` are emitted as their own token kinds (COLON, SEMICOLON) when
  they appear as standalone whitespace-delimited words — otherwise they are
  part of a WORD.
* `( ... )` comments are nestable and discarded.
* `\\ ...\n` line comments are discarded.
* `." text"` and `S" text"` are recognised when `."` / `S"` appear as
  standalone whitespace-delimited words and consume content up to the
  matching `"`.

Anything else that survives the whitespace split becomes either a NUMBER
(if it parses as a decimal integer, optionally signed), a FLOAT (if it
parses as a decimal float per the bead mforth-xk7 regex
``^[-+]?\\d+\\.\\d+(?:[eE][-+]?\\d+)?$``), or a WORD.

The float regex requires at least one digit on EACH side of the decimal
point, so the Forth ``.`` (pop-and-print) word and bare decimals like
``3.`` / ``.5`` never get swallowed as floats — they fall through to
WORD. Whitespace splits ``3 . 14`` into three tokens (NUMBER, WORD,
NUMBER) already, so the lookahead concern from the bead's design notes
is handled by the whitespace-delimited tokenization discipline itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterator, Optional


# Float-literal regex (bead mforth-xk7).
#
# Requires:
#   * optional leading sign,
#   * at least one digit before the `.`,
#   * a `.`,
#   * at least one digit after the `.`,
#   * an optional scientific-notation exponent ``[eE][-+]?\d+``.
#
# Scientific notation is IN scope at claim time: cheap to support, mlog
# accepts the same form, and Python's ``float()`` constructor parses it
# verbatim. The "at least one digit on each side of the dot" rule keeps
# the Forth ``.`` word + bare decimals (``3.``, ``.5``) from being
# misclassified as floats.
_FLOAT_RE = re.compile(r"^[-+]?\d+\.\d+(?:[eE][-+]?\d+)?$")


class TokenKind(Enum):
    NUMBER = "NUMBER"
    FLOAT = "FLOAT"
    WORD = "WORD"
    COLON = "COLON"
    SEMICOLON = "SEMICOLON"
    # LPAREN / RPAREN / LINE_COMMENT are part of the grammar but the lexer
    # discards them — they are kept on the enum so downstream tooling can
    # refer to them by name when reporting structural errors about comments.
    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    LINE_COMMENT = "LINE_COMMENT"
    STRING_DOT_QUOTE = "STRING_DOT_QUOTE"
    STRING_S_QUOTE = "STRING_S_QUOTE"
    EOF = "EOF"


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    text: str
    file: str
    line: int
    col: int
    value: Optional[Any] = None


class LexError(Exception):
    """Raised on malformed input. Carries the source location of the error."""

    def __init__(self, message: str, file: str, line: int, col: int) -> None:
        super().__init__(f"{file}:{line}:{col}: {message}")
        self.message = message
        self.file = file
        self.line = line
        self.col = col


_WS = " \t\r\n"


def _is_ws(ch: str) -> bool:
    return ch != "" and ch in _WS


def _parse_int_or_none(text: str) -> Optional[int]:
    """Return the int value of `text` if it is a signed decimal integer."""
    if not text:
        return None
    body = text[1:] if text[0] in "+-" else text
    if not body or not body.isdigit():
        return None
    try:
        return int(text)
    except ValueError:  # pragma: no cover — guarded by isdigit check above
        return None


def _parse_float_or_none(text: str) -> Optional[float]:
    """Return the float value of `text` if it matches the mforth-xk7
    float-literal regex (signed decimal with at least one digit on each
    side of the dot, optional scientific-notation exponent).

    Returns ``None`` for inputs the int-recognizer would already accept
    (no decimal point) and for malformed cases like ``3.`` / ``.5`` /
    ``3.14.15``. Those fall through to the WORD branch.
    """
    if not text or _FLOAT_RE.match(text) is None:
        return None
    try:
        return float(text)
    except ValueError:  # pragma: no cover — guarded by regex above
        return None


def tokenize(src: str, file: str = "<unknown>") -> Iterator[Token]:
    """Tokenize `src`. Yields tokens including a trailing EOF.

    Raises `LexError` with source location on malformed input.
    """
    n = len(src)
    i = 0
    line = 1
    col = 1

    def advance() -> str:
        """Consume one character and update (line, col). Returns the char."""
        nonlocal i, line, col
        if i >= n:
            return ""
        ch = src[i]
        i += 1
        if ch == "\n":
            line += 1
            col = 1
        else:
            col += 1
        return ch

    def peek(offset: int = 0) -> str:
        j = i + offset
        return src[j] if 0 <= j < n else ""

    def skip_ws() -> None:
        while i < n and _is_ws(src[i]):
            advance()

    while True:
        skip_ws()

        if i >= n:
            yield Token(TokenKind.EOF, "", file, line, col)
            return

        start_line, start_col = line, col
        ch = src[i]
        next_ch = peek(1)

        # Line comment: standalone "\" word
        if ch == "\\" and (next_ch == "" or _is_ws(next_ch)):
            while i < n and src[i] != "\n":
                advance()
            continue

        # Paren comment: standalone "(" word, nestable, must terminate with ")"
        if ch == "(" and (next_ch == "" or _is_ws(next_ch)):
            paren_line, paren_col = start_line, start_col
            advance()  # consume opening '('
            depth = 1
            while i < n and depth > 0:
                c = src[i]
                if c == "(" and (peek(-1) == "" or _is_ws(peek(-1))):
                    depth += 1
                    advance()
                elif c == ")":
                    depth -= 1
                    advance()
                else:
                    advance()
            if depth > 0:
                raise LexError(
                    "unterminated '(' comment", file, paren_line, paren_col
                )
            continue

        # Dot-quote string: standalone ".\"" parsing-word
        if (
            ch == "."
            and next_ch == '"'
            and (peek(2) == "" or _is_ws(peek(2)))
        ):
            advance(); advance()  # consume `."`
            if i < n and src[i] in " \t":
                advance()  # consume one delimiter char
            chars: list[str] = []
            while i < n and src[i] != '"':
                chars.append(src[i])
                advance()
            if i >= n:
                raise LexError(
                    'unterminated ." string', file, start_line, start_col
                )
            advance()  # closing '"'
            value = "".join(chars)
            yield Token(
                TokenKind.STRING_DOT_QUOTE,
                '."' + value + '"',
                file,
                start_line,
                start_col,
                value=value,
            )
            continue

        # S-quote string: standalone "S\"" parsing-word
        if (
            ch == "S"
            and next_ch == '"'
            and (peek(2) == "" or _is_ws(peek(2)))
        ):
            advance(); advance()  # consume `S"`
            if i < n and src[i] in " \t":
                advance()
            chars = []
            while i < n and src[i] != '"':
                chars.append(src[i])
                advance()
            if i >= n:
                raise LexError(
                    'unterminated S" string', file, start_line, start_col
                )
            advance()
            value = "".join(chars)
            yield Token(
                TokenKind.STRING_S_QUOTE,
                'S"' + value + '"',
                file,
                start_line,
                start_col,
                value=value,
            )
            continue

        # Generic whitespace-delimited token
        chars: list[str] = []
        while i < n and not _is_ws(src[i]):
            chars.append(src[i])
            advance()
        text = "".join(chars)

        if text == ":":
            yield Token(TokenKind.COLON, ":", file, start_line, start_col)
            continue
        if text == ";":
            yield Token(TokenKind.SEMICOLON, ";", file, start_line, start_col)
            continue

        int_value = _parse_int_or_none(text)
        if int_value is not None:
            yield Token(
                TokenKind.NUMBER, text, file, start_line, start_col, value=int_value
            )
            continue

        float_value = _parse_float_or_none(text)
        if float_value is not None:
            yield Token(
                TokenKind.FLOAT, text, file, start_line, start_col, value=float_value
            )
            continue

        yield Token(TokenKind.WORD, text, file, start_line, start_col)


