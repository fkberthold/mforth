"""mforth parser.

Consumes a token stream from `mforth.lex.tokenize` and produces a `Program`
AST: a list of `Definition`s and a `main` sequence of `Term` values, each
carrying a `SrcLoc` for end-to-end location propagation.

The parser is purely syntactic. It does **not** recognise `VARIABLE` /
`@` / `!` semantics — those become plain `WordCall`s and the dictionary
+ resolver pass (bead mforth-10t.6) is responsible for turning them into
`VarRef`s. `VarRef` is exported here for that downstream stage but the
parser never emits one. This split keeps the parser dictionary-free.

Control-flow constructs (`IF/ELSE/THEN`, `BEGIN/UNTIL`, `BEGIN/WHILE/REPEAT`,
`DO/LOOP`) are recognised by the parser because they have nesting structure
that needs to be made explicit in the AST. Keywords are matched
case-insensitively, matching standard Forth practice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from mforth.lex import LexError, Token, TokenKind, tokenize


# ---------------------------------------------------------------------------
# Source locations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SrcLoc:
    file: str
    line: int
    col: int


def _loc(tok: Token) -> SrcLoc:
    return SrcLoc(tok.file, tok.line, tok.col)


# ---------------------------------------------------------------------------
# AST node types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LitInt:
    value: int
    src_loc: SrcLoc


@dataclass(frozen=True)
class LitFloat:
    """Float-literal AST node (bead mforth-xk7).

    Same stack-effect as ``LitInt`` (``( -- f )``); the only reason
    LitFloat is a distinct type is so downstream consumers (the host
    primitive registry, the mlog emitter, the LSP hover) can render
    the value as a Python ``float`` instead of an ``int``. The host
    pushes ``self.value`` directly onto the data stack; the mlog
    emitter lowers it to ``set s<i> <value>`` using ``repr(value)``
    (mlog accepts Python's decimal/scientific float repr verbatim).
    """

    value: float
    src_loc: SrcLoc


@dataclass(frozen=True)
class LitStr:
    value: str
    src_loc: SrcLoc


@dataclass(frozen=True)
class WordCall:
    name: str
    src_loc: SrcLoc


@dataclass
class IfThen:
    then_body: list
    else_body: list
    src_loc: SrcLoc


@dataclass
class Begin:
    body: list
    kind: str  # 'until' | 'while-repeat'
    cond_body: list
    src_loc: SrcLoc


@dataclass
class DoLoop:
    body: list
    src_loc: SrcLoc


@dataclass
class VarRef:
    """Emitted by the resolver pass (bead mforth-10t.6), not by the parser."""

    name: str
    mode: str  # 'fetch' | 'store'
    src_loc: SrcLoc


Term = Union[LitInt, LitFloat, LitStr, WordCall, IfThen, Begin, DoLoop, VarRef]


@dataclass
class Definition:
    name: str
    body: list
    src_loc: SrcLoc
    # Optional declared stack effect from a `( inputs -- outputs )` comment
    # immediately after the `:` name. `None` means no declared effect (the
    # stack-checker stays permissive on this definition); a `(in, out)`
    # tuple means stackcheck must verify the inferred effect matches.
    # See bead mforth-6dh.
    declared_effect: "tuple[int, int] | None" = None


@dataclass
class Program:
    definitions: list = field(default_factory=list)
    main: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ParseError(Exception):
    """Raised on syntax errors. Carries the source location of the error."""

    def __init__(self, message: str, file: str, line: int, col: int) -> None:
        super().__init__(f"{file}:{line}:{col}: {message}")
        self.message = message
        self.file = file
        self.line = line
        self.col = col


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


_CONTROL_KEYWORDS = {
    "if", "else", "then",
    "begin", "until", "while", "repeat",
    "do", "loop",
}


class _Parser:
    def __init__(self, tokens: list[Token], file: str) -> None:
        self.tokens = tokens
        self.file = file
        self.pos = 0
        self._in_definition = False

    def _peek(self) -> Token:
        return self.tokens[self.pos]

    def _advance(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def _is_keyword(self, tok: Token, kw: str) -> bool:
        return tok.kind == TokenKind.WORD and tok.text.lower() == kw

    # --- top-level program ------------------------------------------------

    def parse_program(self) -> Program:
        definitions: list[Definition] = []
        main: list[Term] = []
        while self._peek().kind != TokenKind.EOF:
            tok = self._peek()
            if tok.kind == TokenKind.COLON:
                definitions.append(self._parse_definition())
            elif tok.kind == TokenKind.EFFECT_COMMENT:
                # Top-level effect-shaped comments aren't attached to
                # anything — they're just comments and get skipped.
                self._advance()
            else:
                main.append(self._parse_term())
        return Program(definitions=definitions, main=main)

    # --- definitions ------------------------------------------------------

    def _parse_definition(self) -> Definition:
        colon = self._advance()
        if self._in_definition:
            raise ParseError(
                "nested ':' definitions are not allowed",
                self.file, colon.line, colon.col,
            )
        name_tok = self._peek()
        if name_tok.kind != TokenKind.WORD:
            raise ParseError(
                "expected definition name after ':'",
                self.file, colon.line, colon.col,
            )
        self._advance()
        # Optional declared stack effect: `( inputs -- outputs )` comment
        # immediately after the name. The lexer has already parsed the
        # arities and attached them as `.value = (in, out)`. Anywhere else
        # in the body an EFFECT_COMMENT is just a comment and is skipped
        # in `_parse_term` (see below). See bead mforth-6dh.
        declared_effect: tuple[int, int] | None = None
        if self._peek().kind == TokenKind.EFFECT_COMMENT:
            effect_tok = self._advance()
            declared_effect = effect_tok.value
        self._in_definition = True
        body: list[Term] = []
        try:
            while True:
                tok = self._peek()
                if tok.kind == TokenKind.EOF:
                    raise ParseError(
                        "unterminated ':' definition (expected ';' before EOF)",
                        self.file, colon.line, colon.col,
                    )
                if tok.kind == TokenKind.SEMICOLON:
                    self._advance()
                    break
                if tok.kind == TokenKind.COLON:
                    raise ParseError(
                        "nested ':' definitions are not allowed",
                        self.file, tok.line, tok.col,
                    )
                if tok.kind == TokenKind.EFFECT_COMMENT:
                    # Mid-body effect-shaped comments are treated as plain
                    # comments — they don't override the declared effect
                    # from the opener position, and they don't emit AST
                    # nodes. Just skip.
                    self._advance()
                    continue
                body.append(self._parse_term())
        finally:
            self._in_definition = False
        return Definition(
            name=name_tok.text,
            body=body,
            src_loc=_loc(colon),
            declared_effect=declared_effect,
        )

    # --- terms ------------------------------------------------------------

    def _parse_term(self) -> Term:
        tok = self._peek()

        if tok.kind == TokenKind.NUMBER:
            self._advance()
            return LitInt(value=tok.value, src_loc=_loc(tok))

        if tok.kind == TokenKind.FLOAT:
            self._advance()
            return LitFloat(value=tok.value, src_loc=_loc(tok))

        if tok.kind in (TokenKind.STRING_DOT_QUOTE, TokenKind.STRING_S_QUOTE):
            self._advance()
            return LitStr(value=tok.value, src_loc=_loc(tok))

        if tok.kind == TokenKind.WORD:
            lowered = tok.text.lower()
            if lowered == "if":
                return self._parse_if()
            if lowered in ("else", "then"):
                raise ParseError(
                    f"'{tok.text}' without matching 'IF'",
                    self.file, tok.line, tok.col,
                )
            if lowered == "begin":
                return self._parse_begin()
            if lowered in ("until", "while", "repeat"):
                raise ParseError(
                    f"'{tok.text}' without matching 'BEGIN'",
                    self.file, tok.line, tok.col,
                )
            if lowered == "do":
                return self._parse_do()
            if lowered == "loop":
                raise ParseError(
                    "'LOOP' without matching 'DO'",
                    self.file, tok.line, tok.col,
                )
            self._advance()
            return WordCall(name=tok.text, src_loc=_loc(tok))

        if tok.kind == TokenKind.SEMICOLON:
            raise ParseError(
                "unexpected ';' (no open ':' definition)",
                self.file, tok.line, tok.col,
            )

        if tok.kind == TokenKind.COLON:
            # Inside a definition body the loop short-circuits COLON before
            # _parse_term is called; at top level _parse_program routes COLON
            # to _parse_definition. Reaching here would mean someone tried to
            # parse a COLON as a Term — defensive.
            raise ParseError(
                "unexpected ':'", self.file, tok.line, tok.col,
            )

        raise ParseError(
            f"unexpected token {tok.kind.name}",
            self.file, tok.line, tok.col,
        )

    # --- control flow -----------------------------------------------------

    def _parse_if(self) -> IfThen:
        if_tok = self._advance()
        then_body: list[Term] = []
        else_body: list[Term] = []
        in_else = False
        while True:
            tok = self._peek()
            if tok.kind == TokenKind.EOF:
                raise ParseError(
                    "unterminated 'IF' (expected 'THEN')",
                    self.file, if_tok.line, if_tok.col,
                )
            if self._is_keyword(tok, "else"):
                if in_else:
                    raise ParseError(
                        "duplicate 'ELSE' in 'IF'",
                        self.file, tok.line, tok.col,
                    )
                self._advance()
                in_else = True
                continue
            if self._is_keyword(tok, "then"):
                self._advance()
                return IfThen(then_body=then_body, else_body=else_body, src_loc=_loc(if_tok))
            (else_body if in_else else then_body).append(self._parse_term())

    def _parse_begin(self) -> Begin:
        begin_tok = self._advance()
        first_body: list[Term] = []
        while True:
            tok = self._peek()
            if tok.kind == TokenKind.EOF:
                raise ParseError(
                    "unterminated 'BEGIN' (expected 'UNTIL' or 'WHILE')",
                    self.file, begin_tok.line, begin_tok.col,
                )
            if self._is_keyword(tok, "until"):
                self._advance()
                return Begin(
                    body=first_body, kind="until", cond_body=[], src_loc=_loc(begin_tok)
                )
            if self._is_keyword(tok, "while"):
                self._advance()
                second_body: list[Term] = []
                while True:
                    tok2 = self._peek()
                    if tok2.kind == TokenKind.EOF:
                        raise ParseError(
                            "unterminated 'BEGIN/WHILE' (expected 'REPEAT')",
                            self.file, begin_tok.line, begin_tok.col,
                        )
                    if self._is_keyword(tok2, "repeat"):
                        self._advance()
                        return Begin(
                            body=first_body,
                            kind="while-repeat",
                            cond_body=second_body,
                            src_loc=_loc(begin_tok),
                        )
                    second_body.append(self._parse_term())
            first_body.append(self._parse_term())

    def _parse_do(self) -> DoLoop:
        do_tok = self._advance()
        body: list[Term] = []
        while True:
            tok = self._peek()
            if tok.kind == TokenKind.EOF:
                raise ParseError(
                    "unterminated 'DO' (expected 'LOOP')",
                    self.file, do_tok.line, do_tok.col,
                )
            if self._is_keyword(tok, "loop"):
                self._advance()
                return DoLoop(body=body, src_loc=_loc(do_tok))
            body.append(self._parse_term())


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse(src: str, file: str = "<unknown>") -> Program:
    """Parse `src` into a `Program`. Raises `ParseError` or `LexError`."""
    tokens = list(tokenize(src, file=file))
    return _Parser(tokens, file).parse_program()


__all__ = [
    "Begin",
    "Definition",
    "DoLoop",
    "IfThen",
    "LexError",
    "LitFloat",
    "LitInt",
    "LitStr",
    "ParseError",
    "Program",
    "SrcLoc",
    "Term",
    "VarRef",
    "WordCall",
    "parse",
]
