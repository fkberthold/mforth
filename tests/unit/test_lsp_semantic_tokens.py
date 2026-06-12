"""Unit tests for the mforth LSP semantic-tokens handler.

Bead mforth-10t.26 (part 2). Adds a ``textDocument/semanticTokens/full``
capability that classifies every lexeme in a Forth document so the editor
can colorize them:

* **keyword** — control-flow words (``if``/``else``/``then``/``begin``/
  ``until``/``while``/``repeat``/``do``/``loop``) and the ``:`` / ``;``
  definition delimiters.
* **function** — built-in WordCalls and user-defined word calls.
* **variable** — user ``VARIABLE`` references.
* **number** — integer and float literals.
* **string** — ``."`` / ``S"`` string literals.
* **comment** — ``( ... )`` paren comments and ``\\ `` line comments.
* **macro** — ``@``-identifiers (mlog magic vars like ``@tick``,
  ``@counter``) and sidecar-bound link names. (``macro`` is the standard
  LSP semantic-token type closest to "compile-time-resolved constant".)

The encoded wire format is the LSP delta-encoded ``data`` array (five
ints per token: deltaLine, deltaStart, length, tokenType, tokenModifiers).
Tests drive the decode-friendly ``semantic_token_spans`` helper (absolute
``(line, col, length, type_name)`` tuples, 0-based) so assertions read
naturally, plus one test that the encoded ``data`` round-trips through the
legend.

All tests are hermetic — they call the pure functions / registered
handler directly, no live editor.
"""

from __future__ import annotations

import textwrap

import pytest

pytest.importorskip("pygls")
pytest.importorskip("lsprotocol")

from lsprotocol import types as lsp

from mforth.lsp.server import (
    SEMANTIC_TOKEN_LEGEND,
    create_server,
    semantic_token_spans,
    semantic_tokens_for,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _types_at(spans, line, col):
    """Return the set of token type names whose span starts at (line, col)."""
    return {t for (ln, cc, _length, t) in spans if ln == line and cc == col}


def _all_types(spans):
    return {t for (_ln, _cc, _length, t) in spans}


def _span_for(spans, line, col):
    for span in spans:
        if span[0] == line and span[1] == col:
            return span
    return None


# ---------------------------------------------------------------------------
# Layer 1 — pure semantic_token_spans(text)
# ---------------------------------------------------------------------------


def test_integer_literal_classified_as_number():
    spans = semantic_token_spans("42 .\n")
    assert "number" in _types_at(spans, 0, 0)
    span = _span_for(spans, 0, 0)
    assert span[2] == 2  # length of "42"


def test_float_literal_classified_as_number():
    spans = semantic_token_spans("0.95 .\n")
    assert "number" in _types_at(spans, 0, 0)


def test_builtin_word_classified_as_function():
    spans = semantic_token_spans("DUP\n")
    assert "function" in _types_at(spans, 0, 0)


def test_user_word_call_classified_as_function():
    text = ": square dup * ;\n3 square .\n"
    spans = semantic_token_spans(text)
    # `square` call on line 2 (0-based 1), col 2.
    assert "function" in _types_at(spans, 1, 2)


def test_user_variable_reference_classified_as_variable():
    text = "VARIABLE counter\ncounter @ .\n"
    spans = semantic_token_spans(text)
    # `counter` reference on line 2 (0-based 1), col 0.
    assert "variable" in _types_at(spans, 1, 0)


def test_string_literal_classified_as_string():
    spans = semantic_token_spans('." hello"\n')
    assert "string" in _types_at(spans, 0, 0)


def test_s_quote_string_classified_as_string():
    spans = semantic_token_spans('S" hello"\n')
    assert "string" in _types_at(spans, 0, 0)


def test_paren_comment_classified_as_comment():
    spans = semantic_token_spans("DUP ( a comment ) DROP\n")
    # The comment starts at col 4.
    assert "comment" in _types_at(spans, 0, 4)


def test_line_comment_classified_as_comment():
    spans = semantic_token_spans("DUP \\ trailing comment\n")
    assert "comment" in _types_at(spans, 0, 4)


def test_colon_and_semicolon_classified_as_keyword():
    text = ": square dup * ;\n"
    spans = semantic_token_spans(text)
    # `:` at col 0, `;` at col 15.
    assert "keyword" in _types_at(spans, 0, 0)
    assert "keyword" in _types_at(spans, 0, 15)


def test_control_flow_words_classified_as_keyword():
    text = ": f if dup then ;\n"
    spans = semantic_token_spans(text)
    # `:`0 space1 `f`2 space3 `if`4-5 space6 `dup`7-9 space10 `then`11-14.
    assert "keyword" in _types_at(spans, 0, 4)
    assert "keyword" in _types_at(spans, 0, 11)


def test_at_identifier_classified_as_macro():
    text = "@tick PRINT\n"
    spans = semantic_token_spans(text)
    assert "macro" in _types_at(spans, 0, 0)


def test_empty_document_has_no_spans():
    assert semantic_token_spans("") == []


def test_spans_do_not_overlap_and_are_ordered():
    text = ": square dup * ;\n3 square .\n"
    spans = semantic_token_spans(text)
    # Sorted by (line, col) and non-overlapping.
    flat = sorted(spans, key=lambda s: (s[0], s[1]))
    assert spans == flat, "spans must be returned in document order"
    for a, b in zip(spans, spans[1:]):
        if a[0] == b[0]:
            assert a[1] + a[2] <= b[1], f"overlapping spans {a} and {b}"


# ---------------------------------------------------------------------------
# Layer 1b — encoded SemanticTokens data round-trips through the legend
# ---------------------------------------------------------------------------


def test_semantic_tokens_for_returns_delta_encoded_data():
    text = "42 DUP\n"
    tokens = semantic_tokens_for(text, uri="file:///tmp/x.fs")
    assert isinstance(tokens, lsp.SemanticTokens)
    data = tokens.data
    # Five ints per token.
    assert len(data) % 5 == 0
    assert len(data) >= 10  # at least two tokens (42, DUP)

    # Decode the delta encoding and compare to the absolute spans.
    legend = SEMANTIC_TOKEN_LEGEND.token_types
    decoded = []
    line = 0
    col = 0
    for i in range(0, len(data), 5):
        d_line, d_start, length, type_idx, _mods = data[i : i + 5]
        if d_line == 0:
            col += d_start
        else:
            line += d_line
            col = d_start
        decoded.append((line, col, length, legend[type_idx]))

    spans = semantic_token_spans(text)
    assert decoded == spans


def test_legend_contains_every_emitted_type():
    """Every type name produced by the tokenizer must be in the legend, or
    the index lookup at encode time would be out of range."""
    text = textwrap.dedent(
        """\
        : square ( a comment ) dup * ;
        VARIABLE counter
        42 0.5 counter @ square @tick ." hi" \\ done
        """
    )
    spans = semantic_token_spans(text)
    legend_types = set(SEMANTIC_TOKEN_LEGEND.token_types)
    for _l, _c, _length, type_name in spans:
        assert type_name in legend_types, f"{type_name} missing from legend"


# ---------------------------------------------------------------------------
# Layer 2 — pygls server registers the semantic-tokens handler
# ---------------------------------------------------------------------------


def test_create_server_registers_semantic_tokens():
    server = create_server()
    registered = set(server.protocol.fm.features.keys())
    assert lsp.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL in registered


# ---------------------------------------------------------------------------
# Layer 3 — driving the handler matches the pure function
# ---------------------------------------------------------------------------


def _invoke(handler, server, params):
    try:
        return handler(server, params)
    except TypeError:
        return handler(params)


def test_semantic_tokens_handler_matches_pure_function(monkeypatch):
    server = create_server()
    open_handler = server.protocol.fm.features[lsp.TEXT_DOCUMENT_DID_OPEN]
    st_handler = server.protocol.fm.features[
        lsp.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL
    ]

    uri = "file:///tmp/handler.fs"
    text = "42 DUP\n"

    monkeypatch.setattr(
        server, "text_document_publish_diagnostics", lambda _params: None
    )

    open_params = lsp.DidOpenTextDocumentParams(
        text_document=lsp.TextDocumentItem(
            uri=uri, language_id="forth", version=1, text=text
        )
    )
    _invoke(open_handler, server, open_params)

    st_params = lsp.SemanticTokensParams(
        text_document=lsp.TextDocumentIdentifier(uri=uri),
    )
    result = _invoke(st_handler, server, st_params)
    assert isinstance(result, lsp.SemanticTokens)
    expected = semantic_tokens_for(text, uri=uri)
    assert result.data == expected.data


def test_semantic_tokens_handler_returns_empty_for_unknown_document():
    server = create_server()
    st_handler = server.protocol.fm.features[
        lsp.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL
    ]
    st_params = lsp.SemanticTokensParams(
        text_document=lsp.TextDocumentIdentifier(uri="file:///nowhere.fs"),
    )
    result = _invoke(st_handler, server, st_params)
    # Unknown doc → empty token set (data == []).
    assert isinstance(result, lsp.SemanticTokens)
    assert result.data == []
