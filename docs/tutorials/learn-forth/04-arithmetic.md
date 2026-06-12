# Chapter 4 — Arithmetic and truth

> **You will:** do real math on the stack, ask yes/no questions with
> the comparison words, learn how mforth spells *true* and *false*,
> and combine those answers with `AND`, `OR`, and `NOT`.
>
> **Before this:** [Chapter 3 — Defining words](03-defining.md). You
> can already push numbers, juggle them with `DUP`/`SWAP`/`OVER`, and
> name a sequence with `: ... ;`.

So far the only arithmetic you have used is `+`. There are four more
operators, and a whole family of words that answer questions. Together
they are the raw material every decision in the next chapter is built
from.

## More arithmetic

You met `+` in Chapter 1. The full set of arithmetic words is small
and behaves exactly the way the stack model predicts — each one pops
two values and pushes one result:

| word | effect | does |
|------|--------|------|
| `+` | `( a b -- a+b )` | add |
| `-` | `( a b -- a-b )` | subtract (`a` minus `b`) |
| `*` | `( a b -- a*b )` | multiply |
| `/` | `( a b -- a/b )` | divide |
| `MOD` | `( a b -- a%b )` | remainder of `a / b` |

`MOD` is the one you may not have seen. It gives the *remainder* after
division — what is left over.

```forth
17 5 MOD .
```

`17 / 5` is 3 with 2 left over, so `17 5 MOD` leaves `2`, and `.`
prints it:

```
2
```

`MOD` is the workhorse of "is this divisible?" tests. `n 2 MOD` is 0
exactly when `n` is even — you will use that in the exercises.

!!! note "`/` divides like a calculator, not like classic Forth"
    Traditional Forth makes `/` round down to a whole number. mforth's
    `/` is ordinary division: `7 2 /` is `3.5`, not `3`. This is a
    deliberate choice so the simulator behaves the way the math looks.
    When you want the whole-number part *and* the remainder, reach for
    `MOD`.

## Asking questions: the comparison words

A comparison word pops two values and pushes the *answer* to a yes/no
question. There are six:

| word | effect | true when |
|------|--------|-----------|
| `<`  | `( a b -- flag )` | `a` is less than `b` |
| `>`  | `( a b -- flag )` | `a` is greater than `b` |
| `=`  | `( a b -- flag )` | `a` equals `b` |
| `<>` | `( a b -- flag )` | `a` is *not* equal to `b` |
| `<=` | `( a b -- flag )` | `a` is less than or equal to `b` |
| `>=` | `( a b -- flag )` | `a` is greater than or equal to `b` |

Read them the same left-to-right way as the math words: `5 3 <` asks
"is 5 less than 3?".

```forth
5 3 < .
5 3 > .
4 4 = .
```

prints

```
0
1
1
```

5 is not less than 3, so the first answer is `0`. 5 *is* greater than
3, so the second is `1`. 4 equals 4, so the third is `1`.

## How mforth spells true and false

That `0` and `1` is the whole story: **`0` is false, `1` is true.**
A comparison always leaves one of those two numbers. There is no
separate boolean type — a *flag* is just a number, and `.` prints it
like any other number.

This matters because a flag is an ordinary value you can keep doing
arithmetic on. `2 3 <` leaves `1`, and you could add to it, store it,
or — most usefully — feed it to the next chapter's `IF`.

!!! note "Other Forths use -1 for true"
    If you have seen Forth before, you may expect *true* to be `-1`
    (all bits set). mforth uses `1`, because that is what the Mindustry
    logic processor it compiles to produces. Anything you write here
    behaves the same in the game.

## Combining answers: `AND`, `OR`, `NOT`

One question is rarely enough. To ask "is `n` between 1 and 10?" you
need *two* comparisons and a way to join them. That is what the logical
words do:

| word | effect | does |
|------|--------|------|
| `AND` | `( a b -- flag )` | true when **both** are true |
| `OR`  | `( a b -- flag )` | true when **either** is true |
| `NOT` | `( a -- flag )` | flips: false becomes true, true becomes false |

```forth
1 1 AND .
1 0 OR .
0 NOT .
```

prints

```
1
1
1
```

Both of `1 1` are true, so `AND` gives `1`. At least one of `1 0` is
true, so `OR` gives `1`. `NOT` flips `0` to `1`.

Because flags are just numbers, you build a compound test by stacking
two comparisons and joining them:

```forth
10 0 > 10 100 < AND .
```

Reading left to right: `10 0 >` leaves `1` (10 is positive), then
`10 100 <` leaves `1` (10 is below 100), then `AND` joins them into a
single `1`. So 10 passed both halves of "positive and below 100", and
the line prints:

```
1
```

!!! note "`NOT` treats any nonzero value as true"
    `NOT` of `0` is `1`. `NOT` of `1` is `0`. And `NOT` of *any* other
    number — `7 NOT`, `-3 NOT` — is `0`, because anything that is not
    zero counts as true. So `NOT` reliably turns a true-ish value into
    `0` and only `0` into `1`. That makes `... 0 = NOT` a handy
    "is it nonzero?" idiom, which you will use in the `odd?` exercise.

## Exercises

Write each answer in its own `.fs` file, put the `\ @exercise <id>`
marker as the first line, then run `mforth check <file>`. A pass looks
like:

```
✓ forth-102/01-even — 4/4 cases pass
```

Stuck on the shape of the file? `mforth check --scaffold forth-102/01-even`
writes a starter stub with the prompt and a hint. Truly stuck?
`mforth check --solution forth-102/01-even` prints a reference answer —
but try the hint first.

### Exercise 1 — `even?` ( n -- flag )

Leave `1` if `n` is even, `0` if it is odd.

*Hint:* a number is even exactly when `n 2 MOD` is `0`. Compare the
remainder to `0` with `=`.

```
mforth check forth-102/01-even.fs
→ ✓ forth-102/01-even — 4/4 cases pass
```

### Exercise 2 — `odd?` ( n -- flag )

Leave `1` if `n` is odd, `0` if it is even. Build it from the even
test — odd is just *even, negated*.

*Hint:* `2 MOD 0 =` is the even test; `NOT` flips the flag. (Or
compare the remainder to `0` with `<>`.)

```
mforth check forth-102/02-odd.fs
→ ✓ forth-102/02-odd — 3/3 cases pass
```

### Exercise 3 — `between?` ( lo hi n -- flag )

Leave `1` if `lo <= n <= hi`, otherwise `0`. Note the argument order:
the two bounds go on the stack first, the value to test goes on top.

This one needs two comparisons joined with `AND`, and a little stack
juggling to keep `n` around for both. `OVER` copies a buried value to
the top so you can compare it without losing it; `ROT ROT` rotates the
top three twice, which buries the first flag while you compute the
second. Trace the stack on paper one step at a time — that is the real
skill this exercise teaches.

```
mforth check forth-102/03-between.fs
→ ✓ forth-102/03-between — 5/5 cases pass
```

## What you learned

- `-`, `*`, `/`, and `MOD` round out arithmetic; `MOD` gives the
  remainder and powers divisibility tests.
- The six comparison words (`< > = <> <= >=`) each pop two values and
  push a **flag**.
- A flag is just a number: **`0` is false, `1` is true**. No separate
  boolean type.
- `AND`, `OR`, and `NOT` combine flags into compound tests.

## Next

[Chapter 5 — Branching](05-branching.md). Now that you can produce a
flag, `IF ... ELSE ... THEN` lets your program *act* on it — running
one piece of code or another depending on the answer.
