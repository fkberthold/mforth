# The meta layer

> **Thesis:** mforth re-admits `CREATE` / `,` / `DOES>` / `MACRO:` —
> the very meta-words [Why mforth](why-mforth.md#what-it-gives-up)
> says v1 gives up — but only in a form that is *fully eliminated
> before stack analysis runs*. The meta layer earns its place by
> being **compile-time-only, cell-free, and pure**: three constraints
> that keep it from touching the two load-bearing invariants (static
> stack analysis and REPL ↔ mlog equivalence). This page argues why
> those three constraints are the right boundary.

ANS Forth's meta-compilation (`POSTPONE`, `IMMEDIATE`, open-ended
`DOES>`, `EXECUTE`) makes static stack analysis undecidable in general
— that is why the dialect drops it. But a *restricted* defining-word
surface is too useful to lose entirely: `CONSTANT` is the textbook
example, and user-defined defining words (`DOUBLED`, unit-table
builders, named-color words) are exactly the kind of factoring Forth
is good at. The meta layer is the answer to "how much of that can we
have without re-opening the door we deliberately closed?"

## One seam: phase-0 `expand`

Everything meta happens in a single deterministic pass — `expand`
(`src/mforth/expand.py`) — that runs **between `resolve` and
`stackcheck`**:

```
[lex] → [parse] → [resolve] → [expand] → [stackcheck] → backend
                              ^^^^^^^^^^
                              the only meta-elimination seam
```

The whole point of putting it *there* is **expand-then-check**: by the
time `stackcheck` sees the program, every meta-word is gone. `expand`
guarantees an invariant — *zero meta-words survive* — so the
stack-checker and both backends only ever analyse and lower ordinary
Forth. The static stack analysis never has to reason about a word that
might rewrite the compiler; there is no such word left to reason about.

`resolve` runs *before* `expand`, so it cannot yet know what a
defining word will stamp. It handles this by **tolerating** the meta
names (the defining-word names, their not-yet-defined children, and
`CREATE` / `,` / `DOES>`) during its existence check, and deferring
all of the real work — and any error that can be raised — to `expand`.
`resolve` builds the dictionary and checks names; `expand` is the one
place that mutates the program and the one place that can reject it.

## The cell-free `DOES>` boundary (D5)

`expand` stamps a child by **partial-evaluating** its `DOES>` body
against an immutable compile-time field. The classic
`: CONSTANT CREATE , DOES> @ ;` *conceptually* stores the value in a
per-child field and has `DOES> @` fetch it at runtime — that fetch is
where a naïve implementation would allocate an mlog memory cell.
mforth's move is to **evaluate the fetch away**: the field is a
compile-time constant, so `@` against it folds to a literal, and the
child lowers to a bare literal push. No cell, no `read`, no `write`.

This draws a sharp line:

- **const + immutable data → stampable, cell-free.** The `DOES>` body
  reduces to literals. `CONSTANT` is the first case; the general
  stamper also handles pure arithmetic and stack juggling
  (`: DOUBLED CREATE , DOES> @ 2 * ;` → a child that pushes `2 ×` its
  field), and multi-`,` fields.
- **mutable / runtime-computed per-instance fields → rejected.** A
  `DOES>` body with `!` (store), a runtime `@`, a non-constant
  argument, or a residual that will not fold needs a real per-instance
  cell — which v1 does not have.

The crucial property is **rejected, not miscompiled**. A
boundary-crossing defining word raises `CellBoundaryError`, naming the
offending word, at compile time. It is *never* silently lowered to a
memory cell and *never* allowed to diverge between the REPL and the
compiler. v1 would rather refuse a program than quietly grow a cell;
those cells re-enter only in v2, behind an explicit flag.

> Why refuse rather than allocate? Because a silently-allocated cell is
> exactly the kind of thing that breaks REPL ↔ mlog equivalence and
> the cell-free demos lore-cap *without anyone noticing*. A loud
> `CellBoundaryError` keeps the boundary visible.

## Meta-word purity (D14)

A meta-word runs **at compile time**. So its body must not *do*
anything observable at runtime — it cannot print, flush, sense, wait,
get a link, drive a `CONTROL-*`, or read mutable runtime state via `@`
on a `VARIABLE`. A `MACRO:` body (or a `DOES>` body) that violates
this raises `PurityError`, naming the offending primitive.

The check is **tag-driven, not name-driven**. It keys off the
`"mindustry"` / `"mindustry-control"` family tags rather than a
hard-coded list of forbidden words, so a *new* world-sink primitive —
added under any name in some later bead — is caught automatically,
with no edit to the purity check. That is the difference between a
denylist that rots and an invariant that holds.

Purity is what makes "compile-time-only" *true* rather than merely
intended. Without it, a macro could smuggle a `PRINT` into the
compile-time phase, and the REPL (which would run the body at
expansion time) and the compiler (which would emit it) could disagree
about when — or whether — that print happens. Purity closes that gap
before it opens.

## Why these three, together

The three constraints are not independent niceties; each one defends a
specific invariant:

| Constraint | Mechanism | Invariant it protects |
| --- | --- | --- |
| Compile-time-only | `expand` eliminates all meta-words before `stackcheck` | static stack analysis stays decidable |
| Cell-free (D5) | partial-evaluate `DOES> @` to a literal; reject the rest | v1 cell-free strategy + the demos lore-cap |
| Pure (D14) | tag-driven world-sink / runtime-read rejection | REPL ↔ mlog equivalence |

Drop any one and the meta layer starts leaning on something it was
designed not to touch. Keep all three and you get the useful 80% of
defining words — `CONSTANT` and its honest relatives — with none of
the undecidability that made the dialect drop meta-compilation in the
first place.

## Cross-references

- [Why mforth](why-mforth.md#what-it-gives-up) — the original
  decision to drop ANS meta-compilation; this page is the principled
  re-admission of its safe subset.
- [Dictionary → Meta layer](../reference/dictionary.md#meta-layer-defining-words-macros)
  — the surface catalogue (`CREATE`, `,`, `DOES>`, `CONSTANT`,
  `MACRO:`).
- [mlog lowering → Stamped defining words](../reference/mlog-lowering.md#stamped-defining-words-create-does)
  — exactly how a stamp reaches a literal push, and when it is
  rejected.
