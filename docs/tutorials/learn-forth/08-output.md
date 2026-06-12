# 8. Output — strings, `PRINT`, and `.`

> **You will:** push a string with `S" ..."`, learn how `PRINT`
> differs from `.`, and build a labelled message like `score=42` out
> of two prints.
>
> **Before this:** [7. State](07-state.md) gave you variables to
> remember numbers. Now we make those numbers *say what they are*.

You have been printing with `.` since [chapter 1](01-stack.md). It is
handy but blunt: `.` pops a number and prints it, followed by a space,
and that is all it can do. A bare `7` on a screen tells you nothing
about *what* `7` means. Real output pairs a number with a label —
`score=7`, `count=3`, `temp=20`. For that you need two more tools:
**strings** and **`PRINT`**.

## Strings with `S" ..."`

`S" ..."` pushes a string onto the stack. Mind the spacing: `S"` is a
word, so it needs a space after it, and the string runs up to the
closing `"`.

```forth
S" hello"
```

That leaves the string `hello` on the stack (stack effect `( -- str )`),
the same way `7` leaves a number. It is just a value — nothing prints
yet. To get it onto the screen you hand it to a printing word.

## `PRINT` versus `.`

mforth has two words that put something on the screen, and the
difference is the whole lesson of this chapter:

| Word | Stack effect | What it prints |
|------|-------------|----------------|
| `.`     | `( n -- )` | a **number**, then a trailing space |
| `PRINT` | `( v -- )` | **any value** — number *or* string — no extra space |

`.` is for numbers only. `PRINT` is the general one: it takes whatever
is on top — a string from `S" ..."`, or a number — and queues it to the
output. So a string can only go out through `PRINT`:

```forth
S" hello, mforth" PRINT
```

That prints `hello, mforth`. And a number can go through either —
`42 .` and `42 PRINT` both put `42` on the screen (the difference is
only the trailing space `.` adds).

> **Whole numbers print clean.** A value like `3.0` prints as `3`,
> not `3.0` — mforth follows mlog's rule that whole-number values
> render without a trailing `.0`. A genuinely fractional value like
> `3.5` prints as `3.5`.

## Building a labelled message

Here is the key idea, and it surprises people coming from other
languages. **Each `PRINT` (and each `.`) is a separate output.** To
show `score=42` you print the label, *then* print the number — two
prints in a row:

```forth
S" score=" PRINT
42 PRINT
```

On a Mindustry message block those two prints land side by side and
read as `score=42`. This is not a quirk of mforth — it is exactly how
mlog's print buffer works: each `print` instruction *appends* to a
buffer, and one `printflush` later pushes the whole buffer to the
block. Label, then value, then flush.

When the checker runs your code it records each print as its own entry
in an ordered list. So the program above produces **two** recorded
outputs, in order: `score=` and then `42`. Keep that in mind when you
read an exercise's expected output — a label-plus-value message is two
entries, not one joined string.

Wrap the pattern in a word and you have a reusable reporter:

```forth
: report ( n -- ) S" score=" PRINT PRINT ;
42 report
```

Trace the body. When `report` runs, `n` is already on the stack
(`42`). `S" score="` pushes the label *on top* of it; `PRINT` prints
the label and removes it; the second, bare `PRINT` prints what is now
on top — your `n`. Two prints: `score=` then `42`.

Combine it with [chapter 7](07-state.md)'s state and you can announce a
remembered value:

```forth
VARIABLE count
3 count !
: show-count ( -- ) S" count=" PRINT count @ PRINT ;
show-count
```

`show-count` prints the label, then fetches `count` and prints that —
`count=` then `3`.

## Exercises

As before: write a `.fs`, then `mforth check my-answer.fs`. A pass
prints `✓ <id> — N/N cases pass`. Use `mforth check --scaffold <id>`
for a starter and `mforth check --solution <id>` if you are stuck.

Note the expected output in these exercises is a **list** — one entry
per print, in order. `score=42` on screen is the two entries
`score=` and `42`.

### Exercise 1 — `report` ( n -- )

`id: forth-103/03-report`

Define `report`: print the label `score=`, then print the number `n`.
`report` consumes `n` and leaves nothing.

```forth
\ @exercise forth-103/03-report
: report ( n -- ) ... ;
```

`42 report` should produce `score=` then `42`.

### Exercise 2 — `show-count` ( -- )

`id: forth-103/04-show-count`

A variable `count` holds a number. Define `show-count`: print the
label `count=`, then print the value stored in `count`. It takes
nothing and leaves nothing. This is chapter 7's state meeting chapter
8's output — fetch the variable, print the pieces.

`3 count !  show-count` should produce `count=` then `3`.

## What you learned

- `S" ..."` pushes a string onto the stack.
- `.` prints a **number** (plus a trailing space); `PRINT` prints
  **any value**, string or number, with no extra space. Strings must
  go through `PRINT`.
- A labelled message is **two prints** — label, then value — because
  each print is a separate output that the buffer concatenates.

Next: [9. Factoring](09-factoring.md) — the Forth habit of breaking a
fat definition into small, well-named words that read like sentences.
