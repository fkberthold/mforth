"""mforth language server package.

Bead mforth-10t.23. Houses the pygls-based stdio language server
(`mforth lsp`) plus the analyzer entry points that share the
compiler's lex / parse / resolve / stackcheck pipeline.

The package is intentionally split across three modules so the test
surface stays small and so future LSP capability beads (.24 hover,
.25 completion, .26 definition + sidecar watcher) can extend without
touching CLI plumbing:

* :mod:`mforth.lsp.server`  — pure analyzer (`analyze_document`,
  `analyze_sidecar`) plus the pygls server factory `create_server`.
* :mod:`mforth.lsp.cli_subcommand` — registers the `lsp` subcommand
  on the shared `mforth.cli` registry (see drawer
  ``drawer_mforth_decisions_85f5383552bbb0d611c8c989`` for the
  registry pattern).
* :mod:`mforth.lsp.__main__` — `python -m mforth.lsp` runs the
  stdio server, mirroring the package-level `python -m mforth`
  pattern from mforth-326.
"""
