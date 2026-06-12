# Chapter 2 — Juggling the stack

> **You will:** learn the handful of words that copy, discard, and
> reorder items on the stack, so the value a word needs is on top when
> it runs. These are the verbs you reach for constantly once programs
> get longer than one line.
>
> **You will need:** [Chapter 1](01-stack.md) finished — you should be
> comfortable that `3 4 + .` prints `7` and *why*.

In Chapter 1 every value got consumed in the order it arrived. Real
programs are not so tidy: you often need a value *twice*, or you need
the second-from-top item before the top one. Forth gives you a small
set of **stack-juggling** words for exactly this. They do not compute
anything — they just rearrange the pile.

## The five you will use most

Here are the five core juggling words with their stack effects. Recall
the notation from Chapter 1's exercises: `( before -- after )`, top of
stack on the **right**.

```text
DUP    ( a -- a a )        duplicate the top item
DROP   ( a -- )            discard the top item
SWAP   ( a b -- b a )      swap the top two
OVER   ( a b -- a b a )    copy the second item up to the top
ROT    ( a b c -- b c a )  rotate the top three (third comes to top)
```

Two more are handy shorthands for common pairs, and mforth has them
built in too:

```text
NIP    ( a b -- b )        drop the second item (= SWAP DROP)
TUCK   ( a b -- b a b )    copy the top under the second (= SWAP OVER)
```

## `DUP`: use a value twice

The most common need: you have a number and you want to use it in two
places. `DUP` makes a second copy so the first operation does not
consume your only one.

```forth
5 DUP * .
```

Trace it: `5` pushes, `DUP` copies it to `5 5`, `*` multiplies the two
copies, `.` prints `25`. That is "five squared" with no variable — the
stack held the second copy for you. (You met this exact pattern as the
`double` exemplar, `DUP +`; squaring is the same shape with `*`.)

## A word on the print order

One thing to watch when an exercise prints several values: `.` always
prints the **top** of the stack and removes it. So a run of `.` words
prints the stack **top-first**, which is the reverse of how the values
look written left to right.

```forth
1 2 3 . . .
```

The stack is `1 2 3` with `3` on top, so the three `.` words print
`3`, then `2`, then `1`. Keep this in mind reading the expected output
of the exercises below — the *checker* compares the exact sequence of
printed lines.

## `SWAP` and `OVER`: getting the right item on top

`SWAP` exchanges the top two. `OVER` reaches past the top to copy the
second item upward (leaving both originals in place). Watch what each
leaves, printed out:

```forth
1 2 SWAP . .
```

`SWAP` turns `1 2` into `2 1`. Printing top-first gives `1`, then `2`.

```forth
3 8 OVER . . .
```

`OVER` turns `3 8` into `3 8 3`. Printing top-first gives `3`, `8`,
`3` — the copied `3` came back first.

## `ROT`: reach for the third item

`ROT` rotates the top three so the deepest of them surfaces:
`( a b c -- b c a )`. Apply it **twice** and you rotate the other way
(two thirds of a turn = one turn backward), which is a useful trick.

```forth
1 2 3 ROT . . .
```

`ROT` turns `1 2 3` into `2 3 1`. Printing top-first: `1`, `3`, `2`.

## Exercises

Each of these asks you to **define a word** that performs a specific
rearrangement. (We formally meet `: ... ;` definitions in
[Chapter 3](03-defining.md), but the shape is simple enough to use
now: `: name ( stack-effect ) body ;`. The exemplars `01-double` and
`02-nip` already live in this track if you want to peek at the form
with `mforth check --solution forth-101/01-double`.)

The driver the checker appends prints the rearranged stack with a run
of `.` words, so mind the top-first print order when you read the
expected output.

### Exercise 2.1 — `forth-101/05-triplicate`

Define `triplicate` `( a -- a a a )`: leave three copies of the top
item.

```bash
mforth check --scaffold forth-101/05-triplicate
mforth check 05-triplicate.fs
```

Expected:

```text
✓ forth-101/05-triplicate — 2/2 cases pass
```

### Exercise 2.2 — `forth-101/06-peek-under`

Define `peek-under` `( a b -- a b a )`: copy the second item up onto
the top, leaving the original two in place. (One built-in word does
exactly this.)

```bash
mforth check 06-peek-under.fs
```

Expected:

```text
✓ forth-101/06-peek-under — 2/2 cases pass
```

### Exercise 2.3 — `forth-101/07-back-rot`

Define `back-rot` `( a b c -- c a b )`: rotate the top three the
*other* way from `ROT`, so the top item sinks under the other two.
Hint: what does applying `ROT` twice do?

```bash
mforth check 07-back-rot.fs
```

Expected:

```text
✓ forth-101/07-back-rot — 2/2 cases pass
```

## What you learned

- `DUP DROP SWAP OVER ROT` copy, discard, and reorder stack items
  without computing anything; `NIP` and `TUCK` are built-in shorthands.
- `DUP` is how you use a value twice without naming it.
- `.` prints **top-first**, so a run of `.` reverses the written order
  of the stack.
- Two `ROT`s rotate the opposite way from one.

Next: [Chapter 3 — Defining your own words](03-defining.md). You have
been writing `: name ... ;` already in these exercises; next we make
that the centre of attention — naming, factoring, and building bigger
words out of small ones.
