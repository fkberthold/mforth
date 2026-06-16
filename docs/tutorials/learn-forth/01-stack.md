# Chapter 1 — The stack, and why `3 4 +`

> **You will:** meet Forth's one big idea — the data stack — and learn
> to read and write arithmetic the way Forth does: operator *last*.
> By the end you will evaluate a few expressions and have mforth print
> the answers back to you.
>
> **You will need:** mforth installed (see
> [Getting started](../getting-started.md)) so that `mforth --help`
> works. No prior Forth experience — this is chapter one.

This is the start of **Learn Forth with mforth**, a series that
teaches you Forth from zero using mforth's simulator and its built-in
exercise checker. You do not need to know Mindustry yet; the first
eleven chapters are pure Forth. We pick up Mindustry in
[Chapter 12](12-simulator.md).

## One stack, and that is the whole trick

Most languages you have met compute with *named variables* and
*nested expressions*: `y = (a + b) * c`. Forth throws both away. It
has a single shared workspace called the **data stack**, and a program
is just a stream of tokens that each do one of two things:

- a **number** pushes itself onto the stack, or
- a **word** (Forth's name for a function) consumes some items off the
  top of the stack and pushes its results back.

That is it. There is no assignment, no parentheses, no operator
precedence. Read the tokens left to right and watch the stack change.

Picture the stack as a pile of plates. New values land on **top**.
Words take from the top and put results back on top.

## Numbers push; `+` adds the top two

Here is the smallest interesting program. Type it into a file called
`first.fs`:

```forth
3 4 + .
```

Run it through the simulator:

```bash
mforth run --no-loop first.fs
```

Walk the tokens one at a time and track the pile:

```text
token   stack after   what happened
-----   -----------   ----------------------------------
3       3             pushed 3
4       3 4           pushed 4 (now on top)
+       7             + took 4 and 3, pushed 3 + 4 = 7
.       (empty)       . took 7 and printed it
```

The new word here is `.` — pronounced "dot". It pops the top value and
prints it. (In a moment we will check that the printed answer is `7`.)

This ordering — operands first, operator last — is called **postfix**,
or **Reverse Polish Notation (RPN)**. If you have used an old HP
calculator, this is the same idea. You write `3 4 +`, never `3 + 4`,
because by the time `+` runs, the two numbers it needs are already
sitting on the stack waiting for it.

## The four arithmetic words

`+`, `-`, `*`, and `/` each take the top two values and push one
result. They all compute **(the value underneath) op (the value on
top)**, which matters the moment the operation is not symmetric:

```forth
10 3 - .
```

`10` is pushed first (underneath), then `3` (on top). `-` computes
`10 - 3`, so this prints `7` — *not* `-7`. The number that arrived
first is the left operand.

A note on division: mforth's `/` is **floating-point** division, not
the integer division some Forths use. `20 4 /` is `5`, and `7 2 /` is
`3.5`. Whole-number results print without a trailing `.0`, so `5`
shows as `5`, not `5.0`.

```forth
20 4 / .
7 2 / .
```

That program prints two lines: `5`, then `3.5`.

## No parentheses, ever

The headline payoff of postfix: the token order *is* the evaluation
order, so you never need parentheses or precedence rules. Compare
these two infix expressions and their Forth equivalents:

```text
infix            forth
--------------   -----------
(5 + 3) * 2      5 3 + 2 *
5 + 3 * 2        5 3 2 * + 
```

For the first, we add `5` and `3` to get `8`, then multiply by `2`:

```forth
5 3 + 2 * .
```

That prints `16`. Trace it: `5 3 +` leaves `8` on the stack, then `2`
pushes on top, then `*` multiplies `8 * 2`. The `+` happened first
purely because we wrote it first — no parentheses required.

For the second expression (`5 + 3 * 2`, where multiplication binds
tighter), we multiply `3 * 2` first, then add `5`:

```forth
5 3 2 * + .
```

That prints `11`. The change in meaning is entirely a change in token
order. There is nothing else to learn about precedence, because there
is no precedence — only order.

## Exercises

Time to drive the checker. For each exercise, write a `.fs` file with
the code asked for, then run `mforth check <file>`. A green `✓` means
your answer behaves correctly when run through the same simulator.

Two checker conveniences you will use throughout the series:

- `mforth check --scaffold <id>` writes a starter `.fs` stub (with the
  exercise's `\ @exercise` marker already in place) into the current
  directory.
- `mforth check --solution <id>` prints the reference answer, if you
  get stuck.

These first exercises ask you to write **just the calculation** — no
word definition, no `.` (the checker appends the `.` that prints your
result). Leave exactly one value on the stack.

### Exercise 1.1 — `forth-101/03-rpn-add-mul`

Compute `(5 + 3) * 2` in postfix and leave it on the stack.

```bash
mforth check --scaffold forth-101/03-rpn-add-mul
# edit 03-rpn-add-mul.fs, then:
mforth check 03-rpn-add-mul.fs
```

Expected when correct:

```text
✓ forth-101/03-rpn-add-mul — 1/1 cases pass
```

### Exercise 1.2 — `forth-101/04-rpn-two-groups`

Compute `(10 - 2) * (3 + 1)` in postfix and leave it on the stack.
Stack effect of your snippet: `( -- 32 )`. Build each parenthesised
group in turn — each leaves one value on the stack — then combine the
two leftovers with a single `*`.

```bash
mforth check 04-rpn-two-groups.fs
```

Expected:

```text
✓ forth-101/04-rpn-two-groups — 1/1 cases pass
```

## What you learned

- The **data stack** is Forth's only workspace; numbers push, words
  consume-and-push.
- **Postfix / RPN**: operands first, operator last. Token order is
  evaluation order, so there is no precedence and no parentheses.
- `+ - * /` each take the top two and push one; they compute
  *(under) op (top)*, so order matters for `-` and `/`.
- `.` ("dot") pops and prints the top of the stack.

Next: [Chapter 2 — Juggling the stack](02-juggling.md). So far every
value gets used in the order it arrives. Next we learn the words that
copy, drop, and reorder items so the right value is on top when a word
needs it.
