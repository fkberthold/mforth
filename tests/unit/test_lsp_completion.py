"""Unit tests for the mforth LSP completion handler.

Bead mforth-10t.25. Builds on the ``analyze_document`` contract laid down
in bead .23 (drawer ``drawer_mforth_decisions_71c0ea542686301ac4535cd5``)
and the ``_term_at_position`` walk in .24 (drawer
``drawer_mforth_decisions_abff7f93b90ef1c8bd793f66``) by adding a
``textDocument/completion`` capability.

Completion sources (per the bead acceptance):

1. **Built-ins** — every entry in ``standard_dictionary()``. Surface name,
   stack effect, and doc. CompletionItemKind = ``Function``.
2. **User-defined words** — every ``: name ... ;`` definition whose
   declaration position is BEFORE the cursor. Forth's source-order rule:
   a word is only callable after its ``;``. CompletionItemKind =
   ``Function``.
3. **User variables** — every ``VARIABLE name`` declaration before the
   cursor. CompletionItemKind = ``Variable``.
4. **Sidecar link names** — any ``[links.<name>]`` entry in a sibling
   ``<stem>.world.toml`` file. CompletionItemKind = ``Constant``.

Context restrictions:

* Inside a ``."`` / ``S"`` string literal: empty completions.
* Inside a ``( ... )`` paren comment or after ``\\ `` on the current line:
  empty completions.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

pytest.importorskip("pygls")
pytest.importorskip("lsprotocol")

from lsprotocol import types as lsp

from mforth.lsp.server import (
    completions_for,
    create_server,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pos(line: int, character: int) -> lsp.Position:
    return lsp.Position(line=line, character=character)


def _labels(items) -> set[str]:
    return {it.label for it in items}


def _by_label(items, label: str) -> lsp.CompletionItem | None:
    for it in items:
        if it.label == label:
            return it
    return None


def _doc_text(doc) -> str:
    if doc is None:
        return ""
    if isinstance(doc, str):
        return doc
    return getattr(doc, "value", "")


# ---------------------------------------------------------------------------
# Layer 1 — pure completions_for(text, uri, position)
# ---------------------------------------------------------------------------


def test_completions_include_all_builtins_in_empty_document():
    """Acceptance from bead: 'D' partial → ['DUP', 'DROP', 'DO', ...]
    (client-side filters by prefix; the server returns all candidates)."""
    items = completions_for("", uri="file:///tmp/c.fs", position=_pos(0, 0))
    labels = _labels(items)
    # A representative sample of every built-in family.
    for word in ("DUP", "DROP", "SWAP", "+", "-", "PRINT", "SENSOR", "@", "!"):
        assert word in labels, f"missing built-in '{word}' in completions"


def test_builtin_completion_item_has_stack_effect_and_doc():
    items = completions_for("", uri="file:///tmp/c.fs", position=_pos(0, 0))
    dup = _by_label(items, "DUP")
    assert dup is not None
    assert dup.kind == lsp.CompletionItemKind.Function
    # Detail carries the arity notation.
    assert dup.detail is not None
    assert "--" in dup.detail
    # Documentation carries the dictionary doc string.
    doc_text = _doc_text(dup.documentation)
    assert "duplicate" in doc_text.lower()


def test_user_defined_word_appears_after_definition_closes():
    """Acceptance from bead: user word 'square' becomes available after
    its definition. Source-order rule: only words declared BEFORE the
    cursor are completable."""
    text = textwrap.dedent(
        """\
        : square dup * ;
        3 square .
        """
    )
    # Cursor on line 2 (LSP line 1), at end of leading whitespace before
    # `square`. `square` is defined on line 1, so it should appear.
    items = completions_for(text, uri="file:///tmp/sq.fs", position=_pos(1, 2))
    labels = _labels(items)
    assert "square" in labels, "user word not surfaced after its definition"

    sq = _by_label(items, "square")
    assert sq.kind == lsp.CompletionItemKind.Function


def test_user_word_not_visible_before_its_definition():
    """A word defined later in the file must NOT appear in completions
    at an earlier cursor position. Forth source-order."""
    text = textwrap.dedent(
        """\
        3 .
        : square dup * ;
        """
    )
    # Cursor on line 1 (LSP line 0), col 0 — BEFORE the definition.
    items = completions_for(text, uri="file:///tmp/sq2.fs", position=_pos(0, 0))
    labels = _labels(items)
    assert "square" not in labels, (
        "word defined later in file leaked into earlier-cursor completions"
    )


def test_user_word_not_visible_inside_its_own_body():
    """mforth v1 disallows recursion (per bead .7). A word should NOT
    appear as a completion candidate inside its own definition body —
    Forth's source-order rule is at definition CLOSE, not open."""
    text = textwrap.dedent(
        """\
        : square dup * ;
        """
    )
    # Cursor on line 1 (LSP line 0), middle of the body (after `dup `).
    items = completions_for(text, uri="file:///tmp/sq3.fs", position=_pos(0, 13))
    labels = _labels(items)
    assert "square" not in labels, (
        "word leaked into its own body (recursion disallowed in v1)"
    )


def test_user_variable_appears_after_declaration():
    text = textwrap.dedent(
        """\
        VARIABLE counter
        counter @ .
        """
    )
    # Cursor on line 2 (LSP line 1), col 0.
    items = completions_for(text, uri="file:///tmp/var.fs", position=_pos(1, 0))
    counter = _by_label(items, "counter")
    assert counter is not None, "user variable not surfaced after declaration"
    assert counter.kind == lsp.CompletionItemKind.Variable


def test_user_variable_not_visible_before_declaration():
    text = textwrap.dedent(
        """\
        1 .
        VARIABLE counter
        """
    )
    items = completions_for(text, uri="file:///tmp/var2.fs", position=_pos(0, 0))
    labels = _labels(items)
    assert "counter" not in labels


def test_completion_inside_string_literal_returns_empty():
    """Inside a `." ..."` string literal completion should be silent."""
    text = '." hello world"\n'
    # Cursor inside the string body, at char 5 (between "hel" and "lo").
    items = completions_for(text, uri="file:///tmp/s.fs", position=_pos(0, 5))
    assert items == [], "completion fired inside a string literal"


def test_completion_inside_s_quote_string_returns_empty():
    text = 'S" hello"\n'
    items = completions_for(text, uri="file:///tmp/s2.fs", position=_pos(0, 4))
    assert items == [], "completion fired inside an S\" string"


def test_completion_inside_paren_comment_returns_empty():
    text = "DUP ( unfinished comment\n"
    # Cursor inside the comment, after `( un`.
    items = completions_for(text, uri="file:///tmp/com.fs", position=_pos(0, 10))
    assert items == [], "completion fired inside a paren comment"


def test_completion_after_backslash_line_comment_returns_empty():
    text = "DUP \\ end-of-line comment\n"
    # Cursor inside the line comment.
    items = completions_for(text, uri="file:///tmp/lc.fs", position=_pos(0, 15))
    assert items == [], "completion fired inside a \\ line comment"


def test_completion_after_paren_comment_resumes():
    """Once the `)` closes a paren comment, completion fires again."""
    text = "( a comment ) \n"
    # Cursor after the closing paren + space.
    items = completions_for(text, uri="file:///tmp/ac.fs", position=_pos(0, 14))
    labels = _labels(items)
    # Built-ins should be back.
    assert "DUP" in labels


def test_completion_on_broken_document_still_surfaces_builtins():
    """If the document fails to parse, completion should still surface
    built-ins — the user is typing and the in-flight text is often
    invalid. Mirrors .24's NON-fatal stackcheck decision: hover/
    completion are TEACHING surfaces and should degrade gracefully."""
    text = ": broken\n"  # missing `;`
    items = completions_for(text, uri="file:///tmp/b.fs", position=_pos(0, 5))
    labels = _labels(items)
    assert "DUP" in labels


# ---------------------------------------------------------------------------
# Sidecar link completion
# ---------------------------------------------------------------------------


def test_sidecar_link_names_surfaced_when_sibling_world_toml_exists(tmp_path):
    """The bead requires link names to be completable for SENSOR /
    PRINTFLUSH arguments."""
    fs_file = tmp_path / "blink.fs"
    fs_file.write_text("PRINT\n")
    sidecar = tmp_path / "blink.world.toml"
    sidecar.write_text(
        textwrap.dedent(
            """\
            [links.message1]
            type = "message"
            target = "msg1"

            [links.switch1]
            type = "switch"
            target = "sw1"
            """
        )
    )

    uri = fs_file.as_uri()
    items = completions_for("PRINT\n", uri=uri, position=_pos(0, 0))
    labels = _labels(items)
    assert "message1" in labels
    assert "switch1" in labels

    msg = _by_label(items, "message1")
    assert msg.kind == lsp.CompletionItemKind.Constant


def test_no_sidecar_means_no_link_completions(tmp_path):
    fs_file = tmp_path / "noworld.fs"
    fs_file.write_text("PRINT\n")
    # Note: no `.world.toml` sibling.
    uri = fs_file.as_uri()
    items = completions_for("PRINT\n", uri=uri, position=_pos(0, 0))
    labels = _labels(items)
    assert "DUP" in labels
    # No spurious link-name completions.
    assert "message1" not in labels


def test_broken_sidecar_does_not_crash_completion(tmp_path):
    """A malformed sibling sidecar should not break Forth completion —
    we just skip its link entries."""
    fs_file = tmp_path / "broken.fs"
    fs_file.write_text("PRINT\n")
    sidecar = tmp_path / "broken.world.toml"
    sidecar.write_text("not = valid toml [[[\n")

    uri = fs_file.as_uri()
    items = completions_for("PRINT\n", uri=uri, position=_pos(0, 0))
    labels = _labels(items)
    # Built-ins still flow through.
    assert "DUP" in labels


# ---------------------------------------------------------------------------
# Layer 2 — pygls server registers the completion handler
# ---------------------------------------------------------------------------


def test_create_server_registers_completion():
    server = create_server()
    registered = set(server.protocol.fm.features.keys())
    assert lsp.TEXT_DOCUMENT_COMPLETION in registered


# ---------------------------------------------------------------------------
# Layer 3 — driving the handler returns items matching completions_for()
# ---------------------------------------------------------------------------


def _invoke(handler, server, params):
    try:
        return handler(server, params)
    except TypeError:
        return handler(params)


def _result_items(result):
    if result is None:
        return []
    if isinstance(result, lsp.CompletionList):
        return result.items
    return result


def test_completion_handler_returns_items_matching_pure_function(monkeypatch):
    server = create_server()

    open_handler = server.protocol.fm.features[lsp.TEXT_DOCUMENT_DID_OPEN]
    completion_handler = server.protocol.fm.features[
        lsp.TEXT_DOCUMENT_COMPLETION
    ]

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

    completion_params = lsp.CompletionParams(
        text_document=lsp.TextDocumentIdentifier(uri=uri),
        position=_pos(0, 0),
    )
    result = _invoke(completion_handler, server, completion_params)
    items = _result_items(result)
    expected = completions_for(text, uri=uri, position=_pos(0, 0))
    assert _labels(items) == _labels(expected)


def test_completion_handler_returns_empty_for_unknown_document():
    server = create_server()
    completion_handler = server.protocol.fm.features[
        lsp.TEXT_DOCUMENT_COMPLETION
    ]
    completion_params = lsp.CompletionParams(
        text_document=lsp.TextDocumentIdentifier(uri="file:///nowhere.fs"),
        position=_pos(0, 0),
    )
    result = _invoke(completion_handler, server, completion_params)
    items = _result_items(result)
    assert not items


def test_completion_handler_uses_sidecar_from_filesystem(tmp_path, monkeypatch):
    """End-to-end: a `.world.toml` next to the served `.fs` flows
    through the server's completion handler."""
    fs_file = tmp_path / "live.fs"
    fs_file.write_text("PRINT\n")
    sidecar = tmp_path / "live.world.toml"
    sidecar.write_text(
        textwrap.dedent(
            """\
            [links.cell1]
            type = "memory-cell"
            target = "mem1"
            size = 64
            """
        )
    )

    server = create_server()
    open_handler = server.protocol.fm.features[lsp.TEXT_DOCUMENT_DID_OPEN]
    completion_handler = server.protocol.fm.features[
        lsp.TEXT_DOCUMENT_COMPLETION
    ]
    monkeypatch.setattr(
        server, "text_document_publish_diagnostics", lambda _params: None
    )

    uri = fs_file.as_uri()
    open_params = lsp.DidOpenTextDocumentParams(
        text_document=lsp.TextDocumentItem(
            uri=uri, language_id="forth", version=1, text="PRINT\n"
        )
    )
    _invoke(open_handler, server, open_params)

    completion_params = lsp.CompletionParams(
        text_document=lsp.TextDocumentIdentifier(uri=uri),
        position=_pos(0, 0),
    )
    result = _invoke(completion_handler, server, completion_params)
    items = _result_items(result)
    labels = _labels(items)
    assert "cell1" in labels, "link from sidecar didn't reach completion handler"
