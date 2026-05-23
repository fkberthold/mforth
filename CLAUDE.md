# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

## What mforth is

A pragmatic Forth dialect that compiles to **mlog** — the bytecode for Mindustry's in-game logic processors. Two backends share one parser, AST, dictionary, and stack-checker:

- **Host REPL** — `mforth repl` / `mforth run example.fs`. Executes Forth against a Python simulation of the Mindustry environment (MockWorld + EventStream).
- **AOT compiler** — `mforth compile example.fs -o example.mlog`. Emits paste-ready mlog text.

Plus a web visualizer (`--serve` mode), an LSP for Helix, and a tree-sitter grammar — all four ship in v1 because they share infrastructure.

**Implementation language:** Python (keeps the host language invisible so the user can focus on Forth + mlog).

## START HERE (orientation)

If you are picking up mforth cold, read in order:

1. **MemPalace drawer `drawer_mforth_decisions_3827fd238edc64f763e7b96b`** — design v1, five sections (surfaces, compiler pipeline, REPL+MockWorld, web viz + LSP, layout + testing). The locked design.
2. **MemPalace drawer `drawer_mforth_references_96affa82d1ff0917a17bdbaf`** — mlog reference facts (instruction set, `@counter` writability, `@ipt`, auto-loop semantics, number representation, jump model). The authoritative compile target.
3. **MemPalace drawer `drawer_mforth_decisions_d8910d0d11f2ce62c712c2ab`** — v2 optimization roadmap, tiered by **fast > small** priority.
4. **`bd memories mforth`** — six tribal-knowledge one-liners injected at `bd prime`: `mforth-what`, `mforth-mlog-counter-trick`, `mforth-dialect`, `mforth-sidecar`, `mforth-v1-demo`, `mforth-palace-pointers`, `mforth-opt-priority`.
5. **`bd ready`** — current unblocked work.
6. **`bd dep tree mforth-10t`** — the dependency graph for the umbrella epic.

Drawers and KG facts live in MemPalace wing `mforth`. Search via `mempalace_search` with `wing=mforth`.

## How we work (the three pillars)

These are not optional. Even 1% chance a skill applies → invoke it.

- **MemPalace** — substantive decisions, findings, and design writeups go to `mforth` wing drawers. Bar for filing: "would a future agent benefit from finding this via semantic search?" `docs/` is a navigable surface that points back at MemPalace; the palace is the primary capture.
- **Beads** — ALL task tracking via `bd`. Create the issue before writing code; claim with `bd update <id> --claim`; close with `bd close <id>`. `bd remember "..."` for persistent insights. Never use TodoWrite, TaskCreate, or markdown TODO lists.
- **Superpowers + beadpowers** — workflow skills carry the procedural discipline. Design/planning: `beadpowers:brainstorming` → `beadpowers:create-beads`. Implementation: `superpowers:test-driven-development`, `superpowers:systematic-debugging`, `superpowers:dispatching-parallel-agents`, `superpowers:using-git-worktrees`. Wrapping up: `superpowers:requesting-code-review`, `superpowers:verification-before-completion`, `superpowers:finishing-a-development-branch`.

## Hard rules for mforth

- **REPL ↔ mlog equivalence is the headline test class.** Same `.fs` source must produce the same observable events when run via the host REPL and when compiled-then-executed via the in-repo mlog interpreter (`mforth-10t.31`). A divergence is the highest-severity regression — the REPL is the teaching surface; if it diverges from compiled output, mforth has failed as a teaching tool. Every Mindustry primitive ships with an equivalence fixture pair.
- **Static stack analysis is mandatory.** Every Forth word has a statically-known stack effect. Branches produce the same depth on both sides; loops are stack-neutral. This is the gate for codegen slot assignment AND the source of LSP diagnostics. The pragmatic-Forth dialect (no `POSTPONE` / `IMMEDIATE` / `DOES>` / `EXECUTE` in v1) makes this enforceable.
- **Optimization priority: fast > small.** Often related, not always. Default `-Ofast` (Tier A + Tier B passes). Subroutine emission via the `@counter` trick (`mforth-10t.39`) is a Tier C **size-only fallback**, opt-in via `-Osize` OR auto-triggered when inline-everything exceeds the per-processor instruction budget. Inlining wins on speed always.
- **Sidecar `.world.toml` indirection.** Left side of `=` is the stable mforth name (what `.fs` source references). Right side is `target = "<in-game-name>"` (default, recommended) OR `index = N` (opt-in, fragile to re-link order). Parser errors on both or neither. Tutorials use `target`; `index` gets its own how-to with a tradeoff warning.
- **v1 stays cell-free.** Data stack lives in mlog variables (`s0..sN`); no return stack (inline everything); user `VARIABLE` compiles to bare mlog variables. v1 demos (blink, counter, single-processor controllers) never touch a memory cell. Cells re-enter only for v2 + recursion (not in our dialect anyway), inter-processor IPC, large lookup tables, or persistence across processor disable.
- **`@counter` is writable in mlog.** This is the lever for v2 subroutine emission (caller saves return address; sets `@counter` to entry; callee ends with `set @counter <return-addr-var>`). Also enables jump-table dispatch via `op add @counter @counter <offset>`. Don't forget this when reading the mlog reference drawer.

### REPL ↔ mlog convergence decisions (mforth-2i1, 2026-05-23)

Three deliberate divergence-resolution choices that keep the headline equivalence property holding. All three are pinned by unit tests + equivalence fixtures; any future regression flips the equivalence-on-demos tests in `tests/integration/test_blink_counter.py`.

- **Forth `/` emits mlog `op div` (float division), NOT `op idiv`.** The host REPL primitive uses Python's `/` (float division); the mlog backend now matches. Forth tradition prefers integer `/`; mforth's pragmatic dialect explicitly chooses the Python-natural feel of the REPL over Forth tradition. See `src/mforth/backend/mlog/emit.py` (`_BINARY_OP_MAP["/"]`) and bead `mforth-dlr`.
- **mlog interpreter emits `VariableReadEvent` / `VariableWriteEvent` for `UserVariable` reads/writes.** Matches the REPL's `world.read_variable` / `world.write_variable` instrumentation. The interpreter takes a `user_variables: set[str]` constructor parameter — names of source-declared `VARIABLE foo` (NOT sidecar-pre-seeded link names, which are block-name handles the REPL never instruments). Compiler-internal names (`s<i>` stack slots, `__swap_tmp`, `@`-prefixed magic vars) bypass instrumentation. See `src/mforth/mlog_interp.py` (`_read` / `_write`) and bead `mforth-0qi`.
- **PRINT renders integer-valued floats WITHOUT a trailing `.0`.** Matches the in-game `print` instruction's stringification rule (whole-number doubles render as integers). The host PRINT primitive applies `str(int(x)) if isinstance(x, float) and x.is_integer() else str(x)` — same rule the mlog interpreter already used in `_format_for_print`. See `src/mforth/backend/primitives.py` (`_print`) and bead `mforth-05h`.

## Worktree default

Non-trivial work happens in an isolated git worktree under `.worktrees/<task-id>/`, never directly on `main`. The directory is gitignored. `superpowers:using-git-worktrees` covers the choreography.

## Anti-patterns

- Don't file substantive decisions only to bd memories — substantive decisions go to BOTH bd memories (one-liner, auto-injected) AND MemPalace drawers (full content).
- Don't write design notes to markdown files without also filing to MemPalace; the doc tree is reference, not primary capture.
- Don't use TodoWrite, TaskCreate, or markdown TODO lists for task tracking. Use `bd`.
- Don't work on `main` directly for anything non-trivial — use a worktree.
- Don't add a v2 optimization pass without an equivalence fixture that exercises the optimized path. The REPL ↔ mlog property is non-negotiable.
- Don't reach for a memory cell in v1 codegen. Static stack slots + inline everything is the strategy.
- Don't break out into ANS Forth features (`POSTPONE`, `IMMEDIATE`, `DOES>`, `EXECUTE`) without filing a new epic. The pragmatic-Forth dialect choice is load-bearing for static stack analysis.

## Authoritative design content (where to find what)

- **Locked design:** MemPalace `mforth/decisions` drawers
  - `drawer_mforth_decisions_3827fd238edc64f763e7b96b` — design v1 (five sections)
  - `drawer_mforth_decisions_d8910d0d11f2ce62c712c2ab` — v2 optimization roadmap (tiered, fast > small)
- **mlog reference:** MemPalace `mforth/references` drawer
  - `drawer_mforth_references_96affa82d1ff0917a17bdbaf` — instruction set, `@counter` writability, `@ipt` numbers, auto-loop semantics, number representation, jump model, gotchas
- **bd memories:** seven keys (`bd memories mforth` to list)
  - `mforth-what`, `mforth-mlog-counter-trick`, `mforth-dialect`, `mforth-sidecar`, `mforth-v1-demo`, `mforth-palace-pointers`, `mforth-opt-priority`
- **bd umbrella epic:** `mforth-10t`. Run `bd ready` for unblocked work; `bd dep tree mforth-10t` for the graph. 32 v1 child tasks (A compiler core, B host REPL, C mlog backend, D web viz, E LSP, F tree-sitter + Helix, G testing, H examples, I loom bootstrap) + 8 v2 optimization beads (P3, blocked on `mforth-10t.19` until v1 codegen exists).
- **KG facts:** query via `mempalace_kg_query` for triples about `mforth`, `mforth REPL`, `mforth codegen v1`, `mforth optimization priority`, `mforth v1 compiled output`, `mforth v1 demo`, `@counter`. Anchors lineage from facts back to drawers.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->

> **Note on the push step:** `origin` is `github.com:fkberthold/mforth` (SSH, public) as of 2026-05-23. `git push` is real and required for session close. `bd dolt push` remains a no-op until a dolt-sync remote is configured separately.

## Build & Test

Python project, planned layout:

```bash
# (not yet bootstrapped — see bead mforth-10t.4 for lexer scaffold + pyproject.toml)
pip install -e .            # editable install
pytest -q                   # all tests
pytest tests/unit -q        # fast unit tests only
pytest tests/golden -q      # golden mlog comparisons
pytest tests/integration -q # REPL × MockWorld + equivalence property
mforth run examples/blink.fs --serve   # run with web viz
mforth lsp                  # stdio LSP for Helix
```

Dependencies (planned, minimal): `tomllib` (stdlib in Python 3.11+), `pygls` (LSP), `pytest`, `websockets` (or stdlib `http.server` only — TBD at viz-server bead claim).

## Architecture Overview

See MemPalace drawer `drawer_mforth_decisions_3827fd238edc64f763e7b96b` for the locked design (five sections). Capsule:

```
.fs source
    ↓
[lex]   tokens with (file, line, col)
[parse] AST: Definitions + main = [Term]
[resolve] dictionary lookups attached to WordCalls
[stackcheck] annotated AST + per-word stack effect (mandatory gate)
    ↓
backend = one of:
  - host  → walk + execute against MockWorld + EventStream
  - mlog  → walk + emit mlog with static stack slots (s0..sN)
```

The events stream is the seam where the web viz, LSP runtime diagnostics, and integration tests all plug in. Same code; different subscribers.
