# Chapter 6 — Looping

> **You will:** repeat code — with `BEGIN ... UNTIL` when you loop
> until a condition holds, and with `DO ... LOOP` when you count over a
> range. You will use `I` to read the current loop index, and learn why
> a `DO` loop can run its body *zero* times.
>
> **Before this:** [Chapter 5 — Branching](05-branching.md). You can
> produce a flag and act on it with `IF`. A loop is the natural next
> step: act on a flag *over and over*.

There are two looping shapes in mforth. Reach for whichever fits the
question you are asking.

## `BEGIN ... UNTIL` — loop until a condition

`BEGIN` marks the top of the loop. The body runs, then `UNTIL` pops a
flag: if it is **false** (`0`), jump back to `BEGIN` and go again; if it
is **true**, fall through and stop. So the body always runs at least
once, and *you* are responsible for eventually pushing a true flag.

```forth
: tick3 ( -- ) 0 BEGIN 1 + DUP . DUP 3 = UNTIL DROP ;
```

Trace it. Start with `0`. Each pass adds 1, prints the new value, then
tests whether it has reached 3:

```forth
tick3
```

prints

```
1
2
3
```

After printing `3`, the test `DUP 3 =` pushes a true flag, `UNTIL`
stops, and `DROP` clears the leftover counter so the stack ends empty —
which is what the declared effect `( -- )` promises. (Without that
`DROP`, the word would leave a stray `3` behind and fail its stack
check.)

The pattern is: set up a counter, loop, change the counter, test for
the stopping condition. If you never make the condition true, the loop
runs forever — the same hazard as in any language.

## `DO ... LOOP` — count over a range

When you know the range up front, `DO ... LOOP` is cleaner. It takes
*two* numbers off the stack — a limit and a start — and counts from the
start up to **but not including** the limit:

```
limit start DO  ... body ...  LOOP
```

Inside the body, the word `I` pushes the current index. So this prints
the squares of 1, 2, and 3:

```forth
: squares ( -- ) 4 1 DO I I * . LOOP ;
```

`4 1 DO` counts the index from 1 up to 3 (the limit `4` is excluded).
Each pass, `I I *` squares the index and `.` prints it:

```forth
squares
```

prints

```
1
4
9
```

Note the operand order: the **limit goes on first, the start on top**,
so `4 1 DO` reads as "from 1, up to 4". That mirrors how the two values
sit on the stack when `DO` pops them.

## `I` is the loop counter

`I` ( -- n ) pushes the index of the innermost `DO ... LOOP`. It is
valid only inside the loop body. A common use is just printing the
count:

```forth
: count ( n -- ) 0 DO I . LOOP ;
```

`n 0 DO` counts from 0 up to `n-1`:

```forth
3 count
```

prints

```
0
1
2
```

(There is also `J`, which reads the *next-outer* loop's index when you
nest one `DO` loop inside another. You will not need it in this
chapter.)

## Zero-trip loops

Here is the one surprise. If the start has already reached the limit
when `DO` runs, the body runs **zero times**:

```forth
: z ( -- ) 5 5 DO I . LOOP 99 . ;
```

`5 5 DO` is "from 5, up to 5" — an empty range. The `I .` never runs;
only the `99 .` after the loop does:

```forth
z
```

prints

```
99
```

This is not a quirk to work around — it is what you *want*. It means a
loop over an empty range simply does nothing, with no special case to
write. You will lean on it in the exercises: `factorial` of `0` loops
zero times and the seed value falls straight through, giving the right
answer (`0! = 1`) for free.

## Building an accumulator

The most common loop job is to fold a range into a single result: a
sum, a product, a count. The recipe is always the same three moves:

1. **Seed** an accumulator on the stack (`0` for a sum, `1` for a
   product).
2. **Loop** over the range, combining `I` into the accumulator each
   pass.
3. The accumulator is your result when the loop ends.

You already saw the seed-and-fold idea in `tick3`. The next two
exercises apply it with `DO ... LOOP`.

## Exercises

Same routine: write a `.fs`, run `mforth check <file>`, look for the
`✓`. `--scaffold <id>` gives a starter; `--solution <id>` shows a
reference answer.

A note on the looping exercises: the checker compares **printed
output**. For `sum` and `factorial` you compute a result and the
driver prints it with `.`. For `countdown` your word does the printing
itself, so the expected output is the whole sequence of lines.

### Exercise 1 — `sum` ( n -- total )

Leave the sum `1 + 2 + ... + n`. (For `n = 0`, the sum is `0`.)

*Hint:* seed with `0 SWAP` to get `( 0 n )`. A `DO` limit is exclusive,
so loop `1 + 1 DO ... LOOP` to cover `1..n`. Inside, `I +` adds the
index to the running total. For `n = 0` the range is empty (zero-trip)
and the seed `0` falls through.

```
mforth check forth-102/07-sum.fs
→ ✓ forth-102/07-sum — 4/4 cases pass
```

### Exercise 2 — `factorial` ( n -- n! )

Leave `1 * 2 * ... * n`. By convention `0! = 1`.

*Hint:* the same shape as `sum`, but seed with `1` (the identity for
multiplication) and use `*`: `1 SWAP 1 + 1 DO I * LOOP`. The zero-trip
case hands you `0! = 1` automatically.

```
mforth check forth-102/08-factorial.fs
→ ✓ forth-102/08-factorial — 4/4 cases pass
```

### Exercise 3 — `countdown` ( n -- )

Print `n`, then `n-1`, down to `1`, one value per line. Assume
`n >= 1`. Leave nothing on the stack.

*Hint:* `BEGIN ... UNTIL` fits a count-*down* nicely. Each pass:
`DUP .` prints a copy of the counter, `1 -` decrements, `DUP 0 =`
builds the stop flag. After `UNTIL` you still hold the `0` — `DROP` it
so the stack ends empty.

```
mforth check forth-102/09-countdown.fs
→ ✓ forth-102/09-countdown — 3/3 cases pass
```

## What you learned

- `BEGIN ... UNTIL` repeats the body until you push a true flag at
  `UNTIL`; the body always runs at least once.
- `DO ... LOOP` counts over a range: `limit start DO ... LOOP` runs the
  index from `start` up to **but not including** `limit`.
- `I` pushes the current loop index inside a `DO` body.
- A `DO` loop over an empty range is a **zero-trip** loop — the body
  never runs, which is exactly what you want for edge cases like
  `factorial 0`.
- The accumulator pattern — seed, fold the range, read the result —
  is the backbone of loop-based computation.

## Next

You have finished **Part I**. You can define words, do arithmetic, ask
questions, branch, and loop — the whole computational core of Forth.

[Chapter 7 — State](07-state.md) opens **Part II**: variables that
remember a value between runs, the first step toward programs that
react to a changing world instead of computing one answer and stopping.
