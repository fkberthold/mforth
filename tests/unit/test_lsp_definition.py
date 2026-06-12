"""Unit tests for the mforth LSP go-to-definition handler.

Bead mforth-10t.26 (part 1). Builds on the ``analyze_document`` /
``hover_for`` / ``completions_for`` pure-function seams from beads .23 /
.24 / .25 by adding a ``textDocument/definition`` capability.

Go-to-definition rules:

* **User-defined WordCall** — navigating from a call site jumps to the
  ``:`` opener of the matching ``: name ... ;`` definition (its recorded
  ``src_loc``). Returns an ``lsp.Location`` pointing at that file +
  position.
* **User VARIABLE reference** — navigating from a use of a variable
  jumps to the variable's declaration token (the ``UserVariable``
  ``src_loc``, on the NAME token).
* **Sidecar link name** — navigating from a sidecar-bound name (declared
  in a sibling ``<stem>.world.toml``) jumps to the sidecar file (head of
  file in v1, since TOML positions aren't surfaced).
* **Built-in WordCall** — no source location; returns ``None``.
* **Literal / whitespace / unresolved / broken document** — ``None``.

All assertions drive the pure ``definition_for`` function directly (and a
Layer-2/3 pair that drives the registered pygls handler), mirroring the
hermetic style in ``test_lsp_hover.py`` and ``test_lsp_completion.py``.
"""

from __future__ import annotations

import textwrap

import pytest

pytest.importorskip("pygls")
pytest.importorskip("lsprotocol")

from lsprotocol import types as lsp

from mforth.lsp.server import (
    create_server,
    definition_for,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pos(line: int, character: int) -> lsp.Position:
    return lsp.Position(line=line, character=character)


def _loc(result) -> lsp.Location:
    """Normalize the definition result (single Location, list of
    Locations, or LocationLink) to a single Location for assertions."""
    assert result is not None
    if isinstance(result, list):
        assert len(result) >= 1
        result = result[0]
    return result


# ---------------------------------------------------------------------------
# Layer 1 — pure definition_for(text, uri, position)
# ---------------------------------------------------------------------------


def test_definition_on_user_word_call_jumps_to_definition():
    text = textwrap.dedent(
        """\
        : square dup * ;
        3 square .
        """
    )
    uri = "file:///tmp/sq.fs"
    # Call site `square` is on line 2 (LSP line 1), char 2..7.
    result = definition_for(text, uri=uri, position=_pos(1, 3))
    loc = _loc(result)
    assert loc.uri == uri
    # Definition `:` opener is at line 1 col 1 (1-based) → LSP (0, 0).
    assert loc.range.start.line == 0
    assert loc.range.start.character == 0


def test_definition_on_user_variable_reference_jumps_to_declaration():
    text = textwrap.dedent(
        """\
        VARIABLE counter
        counter @ .
        """
    )
    uri = "file:///tmp/v.fs"
    # Reference `counter` is on line 2 (LSP line 1), char 0..7.
    result = definition_for(text, uri=uri, position=_pos(1, 2))
    loc = _loc(result)
    assert loc.uri == uri
    # `counter` declaration token is at line 1 col 10 (1-based) → LSP (0, 9).
    assert loc.range.start.line == 0
    assert loc.range.start.character == 9


def test_definition_on_builtin_word_returns_none():
    """Built-ins have no source location to navigate to."""
    text = "DUP DROP\n"
    result = definition_for(text, uri="file:///tmp/b.fs", position=_pos(0, 1))
    assert result is None


def test_definition_on_literal_returns_none():
    text = "42 .\n"
    result = definition_for(text, uri="file:///tmp/lit.fs", position=_pos(0, 0))
    assert result is None


def test_definition_on_whitespace_returns_none():
    text = ": square dup * ;\n3 square .\n"
    # Cursor on the space between `3` and `square` (line 2, char 1).
    result = definition_for(text, uri="file:///tmp/ws.fs", position=_pos(1, 1))
    assert result is None


def test_definition_on_broken_document_returns_none():
    text = ": broken\n"  # missing `;`
    result = definition_for(text, uri="file:///tmp/x.fs", position=_pos(0, 3))
    assert result is None


def test_definition_on_definition_header_name_returns_none():
    """The `:` header name is not a navigable WordCall term — the parser
    folds it into the Definition node rather than emitting it as a term in
    the body. Go-to-definition from the declaring occurrence therefore has
    nothing to point at and returns None. (Navigating FROM a call site
    works — see test_definition_on_user_word_call_jumps_to_definition.)"""
    text = ": square dup * ;\n3 square .\n"
    uri = "file:///tmp/self.fs"
    # `square` in the `:` header is at line 1 col 3 (1-based) → LSP (0, 2).
    result = definition_for(text, uri=uri, position=_pos(0, 3))
    assert result is None


def test_definition_on_sidecar_link_jumps_to_sidecar(tmp_path):
    fs_file = tmp_path / "blink.fs"
    fs_file.write_text("display PRINTFLUSH\n")
    sidecar = tmp_path / "blink.world.toml"
    sidecar.write_text(
        textwrap.dedent(
            """\
            [links.display]
            type = "message"
            target = "message1"
            """
        )
    )
    uri = fs_file.as_uri()
    text = "display PRINTFLUSH\n"
    # `display` is on line 1 (LSP line 0), char 0..7.
    result = definition_for(text, uri=uri, position=_pos(0, 2))
    loc = _loc(result)
    assert loc.uri == sidecar.as_uri()


# ---------------------------------------------------------------------------
# Layer 2 — pygls server registers the definition handler
# ---------------------------------------------------------------------------


def test_create_server_registers_definition():
    server = create_server()
    registered = set(server.protocol.fm.features.keys())
    assert lsp.TEXT_DOCUMENT_DEFINITION in registered


# ---------------------------------------------------------------------------
# Layer 3 — driving the handler matches the pure function
# ---------------------------------------------------------------------------


def _invoke(handler, server, params):
    try:
        return handler(server, params)
    except TypeError:
        return handler(params)


def test_definition_handler_matches_pure_function(monkeypatch):
    server = create_server()
    open_handler = server.protocol.fm.features[lsp.TEXT_DOCUMENT_DID_OPEN]
    def_handler = server.protocol.fm.features[lsp.TEXT_DOCUMENT_DEFINITION]

    uri = "file:///tmp/handler.fs"
    text = ": square dup * ;\n3 square .\n"

    monkeypatch.setattr(
        server, "text_document_publish_diagnostics", lambda _params: None
    )

    open_params = lsp.DidOpenTextDocumentParams(
        text_document=lsp.TextDocumentItem(
            uri=uri, language_id="forth", version=1, text=text
        )
    )
    _invoke(open_handler, server, open_params)

    def_params = lsp.DefinitionParams(
        text_document=lsp.TextDocumentIdentifier(uri=uri),
        position=_pos(1, 3),
    )
    result = _invoke(def_handler, server, def_params)
    expected = definition_for(text, uri=uri, position=_pos(1, 3))
    assert _loc(result).range.start.line == _loc(expected).range.start.line
    assert _loc(result).uri == _loc(expected).uri


def test_definition_handler_returns_none_for_unknown_document():
    server = create_server()
    def_handler = server.protocol.fm.features[lsp.TEXT_DOCUMENT_DEFINITION]
    def_params = lsp.DefinitionParams(
        text_document=lsp.TextDocumentIdentifier(uri="file:///nowhere.fs"),
        position=_pos(0, 0),
    )
    result = _invoke(def_handler, server, def_params)
    assert result is None
