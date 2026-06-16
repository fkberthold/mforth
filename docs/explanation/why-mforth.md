# Why mforth

> **Thesis:** mforth's load-bearing idea is that the host REPL and
> the mlog compiler must remain *behaviorally equivalent* on the
> same source. This page names that idea, traces what it makes
> possible, and acknowledges what it gives up in return.

mforth is not a standalone Forth. It targets [Mindustry's mlog
logic-processor language](https://mindustrygame.github.io/wiki/logic/0-introduction/),
which means everything mforth ships exists in service of one
relationship: the `.fs` source you write in your editor compiles to
two different runtimes that must agree.

## The load-bearing idea

For any `.fs` source, the host REPL (`mforth run example.fs`) and
the AOT mlog compiler (`mforth compile example.fs -o example.mlog`)
must produce the same observable behaviour — the same event
sequence, the same final state, the same printed output. The two
surfaces share one parser, one dictionary, one stack-checker, and
one MockWorld semantics. The compiler emits mlog; the REPL
interprets the same source against a Python simulation. If they
diverge, mforth has failed as a teaching tool, because the REPL is
where you debug and the mlog is what runs in-game.

This is captured in CLAUDE.md as a *hard rule*: "REPL ↔ mlog
equivalence is the headline test class. A divergence is the
highest-severity regression."

## What it makes possible

**A real REPL for a target with no REPL of its own.** mlog runs in
Mindustry's logic processors — there is no `mlog repl`, no
breakpoint, no stack trace. mforth gives you all three on the host,
and guarantees that what you saw at the REPL is what will run
in-game. The REPL *is* the debugger.

**Property-based testing instead of golden files alone.** Every
Mindustry primitive ships with an *equivalence fixture pair*: the
same `.fs`, run on both backends, must emit the same event
sequence. This catches whole categories of bugs (operand ordering,
division semantics, integer-valued-float rendering) that golden mlog
files can't — golden files freeze syntax; equivalence freezes
semantics.

**One analyzer, four surfaces.** The lexer, parser, dictionary, and
stack-checker that the compiler uses are exactly what the LSP runs
for diagnostics, what the tree-sitter grammar shadows for
highlighting, and what the web visualizer subscribes to for runtime
state. Adding the LSP, viz, or grammar didn't require a separate
analyzer — they're all subscribers to the same event stream.

## What it gives up

**ANS Forth's *open-ended* meta-compilation.** Arbitrary `POSTPONE` /
`IMMEDIATE`-style compile-time code, and runtime `EXECUTE`, make static
stack analysis undecidable in general — so mforth drops those. What it
keeps is a deliberately *restricted* compile-time layer: hygienic,
terminating, pure user macros (`MACRO: name … ;`) and defining words
(`CREATE` / `,` / `DOES>`, e.g. a source-defined `CONSTANT`) whose
bodies reduce against compile-time-constant data and **stamp to a bare
literal** — no runtime meta, no cell, still fully statically checkable.
So you *can* extend the compiler at compile time; you just can't do it
in a way that breaks the analysis. See [the meta layer](meta-layer.md)
for the exact re-admission boundary. (Runtime `EXECUTE` / tick is still
out of v1 — it forces a stack-representation migration, held for a
later epic.)

**Memory cells in v1.** mlog has memory blocks; mforth v1 has no
addressable cells. The data stack lives in mlog variables
(`s0..sN`); user `VARIABLE` compiles to a bare mlog variable, not a
cell address. This keeps codegen statically analyzable and the
single-processor demos under the ~1000-instruction lore-cap, but
rules out v1 programs that want random-access tables or
inter-processor IPC. v2 reopens this with an explicit
`--mem=<cell>` flag.

**Subroutines in v1.** Every user-defined word is inlined. The
`@counter`-trick subroutine emission (mlog's writable program
counter; see the project's `mforth-mlog-counter-trick` memory) is
held in reserve as a v2 size-only fallback. The bet: inlining wins
on speed always, and v1's blink/counter demos fit easily without
it.

## How to spot drift

If you find yourself arguing for any of these, the load-bearing
idea is under pressure:

- **"It's fine if the REPL behaves a little differently from the
  compiler."** No — equivalence is the test class. File the
  divergence as a P0 regression.
- **"Let's add open-ended `POSTPONE` / `IMMEDIATE` (or runtime
  `EXECUTE`) just for this one word."** Reach for the *restricted* meta
  layer first — a `MACRO:` or a `CREATE … DOES>` defining word covers
  most needs and stays statically analyzable. *Arbitrary* compile-time
  meta and runtime `EXECUTE` are what v1 rules out; if you truly need
  them, that's an epic, not a one-word exception.
- **"We need a memory cell for this v1 demo."** Re-examine. v1
  demos (blink, counter, single-processor controllers) should never
  touch a cell. If they do, the design is leaking out of scope.
- **"The LSP can have its own diagnostics that aren't what the
  compiler runs."** No — that's the entire point of one analyzer
  feeding both. If a diagnostic fires in only one of the two, find
  out why and unify.
- **"This optimization pass doesn't need an equivalence fixture."**
  Every v2 optimization pass MUST ship an equivalence fixture that
  exercises the optimized path. Without it the REPL ↔ mlog property
  decays silently.

The drift to watch for is *the surfaces diverging*. Not a typo, not
a missing feature — those are bugs of the ordinary kind. The
load-bearing failure is when one surface starts to be "the real
one" and the other becomes a convenience. Both are real; that's
the whole point.

## Cross-references

- [Forth, the mental model](forth-mental-model.md) — the
  Forth-language sub-thesis (composition over abstraction,
  factoring as stack discipline, postfix-shape).
- [Reference](../reference/index.md) — the surface this idea shapes.
- [How-to guides](../how-to/index.md) — the recipes that follow.
- [Tutorials](../tutorials/index.md) — the guided path that
  introduces the shape.
