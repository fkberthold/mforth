# Chapter 5 — Branching

> **You will:** make your programs *decide* — run one piece of code or
> another depending on a flag — with `IF`, `ELSE`, and `THEN`. You will
> meet the one rule that keeps branching honest: both paths must leave
> the stack the same depth.
>
> **Before this:** [Chapter 4 — Arithmetic and truth](04-arithmetic.md).
> You can produce a flag (`0` or `1`) from a comparison and combine
> flags with `AND`/`OR`/`NOT`.

A flag on its own does nothing. Branching is how you *act* on it.

## `IF ... THEN`

`IF` pops a flag. If the flag is true (nonzero), the code between `IF`
and `THEN` runs. If it is false (`0`), that code is skipped. `THEN`
just marks where the skipped section ends — think of it as
"continue here", not as the English "then".

```forth
: pay ( n -- n ) DUP 100 > IF 10 - THEN ;
```

`pay` gives a 10-credit discount, but only on amounts over 100. Walk
the stack for `150 pay`:

| token | stack | what happened |
|-------|-------|---------------|
| `DUP` | `150 150` | copy, so the test does not consume `n` |
| `100 >` | `150 1` | 150 > 100 is true |
| `IF` | `150` | flag is true → run the body |
| `10 -` | `140` | subtract the discount |
| `THEN` | `140` | branch rejoins here |

```forth
150 pay .
50 pay .
```

prints

```
140
50
```

`150` was over 100, so it lost 10. `50` was not, so `IF` skipped the
`10 -` entirely and it came through unchanged.

## `IF ... ELSE ... THEN`

Often you want to do *one* thing or *another*, never neither. `ELSE`
gives the false case its own code:

```forth
: label ( n -- n ) DUP 0 < IF DROP -1 ELSE DROP 1 THEN ;
```

If `n` is negative, the result is `-1`; otherwise `1`.

```forth
-5 label .
5 label .
```

prints

```
-1
1
```

The flag picks exactly one of the two branches. `THEN` closes the whole
construct — both the `IF` side and the `ELSE` side rejoin there.

## The one rule: both branches leave the same depth

Here is the rule that makes branching safe, and it is checked for you
*before your program ever runs*: **every path through an `IF` must
leave the stack at the same depth.**

Look back at `label`. The `IF` branch does `DROP -1` — drops one value,
pushes one, net effect "replace the top". The `ELSE` branch does
`DROP 1` — same net effect. Both leave the stack one deep. Balanced.

If they disagree, mforth refuses to compile and tells you where. This
broken word pushes a value on the true side but not the false side:

```forth
: oops ( n -- ? ) 0 > IF 42 THEN ;
```

mforth rejects it:

```
IF branches leave stack at different depths (then delta=+1, else delta=+0)
```

The true side pushes one value (`42`); the false side — skipping the
body — pushes nothing. The two paths disagree by one. That is not a
runtime crash you have to hunt down later — it is caught
the moment you check the file. The fix is to make both paths agree:
give the skip-path something to leave too, or have the `IF` body leave
nothing extra. This is the same static stack discipline from Chapter 3,
now doing real work: it guarantees that no matter which way a branch
goes, the code *after* `THEN` always finds the stack it expects.

A handy consequence: an `IF` with **no** `ELSE` (like `pay` above) must
leave the stack *unchanged* in depth, because the false side — skipping
the body — changes nothing. `pay`'s body is `10 -`: it pushes `10`,
then `-` consumes that `10` together with the value already on the
stack and pushes one result. The body's net depth change is zero, so
both paths agree. Balanced.

## Nesting

Branches nest. To make a three-way decision, put an `IF` inside the
`ELSE` of another:

```forth
: classify ( n -- s )
  DUP 0 > IF
    DROP 1
  ELSE
    0 < IF -1 ELSE 0 THEN
  THEN ;
```

The outer `IF` handles "positive". Its `ELSE` still has the original
`n` on the stack, so the inner `IF` can test it again for "negative",
falling through to `0` for "exactly zero". Each leaf leaves one value,
so the whole tree is balanced. (You will write this one yourself — it
is the `sign` exercise.)

## Exercises

Same routine as before: write a `.fs`, run `mforth check <file>`, look
for the `✓`. `--scaffold <id>` gives you a starter; `--solution <id>`
shows a reference answer if you are stuck.

### Exercise 1 — `abs` ( n -- |n| )

Leave the absolute value: `n` unchanged when it is zero or positive,
negated when it is negative.

*Hint:* `DUP 0 <` tests a copy without consuming `n`. To negate inside
the `IF`, subtract from zero: `0 SWAP -` turns `n` into `0 - n`. Make
sure the branch leaves exactly one value, matching the skip path.

```
mforth check forth-102/04-abs.fs
→ ✓ forth-102/04-abs — 4/4 cases pass
```

### Exercise 2 — `max` ( a b -- max )

Leave the larger of the two values.

*Hint:* `OVER OVER` copies both values up so you can compare them
without consuming the originals, leaving `( a b a b )`; `<` turns the
top pair into a flag. Then `IF SWAP THEN` arranges the larger one on
top, and `DROP` discards the smaller.

```
mforth check forth-102/05-max.fs
→ ✓ forth-102/05-max — 4/4 cases pass
```

### Exercise 3 — `sign` ( n -- s )

Leave `1` if `n` is positive, `-1` if negative, `0` if zero. This is
the nested `IF/ELSE` shape shown above — work it out before peeking.

*Hint:* outer test `DUP 0 >` → `DROP 1`. In the `ELSE`, the original
`n` is still there, so test again with `0 <`: `-1` if true, `0` if not.
Every branch must leave exactly one value.

```
mforth check forth-102/06-sign.fs
→ ✓ forth-102/06-sign — 3/3 cases pass
```

## What you learned

- `IF` pops a flag and runs the following code only when it is true;
  `THEN` marks where the branch rejoins.
- `ELSE` gives the false case its own code.
- **Every path through an `IF` must leave the stack the same depth** —
  checked before the program runs, so an imbalance is a clear
  compile-time error, never a mystery crash.
- Branches nest: an `IF` inside an `ELSE` makes a multi-way decision.

## Next

[Chapter 6 — Looping](06-looping.md). Decisions let your program take
one of two paths *once*. Loops let it repeat a path many times — count
up, count down, and accumulate a result.
