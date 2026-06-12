---
# Project constitution — mforth (Forth → Mindustry mlog compiler)
#
# mforth is a Python project: a pragmatic Forth dialect with two
# backends (host REPL + mlog AOT compiler) sharing one parser, AST,
# dictionary, and stack-checker. It ships a tree-sitter grammar
# (tree-sitter-mforth/) and a MkDocs Material docs site. Installed
# editable via pip; tested with pytest; no shell wrapper.
#
# Field reference: see loom's docs/reference/project-constitution.md.
# JSON Schema: loom references/project-constitution.schema.json.
# Captured 2026-06-11 by /loom-adopt phase P5 (audit-project
# --check=constitution). Front-matter is detection + per-field user
# confirmation; the prose body below is a [HUMAN AUTHOR] stub.

shell:
  enter: ""
  run_prefix: ""

package_manager: pip

language:
  runtime: python
  version: ">=3.11"

forbidden: []

canonical_commands:
  build: "pip install -e .[dev]"
  test: "pytest -q"
  lint: ""
  gen: "cd tree-sitter-mforth && tree-sitter generate"
  dev: "mforth run examples/blink.fs --serve"
  deploy: "mkdocs gh-deploy"

bypass_patterns: []

# Project-specific ARCHITECTURAL invariants (loom-z3m.14). Enforced by
# hooks/constitution-enforce.sh across Bash AND the write-class tools
# (Edit/Write/MultiEdit). Each entry: {id, applies_to:[Bash|Edit|Write|
# MultiEdit], deny_pattern (regex), message}. A match → the hook exits 2
# with the message.
#
# mforth has STRONG candidate invariants the human may choose to enforce
# (left commented — invariant TEXT is a human authorship decision per
# loom-d50, never agent-drafted):
#   - "v1 stays cell-free": v1 codegen must not emit mlog memory-cell
#     reads/writes (read/write s<N> stack slots + bare variables only).
#   - Forth `/` must emit mlog `op div` (float), NOT `op idiv` — the
#     REPL↔mlog equivalence decision (mforth-dlr).
# Uncomment + adapt below to enforce one once you've decided the
# deny_pattern + scope.
#
# invariants:
#   - id: v1-no-memory-cells
#     applies_to: [Edit, Write]
#     deny_pattern: 'TODO author a precise regex'
#     message: "v1 stays cell-free (see CLAUDE.md): no memory-cell codegen in v1."
---

# mforth — project constitution

> [HUMAN AUTHOR] TODO: One-paragraph statement of what this constitution
> is for and who reads it. Ground it in mforth's dual-surface design
> (host REPL + mlog AOT compiler) and the headline property — REPL↔mlog
> equivalence — that the tooling exists to protect.

## Tooling choices

> [HUMAN AUTHOR] TODO: Briefly explain *why* the front-matter values are
> what they are.

- **Shell**: TODO — mforth has no shell wrapper (no devbox/nix); a plain
  Python venv is the only envelope.
- **Package manager**: TODO — `pip` (editable install via hatchling
  backend). No lockfile; `requirements.txt` + `pyproject.toml` pin deps.
- **Language**: TODO — `python` (`requires-python >=3.11`); the host
  language is kept invisible so the user focuses on Forth + mlog.
- **Canonical commands**: TODO — `pytest -q` is the test gate (CI also
  runs `pytest --cov --cov-fail-under=85`); `gen` regenerates the
  tree-sitter parser; `lint` is empty (no linter wired yet); `deploy`
  publishes the docs site via `mkdocs gh-deploy`.

## Forbidden patterns

> [HUMAN AUTHOR] TODO: `forbidden:` is empty. If mforth adopts a lock-in
> posture (e.g. forbid non-pip installs, or forbid a banned mlog
> instruction in codegen), name each entry and the failure mode it
> guards against here.

## Bypass patterns

> [HUMAN AUTHOR] TODO: `bypass_patterns:` is empty. List any command
> shapes that should be exempt from the forbidden rules above (e.g.
> read-only `python3 -c` one-liners), once `forbidden:` has entries.

## Lineage

> [HUMAN AUTHOR] TODO: Note where the load-bearing tooling decisions are
> recorded — e.g. the REPL↔mlog convergence decisions in CLAUDE.md
> (mforth-dlr `/`→`op div`, mforth-0qi variable instrumentation,
> mforth-05h print formatting) and the v1 design drawer
> drawer_mforth_decisions_3827fd238edc64f763e7b96b.
