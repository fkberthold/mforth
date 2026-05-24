# Forth, the mental model

> **Status:** stub. The full explanation will land as a follow-up to
> the [*Writing mforth for Mindustry* tutorial](../tutorials/writing-mforth-for-mindustry.md).

The tutorial's Part 0 gives you the *operational* mental model — what
the stack is, how the five juggling words behave, why postfix exists.
That is enough to read every example in this documentation set.

This page will go deeper, into the *compositional* mental model that
makes Forth feel like Forth once you've written a few hundred lines.
Planned contents:

- **Composition over abstraction.** Why named temporaries are a code
  smell in Forth, and what replaces them when the stack starts to feel
  too deep.
- **Factoring as a stack discipline.** Concrete heuristics for
  splitting a long expression into smaller words, using stack-effect
  comments as the contract.
- **Where postfix shapes programs differently from infix.** The cases
  where Forth code is shorter than the equivalent expression-language
  code, and the cases where it isn't.
- **The mforth-specific take.** How mforth's static stack-checker,
  cell-free v1 dialect, and per-target backend choices nudge you
  toward a particular Forth style — closer to the colorForth /
  embedded-Forth lineage than to ANS.

!!! note "Coming soon"
    This page is intentionally a stub so the tutorial's "deep dive"
    cross-link resolves in the navigation. Tracking via the
    `mforth-10t` umbrella epic's docs lane; check `bd ready` for the
    in-flight bead.
