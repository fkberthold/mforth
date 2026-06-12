# Chapter 3 — Defining your own words

> **You will:** turn a sequence of operations into a named **word** you
> can reuse, write its stack effect honestly, and build bigger words by
> combining smaller ones — the habit Forth programmers call
> *factoring*.
>
> **You will need:** [Chapter 2](02-juggling.md) finished. You have
> already been writing definitions in the exercises; now they take
> centre stage.

A Forth program is mostly *definitions*: you teach the language a new
word, then use it as if it had always existed. New words and built-in
words are indistinguishable at the call site — that is the whole point.

## The colon definition

You define a word with `:` (colon), a name, a body, and `;`
(semicolon) to end it:

```text
: name ( stack-effect ) body ;
  ^    ^                ^    ^
  |    |                |    end the definition
  |    |                the words that run when `name` is called
  |    the name you are defining
  start a definition
```

Here is the `double` word — our running example from the exercises.
Defining it and then using it:

```forth
: double ( n -- n*2 ) DUP + ;
21 double .
```

That prints `42`. The line `: double ( n -- n*2 ) DUP + ;` does not
*run* `DUP +` — it stores it under the name `double`. The next line,
`21 double .`, is where it executes: `21` pushes, `double` runs
`DUP +` (copying `21` and adding the two copies to get `42`), and `.`
prints it.

## The stack effect is a contract — keep it honest

The `( n -- n*2 )` part is a **stack-effect comment**. The text inside
is for *you* — `n` and `n*2` are just labels — but the *shape* matters:
it says `double` consumes one item and leaves one item.

mforth takes this seriously. Every word has a statically known stack
effect, and mforth's checker verifies that the words in your body
actually add up to the shape you declared. Write a body whose net
effect does not match — say, one that consumes two items for a word
you declared as `( n -- n*2 )` — and mforth rejects it before it ever
runs, with a stack error. That is a feature: a whole class of bugs is
caught at definition time, not discovered at runtime.

So the rule of thumb: **write the stack effect you intend, then make
the body match it.** The two exemplars in this track model it —
`double` is `( n -- n*2 )`, and `nip` is `( a b -- b )`. You can print
either reference with `mforth check --solution forth-101/01-double`.

## Factoring: small words make big words

Once a word exists, other words can call it. This is how Forth programs
grow — not by writing longer and longer definitions, but by naming
useful pieces and combining them. Doubling something twice multiplies
it by four:

```forth
: double ( n -- n*2 ) DUP + ;
: quadruple ( n -- n*4 ) double double ;
5 quadruple .
```

That prints `20`. `quadruple`'s body is just `double double` — it never
mentions `DUP` or `+` at all, because `double` already captured that
idea. Reading `double double` tells you *what* it does ("double it,
then double again") without making you re-derive the arithmetic.

Two things to notice:

- **Order matters.** `double` is defined on the first line, so
  `quadruple` on the second line can call it. A word can only call
  words defined *above* it. Swap the two lines and mforth will not know
  what `double` means when it reaches `quadruple`.
- **Names are cheap; spend them.** A good Forth word reads like a
  sentence of smaller words. `quadruple` is clearer than `DUP + DUP +`
  even though they compile to the same work.

## Naming rules

A word name can be almost any run of non-space characters: letters,
digits, and symbols all work. `double`, `quadruple`, `back-rot`,
`peek-under`, even `2dup-ish` are all fine names. Names are
case-insensitive, so `DUP`, `dup`, and `Dup` are the same word — the
built-ins are conventionally written uppercase in this tutorial, and
your own words lowercase, but mforth does not require it.

## Exercises

These ask you to define words and have the checker drive them with
sample inputs. Use `mforth check --scaffold <id>` to get a starter
file with the marker already in place.

### Exercise 3.1 — `forth-101/08-square`

Define `square` `( n -- n*n )`: the input times itself. You met the
shape in Chapter 2 (`DUP *`); here it gets a name.

```bash
mforth check --scaffold forth-101/08-square
mforth check 08-square.fs
```

Expected:

```text
✓ forth-101/08-square — 3/3 cases pass
```

### Exercise 3.2 — `forth-101/09-cube`

Define `cube` `( n -- n*n*n )`: the input raised to the third power.
You need three copies of `n` before you start multiplying.

```bash
mforth check 09-cube.fs
```

Expected:

```text
✓ forth-101/09-cube — 3/3 cases pass
```

### Exercise 3.3 — `forth-101/10-average`

Define `average` `( a b -- avg )`: the mean of the two inputs.
Remember `/` is float division (Chapter 1), so `3 4 average` is `3.5`,
not `3`.

```bash
mforth check 10-average.fs
```

Expected:

```text
✓ forth-101/10-average — 3/3 cases pass
```

### Exercise 3.4 — `forth-101/11-quadruple`

Define `double` `( n -- n*2 )` **and then** `quadruple` `( n -- n*4 )`
that calls `double` twice — the factoring example from above, now your
turn to write. The driver checks both words, so define `double` first.

```bash
mforth check 11-quadruple.fs
```

Expected:

```text
✓ forth-101/11-quadruple — 3/3 cases pass
```

## What you learned

- `: name ( stack-effect ) body ;` defines a reusable word; it stores
  the body, and the word runs only when later called.
- The stack-effect comment is a **contract** mforth checks — declare
  the shape you intend and make the body match, or it is rejected
  before running.
- **Factoring**: build big words from small ones (`quadruple` =
  `double double`). A word can only call words defined above it.
- Names are free-form and case-insensitive.

That closes Part I's first three chapters. Next:
[Chapter 4 — Arithmetic and truth](04-arithmetic.md), where `MOD` and
the comparison words (`< > =`, `AND OR NOT`) give us values to make
*decisions* with — the groundwork for branching in
[Chapter 5](05-branching.md).
