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
# confirmation; the prose body below is the authored rationale.

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
# Enforced below: Forth `/` must lower to mlog `op div` (float), never
# `op idiv` (integer) — the REPL↔mlog equivalence decision (mforth-dlr).
#
# A second STRONG candidate remains unenforced (left documented, not
# wired): "v1 stays cell-free" — v1 codegen must not emit mlog
# memory-cell reads/writes (s<N> stack slots + bare variables only).
# Wire it the same way once its deny_pattern + scope are decided.

invariants:
  - id: slash-emits-op-div
    applies_to: [Edit, Write]
    deny_pattern: '["''`]/["''`]\s*:\s*["'']idiv\b'
    message: >-
      Forth `/` must lower to mlog `op div` (float division), NOT `op idiv`
      (integer). The host REPL primitive uses Python's float `/`; emitting
      `idiv` re-introduces the REPL↔mlog divergence that bead mforth-dlr
      (the 2026-05-23 REPL↔mlog convergence decision) deliberately closed.
      Keep `_BINARY_OP_MAP["/"] = "div"` in src/mforth/backend/mlog/emit.py.
      See CLAUDE.md "REPL ↔ mlog convergence decisions".
---

# mforth — project constitution

This constitution pins the tooling profile and the load-bearing
architectural decisions for mforth. It is read by AI coding agents (via
the loom `constitution-enforce` hook and at session-startup) and by
humans onboarding to the project. mforth is a *dual-surface* compiler: a
pragmatic Forth dialect that runs against a Python simulation of the
Mindustry world through the **host REPL**, and compiles ahead-of-time to
**mlog** — the bytecode of Mindustry's in-game logic processors. Both
surfaces share one parser, AST, dictionary, and stack-checker. The
headline property this whole project exists to protect is **REPL↔mlog
equivalence**: the same `.fs` source must produce the same observable
events whether interpreted by the REPL or compiled and run through the
in-repo mlog interpreter. The REPL is the teaching surface; if it
diverges from compiled output, mforth has failed as a teaching tool. The
front-matter above and the rules below exist to keep that property —
and the conventions that make it enforceable — from quietly eroding.

## Tooling choices

The front-matter values capture *why* mforth is built the way it is, not
just *what* it uses.

- **Shell**: empty (`enter`/`run_prefix` both `""`). mforth has no shell
  wrapper — no devbox, no nix, no `.tool-versions`. A plain Python venv
  is the only envelope. There is deliberately nothing to wrap a `python`
  invocation in, so the run-prefix rule is a no-op here.
- **Package manager**: `pip` (editable install via the hatchling build
  backend, `pip install -e .[dev]`). There is no lockfile; dependencies
  are pinned by `pyproject.toml` (and a minimal `requirements.txt`).
  Dependencies are kept minimal on purpose — `tomllib` is stdlib on
  3.11+, and the LSP (`pygls`) and viz server are the only non-test
  additions — because the implementation language is meant to stay
  invisible to the user, who should be thinking about Forth and mlog.
- **Language**: `python`, `requires-python >=3.11`. The 3.11 floor buys
  stdlib `tomllib` for parsing the sidecar `.world.toml` link files
  without a third-party TOML dependency. Python was chosen so the host
  language disappears behind the two surfaces the user actually cares
  about; keeping it invisible is itself a design goal.
- **Canonical commands**: `pytest -q` is the test gate, and it is the
  gate that matters most — the REPL↔mlog equivalence fixtures live in
  `tests/integration/` and a divergence there is the highest-severity
  regression. CI additionally runs `pytest --cov --cov-fail-under=85`.
  `gen` regenerates the tree-sitter parser (`cd tree-sitter-mforth &&
  tree-sitter generate`). `lint` is empty — no linter is wired yet, so
  the field is honest about that rather than naming a tool that does not
  run. `dev` launches a demo with the web visualizer
  (`mforth run examples/blink.fs --serve`). `deploy` publishes the
  MkDocs Material docs site via `mkdocs gh-deploy`.

A few orientation facts that frame these choices: v1 is deliberately
**cell-free** — the data stack lives in mlog variables (`s0..sN`), there
is no return stack (everything inlines), and user `VARIABLE`s compile to
bare mlog variables, so v1 demos never touch a memory cell. Optimization
follows a **fast > small** priority; the optimizer (`src/mforth/optimize.py`)
defaults to `OptLevel.O0` as a *library* default (so the strict
teaching-equivalence harness stays byte/event-identical to the REPL),
while the `mforth compile` *CLI* defaults to `-Ofast`.

## Forbidden patterns

`forbidden:` is empty. mforth does not adopt a tooling lock-in posture —
there is exactly one package manager and one runtime, with no competing
tool worth banning, so there are no forbidden command phrases. If a
banned-instruction posture is ever wanted (e.g. forbidding a specific
mlog instruction in generated output, or non-pip installs), each entry
and the failure mode it guards against should be named here. Note that
*architectural* invariants — like the `/`→`op div` rule below — are
enforced through the `invariants:` block, not `forbidden:`; `forbidden:`
is for argv-shaped command phrases, `invariants:` for regex-shaped file
content.

## Bypass patterns

`bypass_patterns:` is empty, which follows from `forbidden:` being empty:
there are no forbidden rules to carve exemptions out of. If `forbidden:`
ever gains entries (for example a non-pip install ban), list here any
command shapes that should be exempt — e.g. read-only `python3 -c`
one-liners used for ad-hoc inspection.

## Invariants

One architectural invariant is wired and live-enforced:

- **`slash-emits-op-div`** — denies any Edit/Write that maps the Forth
  `/` word to mlog `op idiv`. The host REPL's `/` primitive uses Python's
  float division, so the mlog backend must emit `op div` (float) to stay
  event-identical; emitting `idiv` (integer) re-introduces the exact
  divergence bead **mforth-dlr** closed on 2026-05-23. The canonical
  mapping is `_BINARY_OP_MAP["/"] = "div"` in
  `src/mforth/backend/mlog/emit.py`. The deny_pattern matches a quoted
  `/` key lowered to an `idiv` value (and is careful *not* to flag the
  correct `div` value, since `idiv` contains `div` as a substring).

A second strong candidate — **"v1 stays cell-free"** (no mlog memory-cell
codegen in v1) — is documented in the front-matter but intentionally left
unwired until its deny_pattern and scope are pinned down.

## Lineage

The load-bearing tooling and convergence decisions are recorded as
follows:

- **REPL↔mlog convergence decisions** (CLAUDE.md, bead `mforth-2i1`,
  2026-05-23): `mforth-dlr` (`/`→`op div`, the invariant enforced above),
  `mforth-0qi` (UserVariable read/write instrumentation in the mlog
  interpreter), and `mforth-05h` (PRINT renders integer-valued floats
  without a trailing `.0`). All three are pinned by unit tests plus
  equivalence fixtures; a regression flips the equivalence-on-demos tests
  in `tests/integration/test_blink_counter.py`.
- **O0/Ofast default split** (bead `mforth-10t.40`, decision `mforth-ump`
  Option A): see `docs/reference/optimization-levels.md` and
  `src/mforth/optimize.py`.
- **Locked v1 design**: MemPalace drawer
  `drawer_mforth_decisions_3827fd238edc64f763e7b96b` (five sections);
  v2 optimization roadmap in `drawer_mforth_decisions_d8910d0d11f2ce62c712c2ab`;
  mlog reference facts in `drawer_mforth_references_96affa82d1ff0917a17bdbaf`.
