"""Unit tests for the mforth LSP hover handler.

Bead mforth-10t.24. Builds on the ``analyze_document`` contract laid down
in bead .23 (drawer ``drawer_mforth_decisions_71c0ea542686301ac4535cd5``)
by adding a new ``textDocument/hover`` capability. Hover responses are
shaped per term-type:

* **Built-in WordCall** — ``<name> ( in -- out )`` + one-line doc from
  the dictionary entry.
* **User-defined WordCall** — ``<name> ( in -- out )`` + ``defined at
  <file>:<line>:<col>``. The stack effect is inferred from the
  stack-checker (bead .7).
* **VARIABLE WordCall** — ``<name> ( -- addr )`` + ``variable defined at
  <file>:<line>:<col>``.
* **Integer literal** — ``<value> ( -- n )``.
* **String literal** — ``"<value>" ( -- str )``.

The hover handler returns ``None`` (no hover) when the cursor isn't on
a recognizable term, when the pipeline fails to analyze the document,
or when the cursor lands on a control-flow keyword that's structural
rather than a real WordCall.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pygls")
pytest.importorskip("lsprotocol")

from lsprotocol import types as lsp

from mforth.lsp.server import (
    create_server,
    hover_for,
)


# ---------------------------------------------------------------------------
# Layer 1 — pure hover_for(text, uri, position)
# ---------------------------------------------------------------------------


def _hover_text(hover: lsp.Hover | None) -> str:
    """Extract the hover string regardless of MarkupContent vs MarkedString
    encoding, so tests assert on the rendered content rather than the
    wrapping shape."""
    assert hover is not None
    contents = hover.contents
    if isinstance(contents, lsp.MarkupContent):
        return contents.value
    if isinstance(contents, str):
        return contents
    parts: list[str] = []
    for c in contents:
        if isinstance(c, str):
            parts.append(c)
        else:
            parts.append(getattr(c, "value", ""))
    return "\n".join(parts)


def _pos(line: int, character: int) -> lsp.Position:
    return lsp.Position(line=line, character=character)


def test_hover_on_builtin_word_shows_stack_effect_and_doc():
    text = "DUP\n"
    h = hover_for(text, uri="file:///tmp/h.fs", position=_pos(0, 1))
    body = _hover_text(h)
    assert "DUP" in body
    assert "--" in body
    assert "duplicate top of stack" in body


def test_hover_on_arithmetic_builtin_shows_doc():
    text = "1 2 +\n"
    h = hover_for(text, uri="file:///tmp/h.fs", position=_pos(0, 4))
    body = _hover_text(h)
    assert "+" in body
    assert "add" in body


def test_hover_on_mindustry_builtin_shows_stack_effect():
    text = "PRINT\n"
    h = hover_for(text, uri="file:///tmp/h.fs", position=_pos(0, 0))
    body = _hover_text(h)
    assert "PRINT" in body
    assert "--" in body


def test_hover_on_user_definition_shows_source_location_and_inferred_effect():
    text = ": square dup * ;\n3 square .\n"
    # Hover on the call site `square` on line 2 (LSP line 1), char 3.
    h = hover_for(text, uri="file:///tmp/sq.fs", position=_pos(1, 3))
    body = _hover_text(h)
    assert "square" in body
    assert "--" in body
    assert "defined at" in body
    # Definition src_loc anchors at the `:` opener (line 1 col 1, 1-based).
    # Decision: use the parser's recorded src_loc rather than synthesizing
    # the name token's location — keeps hover honest about what the AST
    # carries. Captured in the .24 ship drawer.
    assert "1:1" in body


def test_hover_on_user_variable_shows_variable_location():
    text = "VARIABLE counter\ncounter @\n"
    h = hover_for(text, uri="file:///tmp/v.fs", position=_pos(1, 3))
    body = _hover_text(h)
    assert "counter" in body
    assert "variable" in body.lower()
    # `counter` declaration token is at line 1 col 10 (1-based).
    assert "1:10" in body


def test_hover_on_integer_literal_shows_value_and_push_effect():
    text = "42 .\n"
    h = hover_for(text, uri="file:///tmp/lit.fs", position=_pos(0, 0))
    body = _hover_text(h)
    assert "42" in body
    assert "--" in body


def test_hover_on_string_literal_shows_value():
    text = '." hello"\n'
    h = hover_for(text, uri="file:///tmp/str.fs", position=_pos(0, 4))
    body = _hover_text(h)
    assert "hello" in body
    assert "--" in body


def test_hover_on_whitespace_returns_none():
    text = "DUP   DROP\n"
    h = hover_for(text, uri="file:///tmp/ws.fs", position=_pos(0, 3))
    assert h is None


def test_hover_on_empty_document_returns_none():
    text = ""
    h = hover_for(text, uri="file:///tmp/empty.fs", position=_pos(0, 0))
    assert h is None


def test_hover_outside_any_token_returns_none():
    text = "DUP\n"
    h = hover_for(text, uri="file:///tmp/eof.fs", position=_pos(5, 0))
    assert h is None


def test_hover_on_broken_document_returns_none():
    text = ": broken\n"
    h = hover_for(text, uri="file:///tmp/broken.fs", position=_pos(0, 2))
    assert h is None


def test_hover_on_unresolved_word_returns_none():
    text = "nosuchword\n"
    h = hover_for(text, uri="file:///tmp/x.fs", position=_pos(0, 0))
    assert h is None


def test_hover_inside_definition_body_resolves_builtin():
    text = ": double dup + ;\n3 double .\n"
    # `dup` inside body at line 1 col 10 (1-based) → LSP (0, 9).
    h = hover_for(text, uri="file:///tmp/d.fs", position=_pos(0, 9))
    body = _hover_text(h)
    assert "DUP" in body.upper()
    assert "duplicate" in body.lower()


# ---------------------------------------------------------------------------
# Layer 2 — pygls server registers the hover handler
# ---------------------------------------------------------------------------


def test_create_server_registers_hover():
    server = create_server()
    registered = set(server.protocol.fm.features.keys())
    assert lsp.TEXT_DOCUMENT_HOVER in registered


# ---------------------------------------------------------------------------
# Layer 3 — driving the handler returns hover matching hover_for() standalone
# ---------------------------------------------------------------------------


def _invoke(handler, server, params):
    try:
        return handler(server, params)
    except TypeError:
        return handler(params)


def test_hover_handler_returns_content_matching_pure_function(monkeypatch):
    server = create_server()

    open_handler = server.protocol.fm.features[lsp.TEXT_DOCUMENT_DID_OPEN]
    hover_handler = server.protocol.fm.features[lsp.TEXT_DOCUMENT_HOVER]

    uri = "file:///tmp/handler.fs"
    text = "DUP\n"

    monkeypatch.setattr(
        server, "text_document_publish_diagnostics", lambda _params: None
    )

    open_params = lsp.DidOpenTextDocumentParams(
        text_document=lsp.TextDocumentItem(
            uri=uri, language_id="forth", version=1, text=text
        )
    )
    _invoke(open_handler, server, open_params)

    hover_params = lsp.HoverParams(
        text_document=lsp.TextDocumentIdentifier(uri=uri),
        position=_pos(0, 1),
    )
    result = _invoke(hover_handler, server, hover_params)

    assert result is not None
    expected = hover_for(text, uri=uri, position=_pos(0, 1))
    assert _hover_text(result) == _hover_text(expected)


def test_hover_handler_returns_none_for_unknown_document():
    """Hover on a document the server hasn't seen returns None gracefully."""
    server = create_server()
    hover_handler = server.protocol.fm.features[lsp.TEXT_DOCUMENT_HOVER]

    hover_params = lsp.HoverParams(
        text_document=lsp.TextDocumentIdentifier(uri="file:///nowhere.fs"),
        position=_pos(0, 0),
    )
    result = _invoke(hover_handler, server, hover_params)
    assert result is None
