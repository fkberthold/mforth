# 10. Meet the simulator

> **You will:** leave the abstract stack behind and put your first
> words on a (simulated) Mindustry message block — using `PRINT`,
> `PRINTFLUSH`, and a `.world.toml` sidecar that binds a name in your
> source to a block in the game. About ten minutes.
>
> **You will need:** the first nine chapters. You should be comfortable
> with definitions (`: … ;`), `S" …"` strings, and `PRINT` from
> [chapter 8, Output](./08-output.md).

Part I taught you Forth as a language: a stack, words that consume and
produce, branching, looping, state, factoring. Every exercise so far ran
in a vacuum — numbers in, numbers out. That was the point. You now think
in stacks.

Part II spends that fluency on the thing mforth is *for*: controlling
blocks in Mindustry. Nothing about the language changes. The stack is
still the stack; `IF` still branches; a definition is still a definition.
What changes is that some words now talk to a **world** — a simulated
Mindustry processor with blocks wired to it. This chapter introduces that
world and gets one string onto one block. The next two chapters teach you
to *read* the world ([chapter 11](./11-sensing.md)) and *act* on it
([chapter 12](./12-controlling.md)).

## The MockWorld

When you run an mforth program, it executes against a **MockWorld**: a
Python stand-in for a Mindustry logic processor and the blocks linked to
it. The MockWorld is deterministic — it behaves the same way every time —
which is exactly what lets `mforth check` grade simulator exercises the
same way it graded the arithmetic ones.

A real processor has *links*: message blocks, switches, vaults, drills,
anything you've connected to it in-game. The MockWorld models those
links too. But your `.fs` source never hard-codes an in-game block name.
Instead it refers to a **stable mforth-name**, and a side file binds that
name to the real block. That side file is the sidecar.

## The `.world.toml` sidecar

Every `<name>.fs` may have a sibling `<name>.world.toml` — same basename,
same directory. It declares the blocks your program links to. Here is the
smallest useful one:

```toml
[links.display]
type   = "message"
target = "message1"
```

Read it as: *"the name `display` in my source is a message block; in the
game it shows up as `message1`."* The left side of `[links.display]` —
`display` — is the name your Forth code uses. The `target = "message1"`
is the in-game label. You bind by name, so re-linking blocks in a
different order never breaks your program. (There is a second, fragile
addressing mode by slot `index`; tutorials never use it. The
[sidecar schema reference](../../reference/sidecar-schema.md) covers both.)

Why a side file instead of writing `message1` directly in your `.fs`? So
the source stays portable: the same program drops onto any processor,
and only the one-line sidecar changes to match that processor's wiring.

## Your first on-screen output

Two words put text on a message block:

- `PRINT ( v -- )` queues a value into the processor's **print buffer**.
  It does *not* show anything yet — it's building up a message.
- `PRINTFLUSH ( block -- )` flushes the whole buffer onto a message
  block, replacing whatever was there.

So the pattern is always *queue, queue, …, flush*. Here is a complete
program. Save it as `hello.fs`:

```forth
\ hello.fs — one string onto a message block.
S" reactor online" PRINT
display PRINTFLUSH
```

and `hello.world.toml` beside it:

```toml
[links.display]
type   = "message"
target = "message1"
```

Run a single pass through the simulator:

```bash
mforth run --no-loop hello.fs
```

Nothing prints to your terminal — the text went onto the *simulated*
message block, not stdout. That's faithful: in-game, a message block
shows text on the block, not in a console. To see what would land
in-game, compile it instead:

```bash
mforth compile hello.fs -o hello.mlog
cat hello.mlog
```

You get exactly two mlog instructions — `print "reactor online"` and
`printflush message1` — paste-ready for a logic processor. (Compiling is
[chapter 15](./15-where-next.md)'s topic; here it's just a peek.)

## Buffer, then flush: the two-line readout

`PRINT` queues *one value at a time*, and you can queue several before a
single flush. A label plus a value is the everyday shape:

```forth
\ A label and a number, flushed together.
S" map width = " PRINT
@mapw PRINT
display PRINTFLUSH
```

`@mapw` is a built-in that reports the map's width — in the simulator it
is a deterministic stand-in value (40), so the readout is reproducible.
(`@mapw` is one of many `@`-prefixed built-ins; the next chapter is all
about the ones that read *blocks*.) Two `PRINT`s, then one `PRINTFLUSH`:
the block ends up showing `map width = ` followed by `40`.

One thing worth pinning down now, because the checker depends on it: each
`PRINT` is its own unit of output. `mforth check` sees a *list* of
printed pieces — here `"map width = "` then `"40"` — not one concatenated
string. When an exercise expects two pieces, you write two `PRINT`s.

## Exercises

Write each solution in its own `.fs` file, starting with the
`\ @exercise <id>` marker line shown in the prompt, then run
`mforth check <file>`. A green `✓` means your word behaves exactly as
asked. Stuck? `mforth check --scaffold <id>` writes a starter stub, and
`mforth check --solution <id>` reveals the reference answer.

These two exercises bundle their own sidecar — you don't write a
`.world.toml`; the checker supplies one that binds `display` to a message
block.

### Exercise 10.1 — `greet` ( -- )

`PRINT` the string `online` and flush it to the message block `display`.

```
\ @exercise sim-101/01-greet
```

```bash
mforth check greet.fs
```

```
✓ sim-101/01-greet — 1/1 cases pass
```

### Exercise 10.2 — `readout` ( -- )

`PRINT` the label `width=`, then `PRINT` the map width `@mapw`, then flush
to `display`. Remember: two separate `PRINT`s.

```
\ @exercise sim-101/02-label
```

```bash
mforth check readout.fs
```

```
✓ sim-101/02-label — 1/1 cases pass
```

---

You can now get text onto a block. But a controller that only *writes*
is half a controller — the interesting programs *react*. Next:
[chapter 11, Reading the world](./11-sensing.md), where `SENSOR` pulls a
block's properties onto the stack so your `IF`s have something real to
branch on.

(For the polished, end-to-end version of this same material — porting
real wiki scripts, with side-by-side mlog — see
[Writing mforth for Mindustry](../writing-mforth-for-mindustry.md). This
series builds the Forth underneath it.)
