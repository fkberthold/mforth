# Defining words — words that make words

> **You will:** define a *defining word* — a word whose job is to make
> other words. You will build `CONSTANT` yourself out of three small
> meta-words (`CREATE`, `,`, `DOES>`), watch it stamp named values at
> compile time, grow a little family of folding defining words, and meet
> the one place mforth says *no* — the cell-free boundary.
>
> **You will need:** [Chapter 3](03-defining.md), where `: … ;` taught
> the language a new word. This chapter teaches the language a new word
> that, in turn, teaches it more words.

In [Chapter 3](03-defining.md) you learned to define words with `: … ;`.
Every word you have written since — `double`, `cube`, `bump`, `restock`
(soon) — has been a colon definition. That is most of Forth.

But Forth has a second, quieter trick, and it is the one that makes
Forthers grin: you can define a word whose job is *to define more words*.
A word that, when you run it, **stamps out a brand-new word**. Forth calls
these **defining words**, and the textbook first example is one you have
been using in other languages without a second thought: a *constant*.

## `CONSTANT` is not built in — you make it

In most languages a constant is a keyword the compiler hands you. In
Forth it is not. mforth ships no `CONSTANT` word at all. Instead it gives
you the three pieces to *build* one, and `CONSTANT` is a word you define
in your own source:

```forth
: CONSTANT  CREATE , DOES> @ ;
42 CONSTANT answer
answer .
```

That prints `42`. Read it in two beats:

- **Define-time.** `: CONSTANT CREATE , DOES> @ ;` is an ordinary colon
  definition — it just happens to contain the three meta-words. It does
  nothing yet; it teaches the language a new *defining word* called
  `CONSTANT`.
- **Stamp-time.** `42 CONSTANT answer` *runs* `CONSTANT` with `42` on the
  stack. That call stamps a new word, `answer`, into the dictionary. From
  here on, `answer` is a word like any other — `answer .` pushes `42` and
  prints it.

You have written a word that wrote a word. `answer` did not exist until
`CONSTANT` made it.

## What the three pieces do

`CREATE`, `,`, and `DOES>` are not words you call on their own — they
only ever appear *inside* a defining word's body, where together they
describe how to stamp a child. Each has one job:

| Word | When | What it does |
|------|------|--------------|
| `CREATE` | inside the body | begins making the new child word, and gives it a private field to hold data |
| `,` (comma) | after `CREATE` | takes one value off the stack and tucks it into that field |
| `DOES>` | inside the body | everything after it is the **child's** behaviour, run whenever the child is later called |

So read `: CONSTANT CREATE , DOES> @ ;` as a sentence: *to define a
CONSTANT, **create** a new word, comma the value into its field, and say
that **does>** — when you call the child — it fetches (`@`) that field.*

When you run `42 CONSTANT answer`:

1. `42` is on the stack.
2. `CREATE` starts the new word `answer` and gives it a field.
3. `,` moves `42` from the stack into `answer`'s field.
4. `DOES> @` is recorded as what `answer` will do: fetch its field.

Later, `answer` runs `@` against a field holding `42`, so it leaves `42`
on the stack. The pronunciation of `,` is "comma" and of `DOES>` is
"does"; `@` is the same "fetch" you met with variables in
[Chapter 7](07-state.md).

## The compile-time beat: the child is free

Here is the part that connects back to the Forth habit you have been
building. In [Chapter 9](09-factoring.md) the punchline was *factoring is
free at runtime* — mforth inlines your words, so clarity costs nothing.
Defining words have the same shape of payoff, pushed one phase earlier:

**`CONSTANT` runs at *compile time*. The child it stamps folds to a bare
literal — there is no runtime cost at all.**

When you write `42 CONSTANT answer` and later use `answer`, mforth does
not keep a box in memory and read it on every tick. It works the whole
thing out while compiling: the field holds `42`, the child's body is
`@`, so the fetch *folds away* to just the value `42`. By the time your
program runs — whether in the simulator or as mlog pasted into the
game — `answer` is indistinguishable from having typed `42` yourself. No
storage, no fetch, no instruction spent. The defining word did its work
before the program ever started.

This is why mforth can re-admit a meta-word it deliberately left out of
the core language: it is eliminated *before* anything runs. (For the why
behind that design, see
[The meta layer](../../explanation/meta-layer.md).)

## A small family of defining words

`CONSTANT` is the simplest defining word — its `DOES>` body just fetches.
But `DOES>` can do real arithmetic on the field, and it still folds away.
That lets one defining word stamp a whole family of related words.

Here is `DOUBLED`: each child it stamps pushes *twice* its stored value.

```forth
: DOUBLED  CREATE , DOES> @ 2 * ;
21 DOUBLED x
x .
```

That prints `42`. The `DOES>` body is `@ 2 *` — fetch the field, push
`2`, multiply — so the child built from `21` always pushes `42`. And
again, no runtime cost: `@ 2 *` against a field of `21` folds, at compile
time, to the literal `42`.

A second example, building a child that squares its field with `DUP *`
(the squaring trick from [Chapter 2](02-juggling.md)):

```forth
: SQUARED-C  CREATE , DOES> @ DUP * ;
6 SQUARED-C six-sq
9 SQUARED-C nine-sq
six-sq .
nine-sq .
```

That prints `36` then `81`. One defining word, `SQUARED-C`, stamped two
independent children — each carrying its own field, each folding to its
own literal. This is the defining-word payoff: you describe the *pattern*
once, then mint as many named, zero-cost values as you like.

## When mforth says no — the cell-free boundary

A defining word's `DOES>` body can use the field and *compile-time*
values — literals, arithmetic, stack juggling. What it cannot do is
combine the field with a value that only exists *at runtime*. Try to, and
mforth stops you:

```text
: OFFSET  CREATE , DOES> @ + ;
100 OFFSET past-hundred
5 past-hundred
```

That `DOES> @ +` wants to fetch the field (`100`) and add it to whatever
the caller left on the stack (`5`) — a value mforth does not know until
the program runs. mforth refuses to compile it:

```text
CellBoundaryError: defining word 'OFFSET' (child 'past-hundred')
  DOES> body does not reduce to a compile-time constant —
  crosses the cell-free boundary
```

This is the same spirit as the stack checker from
[Chapter 3](03-defining.md): a whole class of trouble is **caught at
definition time, not discovered at runtime**. A child that mixed in a
runtime value could not fold to a literal — it would need a real memory
box to live in, and mforth v1 deliberately has none. Rather than quietly
grow one behind your back, mforth names the offending word and stops, so
the boundary stays visible.

The rule of thumb is short: **a `DOES>` body may compute with its field
and constants, but not with the caller's runtime input.** Field plus a
literal (`@ 100 +`) folds and is fine; field plus a stack value (`@ +`)
does not and is refused. For the full account of which bodies fold and
which are rejected, see
[the meta layer](../../explanation/meta-layer.md#the-cell-free-does-boundary-d5)
and the
[dictionary reference](../../reference/dictionary.md#meta-layer-defining-words-macros).

## Exercises

Write each answer in its own `.fs` file and check it:

```bash
mforth check my-answer.fs
```

A pass prints a line like `✓ forth-104/01-constant — 2/2 cases pass`.
`mforth check --scaffold <id>` writes a starter file with the marker;
`mforth check --solution <id>` reveals the reference answer.

### Exercise 1 — define `CONSTANT`

`id: forth-104/01-constant`

Define the defining word `CONSTANT` `(` value `--` `)` exactly as above
(`CREATE , DOES> @`), then use it to stamp a constant `answer` holding
`42`. The driver pushes `answer` and prints it.

```forth
\ @exercise forth-104/01-constant
: CONSTANT  CREATE , DOES> @ ;
42 CONSTANT answer
```

`answer .` should print `42`.

### Exercise 2 — a `DOUBLED` defining word

`id: forth-104/02-doubled`

Define a defining word `DOUBLED` whose children push **twice** their
stored value (`CREATE , DOES> @ 2 *`). Then stamp a child `x` from `21`.

```forth
\ @exercise forth-104/02-doubled
: DOUBLED  CREATE , DOES> @ 2 * ;
21 DOUBLED x
```

`x .` should print `42`.

### Exercise 3 — a folding family

`id: forth-104/03-family`

Define a defining word `SQUARED-C` whose children push the **square** of
their stored value (`CREATE , DOES> @ DUP *`). Stamp two children — `six`
from `6` and `nine` from `9` — to prove one defining word mints a whole
family.

```forth
\ @exercise forth-104/03-family
: SQUARED-C  CREATE , DOES> @ DUP * ;
6 SQUARED-C six
9 SQUARED-C nine
```

`six .` should print `36`; `nine .` should print `81`.

## What you learned

- A **defining word** is a word that makes other words. `CONSTANT` is the
  classic, and in mforth you **define it yourself** out of `CREATE`,
  `,`, and `DOES>` — there is no built-in.
- `CREATE` starts a child with a private field, `,` fills the field from
  the stack, and `DOES>` describes what the child does when later called.
- A defining word runs at **compile time**; the child it stamps folds to
  a bare literal, with **no runtime cost** — the meta-layer echo of
  Chapter 9's "factoring is free."
- A `DOES>` body may compute with its field and constants (`@ 2 *`,
  `@ DUP *`), and one defining word can mint a whole family of children.
- The **cell-free boundary**: a body that mixes the field with a runtime
  stack value cannot fold, so mforth refuses it with a
  `CellBoundaryError` at definition time rather than growing a hidden
  memory cell.

Next: [11. Macros](11-macros.md) — the other half of the meta layer.
Where a defining word makes a *named value*, a macro substitutes a
*phrase* of Forth straight into your code, and it too vanishes before the
program runs.
