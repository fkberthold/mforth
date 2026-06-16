# 11. Macros

> **You will:** define a **macro** with `MACRO: name … ;` — a word
> whose body is pasted straight into your code wherever you name it,
> before the program runs. You will see how a macro differs from a colon
> definition, compose macros out of macros, write named constants the
> macro way, and meet the purity rule that keeps macros honest.
>
> **You will need:** [Chapter 3](03-defining.md) (`: … ;`) and
> [Chapter 10](10-defining-words.md) (the meta layer, and the idea that
> some words run at compile time). This chapter is the other half of that
> layer.

[Chapter 10](10-defining-words.md) showed you a word that makes a *named
value*. A **macro** is the meta layer's other tool: a word that makes a
*phrase*. When you name a macro, mforth pastes its body into your code in
place of the name — at compile time, before the program runs. No call, no
jump; the words are simply *there*, as if you had typed them yourself.

## A first macro

You declare a macro at the top level with `MACRO:`, a name, a body, and
`;`:

```forth
MACRO: bump 1 + ;
5 bump .
```

That prints `6`. The line `MACRO: bump 1 + ;` does not run `1 +` — it
records that the name `bump` *stands for* the phrase `1 +`. Then `5 bump
.` is compiled as if you had written `5 1 + .`: push `5`, push `1`, add,
print. `bump` was never a separate thing that ran; it dissolved into its
body before execution.

## `:` versus `MACRO:` — substitution, not a call

This looks a lot like a colon definition, and the *result* here is the
same. The difference is in *how* the body gets there:

- A colon word — `: bump 1 + ;` — is a word your program **calls**.
  (mforth happens to inline it, but conceptually `bump` is a thing that
  runs.)
- A macro — `MACRO: bump 1 + ;` — is **textually substituted**. Every
  `bump` is replaced by `1 +` during compilation, and then `bump` does
  not exist anymore. There is nothing left to call.

For a tiny arithmetic word the two feel identical, and for that case you
can reach for either. Macros earn their keep when you want a *name for a
fragment* that is not a clean, stack-balanced word on its own — a phrase
you would never factor with `:`, but want to write once and reuse. And
because a macro is gone before the stack checker runs, it never appears
in a stack-effect contract of its own; it simply contributes its body's
effect at each site it is pasted.

## Macros compose

A macro body can name other macros, and they expand all the way down.
Build `hundredx` out of `tenx`:

```forth
MACRO: tenx 10 * ;
MACRO: hundredx tenx tenx ;
5 hundredx .
```

That prints `500`. `hundredx` expands to `tenx tenx`, and each `tenx`
expands to `10 *`, so the compiler ends up with `5 10 * 10 *` — multiply
by ten, then by ten again. Five hundred. You named the *idea* of "times a
hundred" in terms of "times ten", and both names evaporated before the
program ran.

## Named constants, the macro way

[Chapter 10](10-defining-words.md) built constants with `CONSTANT`. For a
plain literal there is an even shorter road: a macro whose body is just
the number.

```forth
MACRO: WIDTH 40 ;
WIDTH .
```

That prints `40`. `WIDTH` stands for the literal `40`, so anywhere you
write `WIDTH` the compiler sees `40`. It reads like a named constant and
costs exactly nothing — `WIDTH 2 /` compiles to `40 2 /` and folds to
`20`. When the value is a bare literal you want to *name*, a macro is the
lightest possible tool: one line, no `CREATE , DOES>`.

(Which to use? `CONSTANT` is the defining word when you want to stamp
*many* named values from one pattern, or when the value comes with field
arithmetic; a one-line `MACRO:` is perfect for a single named literal.
Both vanish before runtime.)

## The compile-time beat: pure, and gone before stackcheck

A macro, like a defining word, is eliminated at compile time — it never
survives into the running program. That is exactly why mforth can offer
it without weakening the static stack analysis from
[Chapter 3](03-defining.md): by the time the stack checker looks at your
code, every macro has already become ordinary Forth.

But "runs at compile time" carries an obligation. A macro body must be
**pure** — it may compute, but it may not *do* anything observable in the
world. It cannot `PRINT`, flush, `SENSOR`, `WAIT`, or drive a `CONTROL-`
word, because those are runtime actions and a macro has no runtime. Try
to smuggle one in and mforth refuses:

```text
MACRO: shout S" hi" PRINT ;
```

```text
PurityError: macro 'shout' calls world-sink primitive 'PRINT' —
  macros must be pure (compile-time only)
```

This is the same kind of guardrail as the stack checker and the
cell-free boundary you met in [Chapter 10](10-defining-words.md): a class
of mistake **caught at definition time, not at runtime**. The check is
not a hard-coded blocklist — it keys off what kind of word you are
calling, so a *new* world-touching word added to mforth later is caught
automatically, with no edit to the rule. For the full reasoning, see
[The meta layer → purity](../../explanation/meta-layer.md#meta-word-purity-d14).

## Exercises

Write each answer in its own `.fs` file and check it:

```bash
mforth check my-answer.fs
```

A pass prints a line like `✓ forth-105/01-macro — 2/2 cases pass`.
`mforth check --scaffold <id>` writes a starter; `mforth check --solution
<id>` reveals the reference answer.

### Exercise 1 — a first macro

`id: forth-105/01-macro`

Define a macro `bump` that stands for `1 +`, so that naming `bump` adds
one to the value beneath it.

```forth
\ @exercise forth-105/01-macro
MACRO: bump 1 + ;
```

`5 bump .` should print `6`; `0 bump bump .` should print `2`.

### Exercise 2 — compose macros

`id: forth-105/02-compose`

Define `tenx` (`10 *`) and then `hundredx` **in terms of `tenx`**
(`tenx tenx`). Naming `hundredx` should multiply by one hundred.

```forth
\ @exercise forth-105/02-compose
MACRO: tenx 10 * ;
MACRO: hundredx tenx tenx ;
```

`5 hundredx .` should print `500`; `3 tenx .` should print `30`.

### Exercise 3 — a named constant

`id: forth-105/03-width`

Define a macro `WIDTH` that stands for the literal `40` — a named
constant the macro way.

```forth
\ @exercise forth-105/03-width
MACRO: WIDTH 40 ;
```

`WIDTH .` should print `40`; `WIDTH 2 / .` should print `20`.

## What you learned

- A **macro** (`MACRO: name … ;`) is a named *phrase*: mforth pastes its
  body into your code at every call site, at compile time, then the
  macro is gone.
- `MACRO:` is **substitution**, where `:` is a (inlined) **call** — for a
  small word they coincide, but a macro shines for naming a fragment, not
  a stack-balanced word.
- Macros **compose**: a macro body may name other macros, and they expand
  all the way down (`hundredx` → `tenx tenx` → `10 * 10 *`).
- A one-line macro is the **lightest named constant** (`MACRO: WIDTH
  40 ;`), complementing `CONSTANT` from Chapter 10.
- Macro bodies must be **pure** — no world-sinks at compile time — or
  mforth raises a `PurityError` at definition time.

That closes **Part I — Thinking in Forth**. You can now build, name,
branch, loop, remember, report, factor, and — with the meta layer — write
words that *make* words. Next comes **Part II**, where you point all of
it at the Mindustry simulator:
[12. Meet the simulator](12-simulator.md) — real blocks, a `.world.toml`
sidecar, and output that lands on a message block in-game.
