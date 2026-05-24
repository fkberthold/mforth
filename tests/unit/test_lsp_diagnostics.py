"""Unit tests for mforth.lsp — the pygls-based language server.

Bead mforth-10t.23. The LSP runs the same lex / parse / resolve /
stackcheck pipeline as the compiler and publishes diagnostics for every
error surfaced by that pipeline. By construction the LSP and compiler
agree on what is wrong with a `.fs` file.

The tests fall into three layers:

1. `analyze_document(text, uri)` is the pure analyzer at the heart of
   the server. It returns a list of `lsprotocol.types.Diagnostic`
   objects. Tested with five fixture documents (clean / parse-error /
   stack-mismatch / unresolved-word / lex-error) and one sidecar-error
   fixture exercising the companion `analyze_sidecar(text, uri)`.

2. The `mforth lsp` CLI subcommand. Asserts registration via the
   shared `mforth.cli` registry and the no-argument parser shape.

3. The server wiring. Asserts the server instance registers handlers
   for `textDocument/didOpen` and `textDocument/didChange`, and that
   driving those handlers publishes diagnostics that match what
   `analyze_document` returns standalone (the LSP <-> analyzer
   contract).
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

# pygls / lsprotocol are runtime dependencies; if they ever go missing
# from the dev environment the whole LSP test module should fail loudly
# rather than silently skip.
pytest.importorskip("pygls")
pytest.importorskip("lsprotocol")

from lsprotocol import types as lsp

from mforth.lsp.server import (
    analyze_document,
    analyze_sidecar,
    create_server,
)


# ---------------------------------------------------------------------------
# Layer 1 — pure analyzer
# ---------------------------------------------------------------------------


def _diag_messages(diags: List[lsp.Diagnostic]) -> List[str]:
    return [d.message for d in diags]


def test_analyze_clean_document_yields_no_diagnostics():
    text = ": double dup + ;\n3 double .\n"
    diags = analyze_document(text, uri="file:///tmp/clean.fs")
    assert diags == []


def test_analyze_parse_error_yields_diagnostic_at_src_loc():
    # `:` with no body before EOF — parse error.
    text = ": broken\n"
    diags = analyze_document(text, uri="file:///tmp/broken.fs")
    assert len(diags) == 1
    d = diags[0]
    assert d.severity == lsp.DiagnosticSeverity.Error
    assert d.source == "mforth"
    # ParseError carries (file, line, col) 1-based; LSP Position 0-based.
    # The `:` is at line 1 col 1 in source, so LSP line 0 col 0.
    assert d.range.start.line == 0
    assert d.range.start.character == 0


def test_analyze_unresolved_word_yields_diagnostic():
    text = "nosuchword\n"
    diags = analyze_document(text, uri="file:///tmp/unresolved.fs")
    assert len(diags) == 1
    d = diags[0]
    assert d.severity == lsp.DiagnosticSeverity.Error
    assert "nosuchword" in d.message
    assert d.range.start.line == 0
    assert d.range.start.character == 0


def test_analyze_stack_mismatch_yields_diagnostic():
    # `dup` on an empty stack — underflow caught by stackcheck.
    text = "dup\n"
    diags = analyze_document(text, uri="file:///tmp/stack.fs")
    assert len(diags) == 1
    d = diags[0]
    assert d.severity == lsp.DiagnosticSeverity.Error
    # Stack errors come from stackcheck.StackError; their src_loc
    # points at the offending term.
    assert d.range.start.line == 0


def test_analyze_lex_error_yields_diagnostic():
    # Unterminated paren comment — LexError.
    text = "( unterminated\n"
    diags = analyze_document(text, uri="file:///tmp/lex.fs")
    assert len(diags) == 1
    d = diags[0]
    assert d.severity == lsp.DiagnosticSeverity.Error
    assert d.range.start.line == 0


def test_analyze_sidecar_clean_yields_no_diagnostics():
    text = '[clock]\nipt = 8\nrealtime = false\n'
    diags = analyze_sidecar(text, uri="file:///tmp/example.world.toml")
    assert diags == []


def test_analyze_sidecar_toml_error_yields_diagnostic():
    text = "this is not = valid toml [[[\n"
    diags = analyze_sidecar(text, uri="file:///tmp/bad.world.toml")
    assert len(diags) == 1
    d = diags[0]
    assert d.severity == lsp.DiagnosticSeverity.Error
    assert d.source == "mforth"


def test_analyze_sidecar_schema_error_yields_diagnostic():
    # both target and index — schema violation.
    text = (
        '[links.gate]\n'
        'type = "switch"\n'
        'target = "switch1"\n'
        'index = 0\n'
    )
    diags = analyze_sidecar(text, uri="file:///tmp/dup.world.toml")
    assert len(diags) == 1
    assert diags[0].severity == lsp.DiagnosticSeverity.Error


# ---------------------------------------------------------------------------
# Layer 2 — CLI subcommand registration
# ---------------------------------------------------------------------------


def _reset_cli_registry():
    """Clear `_REGISTRY` and force re-import of the lsp subcommand
    module so its module-level `register_subcommand` call re-executes.
    Without the re-import, the cached module object skips its idempotent
    guard and the registry stays empty."""
    import sys

    import mforth.cli as cli_mod

    cli_mod._REGISTRY.clear()
    for mod in list(sys.modules):
        if mod.startswith("mforth.lsp"):
            del sys.modules[mod]
    return cli_mod


def test_lsp_subcommand_is_registered_via_registry():
    """The `lsp` subcommand must appear in the shared CLI registry,
    matching the registry pattern from mforth-326."""
    cli_mod = _reset_cli_registry()
    cli_mod._load_subcommands()
    assert "lsp" in cli_mod._REGISTRY
    entry = cli_mod._REGISTRY["lsp"]
    assert callable(entry.handler)
    assert callable(entry.configure_parser)


def test_lsp_subcommand_parser_takes_no_positional_args():
    """`mforth lsp` is argumentless — stdio LSP."""
    import argparse

    cli_mod = _reset_cli_registry()
    cli_mod._load_subcommands()
    entry = cli_mod._REGISTRY["lsp"]
    parser = argparse.ArgumentParser()
    entry.configure_parser(parser)
    # Should parse with no args.
    ns = parser.parse_args([])
    assert ns is not None


def test_cli_load_subcommands_imports_lsp_module():
    """`mforth.cli._load_subcommands` must import the lsp subcommand
    module so `mforth lsp` is discoverable end-to-end."""
    cli_mod = _reset_cli_registry()
    cli_mod._load_subcommands()
    assert "lsp" in cli_mod._REGISTRY, (
        "_load_subcommands must side-effect-import mforth.lsp.cli_subcommand"
    )


# ---------------------------------------------------------------------------
# Layer 3 — server wiring (handler drives analyzer; publishes diagnostics)
# ---------------------------------------------------------------------------


def test_create_server_returns_language_server_instance():
    from pygls.lsp.server import LanguageServer

    server = create_server()
    assert isinstance(server, LanguageServer)


def test_create_server_registers_did_open_and_did_change():
    server = create_server()
    # pygls stores registered features in protocol.fm.features
    registered = set(server.protocol.fm.features.keys())
    assert lsp.TEXT_DOCUMENT_DID_OPEN in registered
    assert lsp.TEXT_DOCUMENT_DID_CHANGE in registered


def _invoke(handler, server, params):
    """pygls feature handlers may take (ls, params) or (params,)."""
    try:
        return handler(server, params)
    except TypeError:
        return handler(params)


def test_did_open_handler_publishes_diagnostics_matching_analyzer(monkeypatch):
    """Driving the did_open handler on a broken document publishes the
    same diagnostics `analyze_document` returns standalone. This pins
    the LSP <-> analyzer contract that downstream beads .24/.25/.26
    build on."""
    server = create_server()

    published: list[lsp.PublishDiagnosticsParams] = []
    monkeypatch.setattr(
        server,
        "text_document_publish_diagnostics",
        lambda params: published.append(params),
    )

    handler = server.protocol.fm.features[lsp.TEXT_DOCUMENT_DID_OPEN]

    uri = "file:///tmp/broken.fs"
    text = ": broken\n"
    params = lsp.DidOpenTextDocumentParams(
        text_document=lsp.TextDocumentItem(
            uri=uri, language_id="forth", version=1, text=text
        )
    )
    _invoke(handler, server, params)

    assert len(published) == 1
    assert published[0].uri == uri
    expected = analyze_document(text, uri=uri)
    assert _diag_messages(published[0].diagnostics) == _diag_messages(expected)


def test_did_change_handler_republishes_diagnostics(monkeypatch):
    server = create_server()

    published: list[lsp.PublishDiagnosticsParams] = []
    monkeypatch.setattr(
        server,
        "text_document_publish_diagnostics",
        lambda p: published.append(p),
    )

    open_handler = server.protocol.fm.features[lsp.TEXT_DOCUMENT_DID_OPEN]
    change_handler = server.protocol.fm.features[lsp.TEXT_DOCUMENT_DID_CHANGE]

    uri = "file:///tmp/evolving.fs"
    clean = "1 2 + .\n"
    broken = ": broken\n"

    open_params = lsp.DidOpenTextDocumentParams(
        text_document=lsp.TextDocumentItem(
            uri=uri, language_id="forth", version=1, text=clean
        )
    )
    _invoke(open_handler, server, open_params)

    change_params = lsp.DidChangeTextDocumentParams(
        text_document=lsp.VersionedTextDocumentIdentifier(uri=uri, version=2),
        content_changes=[
            lsp.TextDocumentContentChangeWholeDocument(text=broken),
        ],
    )
    _invoke(change_handler, server, change_params)

    assert len(published) == 2
    assert published[0].diagnostics == []
    assert len(published[1].diagnostics) == 1
    assert published[1].diagnostics[0].severity == lsp.DiagnosticSeverity.Error


def test_did_change_handler_incremental_sync_uses_full_document(monkeypatch):
    """Regression for mforth-mig: under TextDocumentSyncKind.Incremental
    (the default for Helix, modern VS Code, modern Neovim), each
    didChange ships only the replaced fragment in `text` — NOT the full
    document. The LSP must analyse the assembled workspace document,
    not the fragment.

    Symptom of the bug being pinned: a `didChange` carrying just `"T"`
    (the user replaced one character) would otherwise cause the LSP
    to analyse the document as the literal string `"T"`, emit
    `unresolved word 'T'`, and pollute the published diagnostics. After
    the fix, the published diagnostics reflect analysis of the full
    workspace document, which is `"1 2 + .\\n"` plus the applied edit.
    """
    from pygls.workspace import Workspace

    server = create_server()
    # Initialise the workspace pygls-style so workspace.get_text_document
    # works. In production, pygls inits this during the LSP `initialize`
    # request; the test harness has to do it manually.
    server.protocol._workspace = Workspace(
        None, lsp.TextDocumentSyncKind.Incremental
    )

    published: list[lsp.PublishDiagnosticsParams] = []
    monkeypatch.setattr(
        server,
        "text_document_publish_diagnostics",
        lambda p: published.append(p),
    )

    open_handler = server.protocol.fm.features[lsp.TEXT_DOCUMENT_DID_OPEN]
    change_handler = server.protocol.fm.features[lsp.TEXT_DOCUMENT_DID_CHANGE]

    uri = "file:///tmp/incremental.fs"
    original = "1 2 + .\n"

    # didOpen with the full document — analysis is clean. Also update
    # the workspace (in production, pygls does this before the handler).
    open_doc = lsp.TextDocumentItem(
        uri=uri, language_id="forth", version=1, text=original
    )
    server.workspace.put_text_document(open_doc)
    _invoke(open_handler, server, lsp.DidOpenTextDocumentParams(text_document=open_doc))

    # didChange with an Incremental edit: insert "T" at col 0. `text`
    # carries ONLY the fragment, not the full document — this is what
    # Helix sends per-keystroke. Apply to workspace BEFORE invoking the
    # handler (mimicking pygls' middleware).
    change = lsp.TextDocumentContentChangePartial(
        range=lsp.Range(
            start=lsp.Position(line=0, character=0),
            end=lsp.Position(line=0, character=0),
        ),
        text="T",
    )
    server.workspace.update_text_document(
        text_doc=lsp.VersionedTextDocumentIdentifier(uri=uri, version=2),
        change=change,
    )
    change_params = lsp.DidChangeTextDocumentParams(
        text_document=lsp.VersionedTextDocumentIdentifier(uri=uri, version=2),
        content_changes=[change],
    )
    _invoke(change_handler, server, change_params)

    # Two publishes: one from didOpen, one from didChange. The didChange
    # publish must NOT contain a phantom `unresolved word 'T'` diagnostic.
    assert len(published) == 2
    assert published[0].diagnostics == []
    fragment_only_diags = [
        d for d in published[1].diagnostics
        if "'T'" in d.message and "unresolved word" in d.message
    ]
    assert fragment_only_diags == [], (
        f"LSP emitted phantom 'unresolved word T' diagnostic — bug "
        f"is analyzing the change fragment instead of the workspace "
        f"document. Diagnostics: {published[1].diagnostics!r}"
    )


def test_analyze_document_resolves_sidecar_link_names(tmp_path: Path):
    """Regression for mforth-pr8: a `.fs` document that references a
    sidecar-bound link (`display PRINTFLUSH`) must NOT show
    `unresolved word 'display'` in LSP diagnostics when the sibling
    `<stem>.world.toml` declares `[links.display]`.

    Before the fix, `analyze_document` ran resolve() without any
    sidecar context, so every sidecar-bound mforth-name surfaced as
    unresolved — even though CLI compile + `.14` runner + `.25` LSP
    completion all knew about the sidecar.

    The canonical fix matches `backend/runner.py` (`.14` ship): load
    the sibling `<stem>.world.toml`, pre-seed each `[links.X]` name as
    a `UserVariable` entry, pass the pre-populated dictionary into
    resolve().
    """
    from mforth.lsp.server import analyze_document

    src_path = tmp_path / "hello.fs"
    src_path.write_text('S" hi" PRINT\ndisplay PRINTFLUSH\n')
    sidecar_path = tmp_path / "hello.world.toml"
    sidecar_path.write_text(
        '[links.display]\ntype = "message"\ntarget = "message1"\n'
    )

    diags = analyze_document(src_path.read_text(), uri=src_path.as_uri())

    sidecar_link_errors = [
        d for d in diags if "'display'" in d.message and "unresolved" in d.message.lower()
    ]
    assert sidecar_link_errors == [], (
        f"LSP emitted phantom 'unresolved word display' even though "
        f"hello.world.toml declares [links.display]. Diagnostics: {diags!r}"
    )


def test_did_open_for_sidecar_publishes_sidecar_diagnostic(monkeypatch, tmp_path: Path):
    """Opening a `.world.toml` document runs the sidecar analyzer
    (not the Forth analyzer)."""
    server = create_server()

    published: list[lsp.PublishDiagnosticsParams] = []
    monkeypatch.setattr(
        server,
        "text_document_publish_diagnostics",
        lambda p: published.append(p),
    )

    open_handler = server.protocol.fm.features[lsp.TEXT_DOCUMENT_DID_OPEN]

    sidecar_path = tmp_path / "bad.world.toml"
    uri = sidecar_path.as_uri()
    text = "this is not = valid [[[ toml\n"
    params = lsp.DidOpenTextDocumentParams(
        text_document=lsp.TextDocumentItem(
            uri=uri, language_id="toml", version=1, text=text
        )
    )
    _invoke(open_handler, server, params)

    assert len(published) == 1
    assert published[0].uri == uri
    assert len(published[0].diagnostics) == 1
    assert published[0].diagnostics[0].severity == lsp.DiagnosticSeverity.Error
