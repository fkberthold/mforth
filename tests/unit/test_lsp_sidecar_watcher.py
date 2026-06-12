"""Unit tests for the mforth LSP sidecar (.world.toml) watcher.

Bead mforth-10t.26 (part 3). When a ``<stem>.world.toml`` sidecar is
edited, the served ``<stem>.fs`` Forth document's diagnostics can change
(a name that previously resolved via ``[links.foo]`` may now be
unresolved, or vice-versa). The watcher refreshes the affected Forth
document's diagnostics when its sidecar changes.

The server handles ``workspace/didChangeWatchedFiles`` notifications:
for every changed ``*.world.toml`` URI, it locates the open sibling
``*.fs`` documents in its document cache, re-runs ``analyze_document``,
and republishes their diagnostics.

Tests drive the registered handler directly and capture the republished
diagnostics — hermetic, no real file-watcher / editor.
"""

from __future__ import annotations

import textwrap

import pytest

pytest.importorskip("pygls")
pytest.importorskip("lsprotocol")

from lsprotocol import types as lsp

from mforth.lsp.server import create_server


def _invoke(handler, server, params):
    try:
        return handler(server, params)
    except TypeError:
        return handler(params)


def test_create_server_registers_watched_files_handler():
    server = create_server()
    registered = set(server.protocol.fm.features.keys())
    assert lsp.WORKSPACE_DID_CHANGE_WATCHED_FILES in registered


def test_sidecar_change_republishes_forth_diagnostics(tmp_path, monkeypatch):
    """Editing the sidecar to ADD a link makes a previously-unresolved
    name resolve — the Forth doc's diagnostics must refresh to empty."""
    fs_file = tmp_path / "blink.fs"
    fs_file.write_text("display PRINTFLUSH\n")
    sidecar = tmp_path / "blink.world.toml"
    # Initially: NO links → `display` is unresolved.
    sidecar.write_text("")

    server = create_server()
    open_handler = server.protocol.fm.features[lsp.TEXT_DOCUMENT_DID_OPEN]
    watch_handler = server.protocol.fm.features[
        lsp.WORKSPACE_DID_CHANGE_WATCHED_FILES
    ]

    published: list[lsp.PublishDiagnosticsParams] = []
    monkeypatch.setattr(
        server,
        "text_document_publish_diagnostics",
        lambda params: published.append(params),
    )

    fs_uri = fs_file.as_uri()
    open_params = lsp.DidOpenTextDocumentParams(
        text_document=lsp.TextDocumentItem(
            uri=fs_uri, language_id="forth", version=1, text="display PRINTFLUSH\n"
        )
    )
    _invoke(open_handler, server, open_params)

    # On open, `display` is unresolved → one diagnostic.
    assert published, "did_open should have published diagnostics"
    assert len(published[-1].diagnostics) == 1
    assert published[-1].uri == fs_uri

    # Now the user edits the sidecar to declare the link on disk.
    sidecar.write_text(
        textwrap.dedent(
            """\
            [links.display]
            type = "message"
            target = "message1"
            """
        )
    )

    published.clear()
    watch_params = lsp.DidChangeWatchedFilesParams(
        changes=[
            lsp.FileEvent(uri=sidecar.as_uri(), type=lsp.FileChangeType.Changed)
        ]
    )
    _invoke(watch_handler, server, watch_params)

    # The Forth doc should have been re-validated and republished, now clean.
    refreshed = [p for p in published if p.uri == fs_uri]
    assert refreshed, "sidecar change did not republish the Forth doc"
    assert refreshed[-1].diagnostics == [], (
        "adding the link to the sidecar should clear the unresolved-word diag"
    )


def test_sidecar_change_for_unopened_forth_doc_is_noop(tmp_path, monkeypatch):
    """A sidecar change with no matching open `.fs` doc must not crash and
    must not publish anything."""
    sidecar = tmp_path / "orphan.world.toml"
    sidecar.write_text("[links.x]\ntype = \"message\"\ntarget = \"m\"\n")

    server = create_server()
    watch_handler = server.protocol.fm.features[
        lsp.WORKSPACE_DID_CHANGE_WATCHED_FILES
    ]
    published: list[lsp.PublishDiagnosticsParams] = []
    monkeypatch.setattr(
        server,
        "text_document_publish_diagnostics",
        lambda params: published.append(params),
    )

    watch_params = lsp.DidChangeWatchedFilesParams(
        changes=[
            lsp.FileEvent(uri=sidecar.as_uri(), type=lsp.FileChangeType.Changed)
        ]
    )
    _invoke(watch_handler, server, watch_params)
    assert published == []
