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
from mforth.dictionary import UnresolvedWordError, resolve
from mforth.lex import LexError
from mforth.parse import ParseError, parse
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

    @server.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
    def _on_did_open(
        ls: LanguageServer, params: lsp.DidOpenTextDocumentParams
    ) -> None:
        uri = params.text_document.uri
        text = params.text_document.text
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
        diags = _select_analyzer(uri)(text, uri=uri)
        ls.text_document_publish_diagnostics(
            lsp.PublishDiagnosticsParams(uri=uri, diagnostics=diags)
        )

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
    "serve_stdio",
]
