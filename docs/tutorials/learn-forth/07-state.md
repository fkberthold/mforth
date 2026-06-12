# 7. State — variables that remember

> **You will:** declare a named cell with `VARIABLE`, read it with
> `@`, write it with `!`, and build words that update remembered
> state — a counter and a running total.
>
> **Before this:** [6. Looping](06-looping.md) gave you `BEGIN`,
> `DO`, and `I`. [3. Defining words](03-defining.md) gave you
> `: ... ;`. This chapter assumes both.

Everything so far has lived on the stack, and the stack is *fleeting*:
a value sits there only until some word eats it. That is exactly what
you want most of the time — but sometimes a program needs to
**remember** a number between one moment and the next. A score that
ticks up. A total that grows. A high-water mark.

For that you need a *variable*: a named box that holds one value and
keeps it until you change it.

## A variable is a named cell

You make one with `VARIABLE`:

```forth
VARIABLE count
```

That declares a cell called `count`. It does nothing to the stack
(`VARIABLE` has stack effect `( -- )`); it just teaches mforth a new
name. From now on `count` refers to that box.

Two words work the box:

| Word | Stack effect | What it does |
|------|-------------|--------------|
| `@`  | `( addr -- value )` | *fetch*: read the value out of the cell |
| `!`  | `( value addr -- )` | *store*: write a value into the cell |

`@` is pronounced "fetch" and `!` is pronounced "store" (or "bang").
Both take the variable's name on the stack to say *which* cell. Read
them right-to-left as English: `count @` is "count, fetch"; `42 count !`
is "forty-two, count, store".

Here is the whole life-cycle in one program:

```forth
VARIABLE count
0 count !
count @ .
```

Walk it token by token:

| token | stack after | what happened |
|-------|-------------|---------------|
| `VARIABLE count` | *(empty)* | declared the cell `count` |
| `0`              | `0`       | pushed `0` |
| `count`          | `0 count` | pushed the cell name |
| `!`              | *(empty)* | stored `0` into `count` |
| `count`          | `count`   | pushed the cell name |
| `@`              | `0`       | fetched `count`'s value (`0`) |
| `.`              | *(empty)* | printed `0` |

So `0 count !` initializes the box to zero, and `count @` pulls that
zero back out. A freshly declared variable has no guaranteed value
until you store one, so initializing it first — `0 count !` — is a
habit worth keeping.

## Updating a variable

The useful move is *read, change, write back*. To add one to `count`:

```forth
count @ 1 + count !
```

Read it as a sentence: fetch `count`, add `1`, store the result back
into `count`. The old value comes out, a new value goes in.

| token | stack after | what happened |
|-------|-------------|---------------|
| `count @` | `0` | fetched the current value |
| `1`       | `0 1` | pushed `1` |
| `+`       | `1` | added |
| `count !` | *(empty)* | stored `1` back into `count` |

Wrap that in a word and you have a reusable *bump*:

```forth
VARIABLE count
0 count !
: bump ( -- ) count @ 1 + count ! ;
bump bump bump
count @ .
```

`bump` takes nothing and leaves nothing — its whole job is the
side effect of changing `count`. After three `bump`s the box holds
`3`, and `count @ .` prints it. A word that touches a variable but
leaves the stack untouched is the bread and butter of stateful Forth.

> **mlog note.** In mforth v1 a `VARIABLE` compiles to a plain mlog
> variable — there is no separate memory cell, no address arithmetic.
> `count @` and `count !` fuse to a single `set` instruction each.
> The `( addr -- value )` notation is Forth tradition; under the hood
> the name *is* the storage. (Cells proper are a v2 topic.)

## Exercises

Write each answer in its own `.fs` file and check it:

```bash
mforth check my-answer.fs
```

A pass prints a line like `✓ forth-103/01-bump — 3/3 cases pass`.
Stuck on the starting shape? `mforth check --scaffold <id>` writes a
stub. Truly stuck? `mforth check --solution <id>` reveals the
reference answer.

### Exercise 1 — `bump` ( -- )

`id: forth-103/01-bump`

A variable `count` already exists. Define `bump` so that each call
adds `1` to whatever `count` holds, storing the result back. It takes
nothing and leaves nothing.

```forth
\ @exercise forth-103/01-bump
VARIABLE count
0 count !
: bump ( -- ) ... ;
```

After `bump bump bump`, fetching `count` should give `3`.

### Exercise 2 — `add` ( n -- ) running total

`id: forth-103/02-total`

A variable `total` starts at `0`. Define `add` ( n -- ): fold the
number on the stack into the running total. Each call adds one more
number in; `add` leaves nothing behind.

`5 add  3 add` should leave `total` holding `8`. This is the same
*read–change–write* shape as `bump`, except the amount you add comes
off the stack instead of being a fixed `1`.

## What you learned

- `VARIABLE name` declares a named cell.
- `@` *fetches* a value out; `!` *stores* a value in.
- The core idiom is *read, change, write back*:
  `count @ 1 + count !`.
- A word can exist purely for its effect on a variable, taking and
  leaving nothing on the stack.

Next: [8. Output](08-output.md) — strings with `S" ..."`, and the
difference between `PRINT` and `.`, so your remembered numbers can
say what they are.
