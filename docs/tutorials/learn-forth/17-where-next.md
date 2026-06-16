# 17 — Where next

> **You will:** take a controller out of the simulator and into the
> game — compile it to paste-ready mlog, see what the optimizer does for
> you, and get pointed at the tutorials, references, and editor tooling
> that pick up where this series ends.

You have come a long way from `5 3 +`. You can think in stacks, define
words, branch and loop, hold state, talk to message blocks, sense the
world, control blocks, and assemble all of it into a real control loop —
the [capstone sorter](16-capstone.md) was a genuine Mindustry program.

So far everything has run in the **simulator** — `mforth run` and
`mforth check` against a Python mock of the Mindustry world. That is the
teaching surface, and it is deliberately the *same* behaviour you get in
the game. The last step is to leave the simulator behind and produce mlog
you can paste into an actual logic processor.

## Compile to mlog

`mforth compile` turns a `.fs` file into mlog text. Take the pump
controller you wrote in [chapter 15](15-control-loop.md), trimmed to just
the decision and action:

```forth
\ A pump controller, ready to compile.
: pump-controller ( level -- )
  20 < IF
    pump 1 CONTROL-ENABLED
  ELSE
    pump 0 CONTROL-ENABLED
  THEN
;

10 pump-controller
```

with its sidecar naming the switch:

```toml
[links.pump]
type   = "switch"
target = "switch1"
```

Compile it:

```bash
mforth compile pump-controller.fs -o pump-controller.mlog
cat pump-controller.mlog
```

You get six instructions of paste-ready mlog:

```
set s0 10
op lessThan s0 s0 20
jump 5 equal s0 0
control enabled pump 1 0 0 0
jump 6 always 0 0
control enabled pump 0 0 0 0
```

That is it — select all, paste it into a logic processor's editor in
Mindustry, link the switch you called `pump` (the sidecar bound it to the
in-game block `switch1`), and the processor runs your controller on its
own auto-loop. The `s0` is a **stack slot** the compiler assigned for
you; your source never named it. Notice your word `pump-controller` is
*gone* — v1 mforth **inlines** every definition, so a named word costs
nothing at runtime. It is purely a tool for *your* thinking.

## What the optimizer did

mforth compiled that with optimization **on by default**. Compare it to
the unoptimized version:

```bash
mforth compile pump-controller.fs -O0 -o raw.mlog
```

`-O0` emits seven instructions instead of six — it spells out
`set s1 20` then `op lessThan s0 s0 s1`, loading the constant `20` into
its own slot first. The default (`-Ofast`) folds the `20` straight into
the comparison: `op lessThan s0 s0 20`. One instruction saved on a
toy program; on a real controller these savings compound, and on a logic
processor — which runs a fixed number of instructions per tick — fewer
instructions means a faster loop.

There are four optimization levels:

| Flag | What it does |
|------|--------------|
| `-O0` | nothing — the literal, unoptimized lowering |
| `-O1` | constant folding + dead-code elimination |
| `-Ofast` | **(default)** `-O1` plus common-subexpression elimination and loop-invariant code motion — optimizes for **speed** |
| `-Osize` | `-Ofast` plus subroutine emission, only when a program is too big to fit a processor |

mforth's rule is **fast before small**: the default makes your loop quick,
and only falls back to size tricks when a program would not otherwise fit.
For the full story see
[Reference: optimization levels](../../reference/optimization-levels.md).

## The next tutorial

This series taught you Forth *using* Mindustry. The companion tutorial,
[**Writing mforth for Mindustry**](../writing-mforth-for-mindustry.md),
goes the other way — it assumes you can already think in stacks (you can
now) and focuses on the *Mindustry* craft: it ladders through six
programs that port real hand-written mlog scripts from the community wiki
and sets the compiled mforth side-by-side with the original, so you can
see exactly what the dialect buys you. The sorter you built in
[chapter 16](16-capstone.md) is one of its set pieces — you will recognize
it. That is the natural next read.

## Reference and tooling

When you are writing your own controllers and want details rather than a
guided path:

- **[Reference: the dictionary](../../reference/dictionary.md)** — every
  word mforth knows, with its stack effect and the mlog it lowers to.
  Every `@`-property you can `SENSOR`, every `CONTROL-*` sub-command,
  every item, liquid, unit, and block handle. This is the page to keep
  open.
- **[Reference index](../../reference/index.md)** — the CLI flags, the
  `.world.toml` sidecar schema, the event types, the mlog instruction
  set.
- **[How-to: use with Helix](../../how-to/use-with-helix.md)** — the
  language server gives you the stack-effect-on-hover and the
  compile-time diagnostics inline as you type, instead of only when you
  run a check. If you write more than a couple of programs, set this up.
- **[Explanation: why mforth](../../explanation/why-mforth.md)** — the
  *why* behind the choices this tutorial took for granted: why postfix,
  why static stack analysis, why v1 is cell-free, why the REPL and the
  compiled mlog are guaranteed to agree.

## What you can build today

With v1 mforth you can write any **block-side** controller: sense any
block property, branch on it, drive any block (`CONTROL-ENABLED`,
`CONTROL-CONFIG`, and the rest), hold state across ticks with a
`VARIABLE`, print to message blocks, and pace with `WAIT`. The wiki's
*Just Charge*, *All In*, *Sorter Picker*, and friends are all in reach —
the companion tutorial ports three of them.

What is *not* here yet is **unit control** — binding and commanding units
(`ubind`, `ucontrol`) — and **memory cells** for cross-processor state.
Those are the v2 north star, and when they land this tutorial will get a
sequel. Until then, you have everything you need to automate a factory.

Welcome to Forth. Go build something.
