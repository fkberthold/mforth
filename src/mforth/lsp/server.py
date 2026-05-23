"""mforth language server — pygls stdio server + pure analyzers.

Bead mforth-10t.23. The server reuses the same lex / parse / resolve /
stackcheck pipeline as the compiler so the LSP and compiler agree on
every diagnostic by construction.

Architecture
============

Two layers:

1. **Pure analyzers** — :func:`analyze_document` and
   :func:`analyze_sidecar` are deterministic functions
   ``(text, uri) -> list[Diagnostic]``. They never touch the network,
   the filesystem, or pygls — they just run the analyzer pipeline,
   convert any error into a single :class:`lsprotocol.types.Diagnostic`
   anchored at its ``src_loc``, and return. This is the surface the
   unit tests exercise.

2. **pygls server** — :func:`create_server` instantiates a
   :class:`pygls.lsp.server.LanguageServer`, registers handlers for
   ``textDocument/didOpen`` and ``textDocument/didChange``, and wires
   them to the appropriate analyzer (forth vs. sidecar, chosen by the
   document URI suffix). The handlers call
   ``ls.text_document_publish_diagnostics(...)`` with the analyzer
   output — so the LSP <-> analyzer contract is exact and pinned by
   ``test_did_open_handler_publishes_diagnostics_matching_analyzer``.

Error → diagnostic mapping
==========================

Every error type the pipeline raises carries a 1-based ``(file, line,
col)`` source location, either as bare attributes (``LexError``,
``ParseError``) or wrapped in a :class:`mforth.parse.SrcLoc`
(``UnresolvedWordError``, ``StackError``). LSP positions are 0-based,
so we subtract 1. The diagnostic range spans a single character;
downstream beads (.24 hover, etc.) can widen ranges per word once the
analyzer surfaces end positions.

The pipeline halts at the first error in v1 — the parser is not
error-recovering. Multi-error documents will surface one diagnostic at
a time until the underlying parser learns error recovery. Tracked as a
followup, not a v1 bug.

Sidecar handling
================

Documents with a ``.world.toml`` suffix are dispatched to
:func:`analyze_sidecar`, which delegates to
:func:`mforth.backend.sidecar.parse_sidecar`. ``SidecarError`` is
mapped to a diagnostic at line 0, col 0 of the sidecar URI — TOML
parse failures do not carry per-token positions in the standard
``tomllib`` exception surface, so v1 anchors at file head. Improving
this (parsing the ``TOMLDecodeError.lineno``/``.colno`` attributes)
is filed as a followup.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from typing import List

from lsprotocol import types as lsp
from pygls.lsp.server import LanguageServer

from mforth.backend.sidecar import SidecarError, parse_sidecar
from mforth.dictionary import (
    BuiltinWord,
    Definition,
    UnresolvedWordError,
    UserVariable,
    resolve,
)
from mforth.lex import LexError
from mforth.parse import (
    Begin,
    DoLoop,
    IfThen,
    LitInt,
    LitStr,
    ParseError,
    Program,
    SrcLoc,
    VarRef,
    WordCall,
    parse,
)
from mforth.stackcheck import StackError, stackcheck


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


SERVER_NAME = "mforth-lsp"
DIAGNOSTIC_SOURCE = "mforth"
SIDECAR_SUFFIX = ".world.toml"


# ---------------------------------------------------------------------------
# Diagnostic helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PosOneBased:
    """Internal carrier: 1-based source location before LSP conversion."""

    line: int
    col: int


def _lsp_position(line_1based: int, col_1based: int) -> lsp.Position:
    """Convert 1-based (line, col) from the compiler error surface to
    0-based LSP positions. Clamps at zero defensively — a malformed
    error with line=0 would otherwise produce a negative position."""
    return lsp.Position(
        line=max(0, line_1based - 1),
        character=max(0, col_1based - 1),
    )


def _diag(line_1based: int, col_1based: int, message: str) -> lsp.Diagnostic:
    start = _lsp_position(line_1based, col_1based)
    end = lsp.Position(line=start.line, character=start.character + 1)
    return lsp.Diagnostic(
        range=lsp.Range(start=start, end=end),
        message=message,
        severity=lsp.DiagnosticSeverity.Error,
        source=DIAGNOSTIC_SOURCE,
    )


# ---------------------------------------------------------------------------
# Pure analyzers
# ---------------------------------------------------------------------------


def analyze_document(text: str, *, uri: str) -> List[lsp.Diagnostic]:
    """Run lex / parse / resolve / stackcheck on `text` and return any
    diagnostics. Always returns a list (possibly empty)."""
    file = _file_from_uri(uri)
    try:
        program = parse(text, file=file)
    except LexError as e:
        return [_diag(e.line, e.col, e.message)]
    except ParseError as e:
        return [_diag(e.line, e.col, e.message)]

    try:
        dictionary = resolve(program)
    except UnresolvedWordError as e:
        return [_diag(e.src_loc.line, e.src_loc.col, str(e.args[0]).split(": ", 2)[-1])]

    try:
        stackcheck(program, dictionary=dictionary)
    except UnresolvedWordError as e:
        return [_diag(e.src_loc.line, e.src_loc.col, str(e.args[0]).split(": ", 2)[-1])]
    except StackError as e:
        return [_diag(e.src_loc.line, e.src_loc.col, e.message)]

    return []


def analyze_sidecar(text: str, *, uri: str) -> List[lsp.Diagnostic]:
    """Parse a `.world.toml` sidecar and return any diagnostics."""
    source = _file_from_uri(uri)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return [_diag(1, 1, f"TOML parse error: {e}")]

    try:
        parse_sidecar(data, source=source)
    except SidecarError as e:
        return [_diag(1, 1, e.message)]

    return []


def _file_from_uri(uri: str) -> str:
    """Best-effort URI → filename for error messages. The LSP doesn't
    require this to be a real path — it's just what shows up in the
    compiler error prefix."""
    if uri.startswith("file://"):
        return uri[len("file://") :]
    return uri


# ---------------------------------------------------------------------------
# Hover (bead mforth-10t.24)
# ---------------------------------------------------------------------------
#
# `hover_for(text, *, uri, position)` mirrors the `analyze_document` seam
# from .23: a pure function the test harness can drive directly without
# spinning up pygls. The hover handler registered on the server is a
# thin shim that pulls the document text from the workspace and calls
# this function.
#
# Algorithm:
#
# 1. Re-run lex → parse → resolve → stackcheck. If any stage fails, the
#    pipeline cannot give a meaningful answer about a term at the
#    cursor, so we return None (no hover). Diagnostics surface the
#    error via the separate publishDiagnostics path; hover stays
#    silent rather than echoing the failure.
#
# 2. Walk every Term in the AST (main + every definition body, and
#    recursively into IfThen/Begin/DoLoop bodies). For each term,
#    compute its (line, start_col, end_col) extent and check whether
#    the cursor lands within that extent.
#
# 3. Classify the matched term:
#      * WordCall → look up in dictionary; format per entry kind
#        (BuiltinWord / Definition / UserVariable).
#      * LitInt / LitStr → format as a literal hover.
#    For BuiltinWord, the doc field is rendered verbatim; if it's
#    empty (none today, but defensive for future entries) the
#    fallback `(no documentation)` is used.
#
# Hover content format: plain text (`MarkupKind.PlainText`). Plain text
# avoids Markdown-escaping issues with `<`, `>`, `*`, `+` — all of
# which appear verbatim in mforth stack-effect notation and
# arithmetic primitives. Editors render `( a -- b )` and `1 -- 1`
# fine as-is. If a future bead wants Markdown bullets we'll switch
# globally.
#
# Position fidelity: token extents are derived from the source
# location (1-based line, col) and the rendered length of the term
# (`name` for WordCall, `str(value)` for LitInt, `len(value) + 2` for
# LitStr to cover surrounding quotes). The lexer does not export
# end-column today; this approximation is correct for every v1
# fixture and degrades gracefully for unusual whitespace (the
# hover-for-whitespace test pins the "no hover on a space" case).


_HOVER_FAILED = object()  # sentinel for failed analysis


def hover_for(
    text: str, *, uri: str, position: lsp.Position
) -> lsp.Hover | None:
    """Return a hover for the term under ``position``, or None if the
    cursor isn't on a recognizable term or analysis fails."""
    file = _file_from_uri(uri)

    # Hover needs the AST + the dictionary at minimum. parse/resolve
    # failures kill hover (we have nothing to point at). Stackcheck
    # failures are NON-fatal — without inferred effects we still
    # display the literal/built-in shape; user-definition effects
    # render as `( ? -- ? )`.
    try:
        program = parse(text, file=file)
    except (LexError, ParseError):
        return None

    try:
        dictionary = resolve(program)
    except UnresolvedWordError:
        return None

    try:
        sc_result = stackcheck(program, dictionary=dictionary)
    except (StackError, UnresolvedWordError):
        sc_result = None

    term = _term_at_position(program, position)
    if term is None:
        return None

    body = _format_hover(term, dictionary, sc_result)
    if body is None:
        return None
    return lsp.Hover(
        contents=lsp.MarkupContent(kind=lsp.MarkupKind.PlainText, value=body)
    )


def _term_at_position(program: Program, position: lsp.Position):
    """Find the AST term whose source extent contains ``position``.

    Position is LSP-style (0-based line + character). Term src_loc is
    1-based. Returns the matched term or None.
    """
    target_line_1based = position.line + 1
    target_col_1based = position.character + 1

    match: object | None = None

    def _visit(term) -> None:
        nonlocal match
        if match is not None:
            return

        # Recurse into structural nodes that don't themselves have a
        # paste-able hover. (IfThen/Begin/DoLoop are AST scaffolding;
        # the if/then/begin/loop keywords don't survive parsing as
        # WordCalls so they have no hover in v1 — see negative case.)
        if isinstance(term, IfThen):
            for t in term.then_body:
                _visit(t)
            for t in term.else_body:
                _visit(t)
            return
        if isinstance(term, Begin):
            for t in term.body:
                _visit(t)
            for t in term.cond_body:
                _visit(t)
            return
        if isinstance(term, DoLoop):
            for t in term.body:
                _visit(t)
            return

        extent = _term_extent(term)
        if extent is None:
            return
        line, start, end = extent
        if line == target_line_1based and start <= target_col_1based < end:
            match = term

    for t in program.main:
        _visit(t)
    for defn in program.definitions:
        for t in defn.body:
            _visit(t)

    return match


def _term_extent(term) -> tuple[int, int, int] | None:
    """Return (line, start_col, end_col_exclusive) for a term, all
    1-based. Returns None if the term has no representable extent
    (control-flow structural nodes — but those are filtered before
    this is called)."""
    if isinstance(term, WordCall):
        return (term.src_loc.line, term.src_loc.col, term.src_loc.col + len(term.name))
    if isinstance(term, LitInt):
        return (
            term.src_loc.line,
            term.src_loc.col,
            term.src_loc.col + len(str(term.value)),
        )
    if isinstance(term, LitStr):
        # `." hello"` — the parser puts src_loc on the `."` opener; the
        # rendered length covers the opener (2 chars), the value, and the
        # trailing quote. This is an approximation, but it's enough to
        # let the cursor land anywhere on the literal and hit it.
        return (
            term.src_loc.line,
            term.src_loc.col,
            term.src_loc.col + len(term.value) + 4,
        )
    if isinstance(term, VarRef):
        return (term.src_loc.line, term.src_loc.col, term.src_loc.col + len(term.name))
    return None


def _format_hover(term, dictionary, sc_result) -> str | None:
    """Render the hover body for a matched term. Returns None if the
    term shape isn't hover-able (e.g. an unresolved word)."""
    if isinstance(term, LitInt):
        return f"{term.value}\n( -- n )"
    if isinstance(term, LitStr):
        return f'"{term.value}"\n( -- str )'
    if isinstance(term, WordCall):
        entry = dictionary.lookup(term.name)
        if entry is None:
            return None
        if isinstance(entry, BuiltinWord):
            return _format_builtin(entry)
        if isinstance(entry, Definition):
            return _format_user_def(entry, sc_result)
        if isinstance(entry, UserVariable):
            return _format_user_var(entry)
    if isinstance(term, VarRef):
        entry = dictionary.lookup(term.name)
        if isinstance(entry, UserVariable):
            return _format_user_var(entry)
    return None


def _format_stack_effect(in_arity: int, out_arity: int) -> str:
    return f"( {in_arity} -- {out_arity} )"


def _format_builtin(entry: BuiltinWord) -> str:
    eff = _format_stack_effect(entry.stack_effect.in_arity, entry.stack_effect.out_arity)
    doc = entry.doc if entry.doc else "(no documentation)"
    return f"{entry.name} {eff}\n{doc}"


def _format_user_def(entry: Definition, sc_result) -> str:
    eff_obj = None
    if sc_result is not None and sc_result.effects is not None:
        eff_obj = sc_result.effects.get(entry.name)
    if eff_obj is not None:
        eff = _format_stack_effect(eff_obj.in_arity, eff_obj.out_arity)
    else:
        eff = "( ? -- ? )"
    loc = entry.src_loc
    return f"{entry.name} {eff}\ndefined at {loc.file}:{loc.line}:{loc.col}"


def _format_user_var(entry: UserVariable) -> str:
    loc = entry.src_loc
    return (
        f"{entry.name} ( -- addr )\n"
        f"variable defined at {loc.file}:{loc.line}:{loc.col}"
    )


# ---------------------------------------------------------------------------
# pygls server factory
# ---------------------------------------------------------------------------


def _select_analyzer(uri: str):
    """Choose the right analyzer based on the document URI."""
    if uri.endswith(SIDECAR_SUFFIX):
        return analyze_sidecar
    return analyze_document


def create_server() -> LanguageServer:
    """Build a fresh `LanguageServer` instance with did_open /
    did_change handlers wired to the analyzer."""
    server = LanguageServer(name=SERVER_NAME, version=_get_server_version())

    # Per-server URI → latest text cache. Populated by did_open and
    # did_change. The hover handler reads from this rather than from
    # `ls.workspace.get_text_document(uri)` because the workspace API
    # requires an initialized server (i.e. a live transport), which
    # the test harness intentionally doesn't provide. Keeping our own
    # cache also means hover behavior is deterministic and decoupled
    # from pygls' internal sync-kind state machine.
    document_cache: dict[str, str] = {}
    server._mforth_document_cache = document_cache  # type: ignore[attr-defined]

    @server.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
    def _on_did_open(
        ls: LanguageServer, params: lsp.DidOpenTextDocumentParams
    ) -> None:
        uri = params.text_document.uri
        text = params.text_document.text
        document_cache[uri] = text
        diags = _select_analyzer(uri)(text, uri=uri)
        ls.text_document_publish_diagnostics(
            lsp.PublishDiagnosticsParams(uri=uri, diagnostics=diags)
        )

    @server.feature(lsp.TEXT_DOCUMENT_DID_CHANGE)
    def _on_did_change(
        ls: LanguageServer, params: lsp.DidChangeTextDocumentParams
    ) -> None:
        uri = params.text_document.uri
        # Full-document sync: pygls accumulates the new text in
        # workspace, but the simplest path that works for both the
        # test harness and the live LSP is to read the last
        # WholeDocument change directly.
        text = _extract_change_text(ls, params)
        if text is None:
            return
        document_cache[uri] = text
        diags = _select_analyzer(uri)(text, uri=uri)
        ls.text_document_publish_diagnostics(
            lsp.PublishDiagnosticsParams(uri=uri, diagnostics=diags)
        )

    @server.feature(lsp.TEXT_DOCUMENT_HOVER)
    def _on_hover(
        ls: LanguageServer, params: lsp.HoverParams
    ) -> lsp.Hover | None:
        uri = params.text_document.uri
        # Sidecar TOML documents don't get hover in v1 — only Forth.
        if uri.endswith(SIDECAR_SUFFIX):
            return None
        text = document_cache.get(uri)
        if text is None:
            return None
        return hover_for(text, uri=uri, position=params.position)

    return server


def _extract_change_text(
    ls: LanguageServer, params: lsp.DidChangeTextDocumentParams
) -> str | None:
    """Pull the new full text from a didChange params.

    With ``TextDocumentSyncKind.Full`` (pygls default) the latest
    content_change carries the full document. Fall back to the
    workspace document if the change is empty.
    """
    if params.content_changes:
        last = params.content_changes[-1]
        text = getattr(last, "text", None)
        if isinstance(text, str):
            return text
    # Workspace fallback — keeps the handler correct against an
    # Incremental sync configuration that the test harness doesn't
    # exercise but a real client might use.
    try:
        doc = ls.workspace.get_text_document(params.text_document.uri)
        return doc.source
    except Exception:
        return None


def _get_server_version() -> str:
    """Return the server version string. Defers to mforth.__version__
    so the LSP reports a version that tracks the package."""
    import mforth

    return mforth.__version__


# ---------------------------------------------------------------------------
# Stdio entry point
# ---------------------------------------------------------------------------


def serve_stdio() -> int:
    """Run the server over stdio. Called from `mforth lsp` and
    `python -m mforth.lsp`."""
    server = create_server()
    server.start_io()
    return 0


__all__ = [
    "DIAGNOSTIC_SOURCE",
    "SERVER_NAME",
    "SIDECAR_SUFFIX",
    "analyze_document",
    "analyze_sidecar",
    "create_server",
    "hover_for",
    "serve_stdio",
]
