# Getting started

> **You will:** install mforth, run a one-line program against the
> host REPL simulator, and compile it to paste-ready mlog. About
> five minutes.
>
> **You will need:** Python 3.11 or newer, `git`, and a terminal.

This is the shortest path from `git clone` to *something mforth ran
and something mforth compiled*. The longer
[Writing mforth for Mindustry](./writing-mforth-for-mindustry.md)
tutorial picks up where this one ends — it walks you through six
parts that ladder from "hello, message block" up to porting a
hand-written mlog script from the Mindustry wiki.

## 1. Install

If you have not already, follow the
[Install how-to](../how-to/install.md). Come back here when
`mforth --help` prints a usage line that names `repl`, `run`, and
`compile` as subcommands.

## 2. Run your first program

Make a file `hello.fs` anywhere on disk:

```forth
S" hello, mforth" PRINT
display PRINTFLUSH
```

And a sidecar `hello.world.toml` next to it (same basename):

```toml
[links.display]
type   = "message"
target = "message1"

[clock]
ipt      = 8
realtime = false
```

The sidecar tells mforth which Mindustry block the name `display`
refers to — here, the in-game message block labelled `message1`.

Run it through the host REPL simulator:

```bash
mforth run --no-loop hello.fs
```

The simulator processed the script silently — it stored
`"hello, mforth"` into the simulated message block and stopped.
(Drop `--no-loop` and the program would auto-repeat: mlog's "fall
off the end and restart" semantics.)

## 3. Compile to mlog

```bash
mforth compile hello.fs -o hello.mlog
cat hello.mlog
```

You will see two lines of mlog:

```
print "hello, mforth"
printflush message1
```

That is paste-ready: open a logic processor in Mindustry, link a
message block to it, paste, and the block will read
`hello, mforth`.

## What you have now

- A working mforth install (`mforth --help` works).
- A `.fs` file that ran end-to-end against the simulator.
- A `.mlog` file you could paste into a Mindustry logic processor.

## What to read next

- [**Writing mforth for Mindustry**](./writing-mforth-for-mindustry.md)
  — the six-part tutorial. Starts at the same `hello` shape and
  ends with a side-by-side port of a wiki-catalogued mlog script.
  About one hour total; do the parts in order.
- [How-to: Install](../how-to/install.md) — re-installing,
  upgrading, or installing on a second machine.
- [Reference](../reference/index.md) — the full surface (every
  Forth word, every sidecar field, every CLI flag).
- [Explanation: Why mforth](../explanation/why-mforth.md) — why
  mforth is shaped the way it is.
