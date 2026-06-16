# Learn Forth with mforth

A from-zero introduction to **Forth** — the language — using mforth and its
built-in Mindustry simulator. You do **not** need to know Forth, and you do
**not** need to have played Mindustry. By the end you'll think in stacks,
define your own words, and write small controllers that react to a simulated
world.

This is the *prequel* to
[Writing mforth for Mindustry](../writing-mforth-for-mindustry.md): that
tutorial assumes you already think in stacks; this one teaches you how.

## How the exercises work

Every chapter ends with a few short exercises, and **each one checks itself**.
You write a `.fs` file, then run:

```text
$ mforth check my-solution.fs
✓ forth-101/01-double — 3/3 cases pass
```

or, when something's off:

```text
✗ forth-101/01-double — 1/3 cases pass
  case 1: 5 double . → printed "15", expected "10"
  hint: DUP makes a second copy, then + adds them.
```

Handy flags:

- `mforth check --list` — see every bundled exercise.
- `mforth check --scaffold <id>` — write a starter file for an exercise.
- `mforth check --solution <id>` — reveal the reference answer when you're stuck.

Your solution carries one marker line so the checker knows which exercise it
is — for example `\ @exercise forth-101/01-double`. The `--scaffold` command
writes that line for you.

## Part I — Thinking in Forth

No game knowledge needed. Each concept is small, and every exercise is
checkable.

| # | Chapter | You'll learn |
|---|---------|--------------|
| 1 | [The stack & postfix](01-stack.md) | Numbers, `+ - * /`, `.`, and why `3 4 +` |
| 2 | [Juggling the stack](02-juggling.md) | `DUP DROP SWAP OVER ROT` |
| 3 | [Defining words](03-defining.md) | `: name … ;` and naming things |
| 4 | [Arithmetic & truth](04-arithmetic.md) | `MOD`, `< > =`, `AND OR NOT` |
| 5 | [Branching](05-branching.md) | `IF / ELSE / THEN` |
| 6 | [Looping](06-looping.md) | `BEGIN/UNTIL`, `DO/LOOP`, `I` |
| 7 | [State & variables](07-state.md) | `VARIABLE`, `@`, `!` |
| 8 | [Output](08-output.md) | `S" …"`, `PRINT`, and `.` |
| 9 | [Factoring](09-factoring.md) | Composing small, well-named words |
| 10 | [Words that make words](10-defining-words.md) | `CREATE`, `,`, `DOES>`, and building `CONSTANT` yourself |
| 11 | [Macros](11-macros.md) | `MACRO: name … ;` — compile-time substitution |

## Part II — Forth in the simulator

Now we make it real: read and react to a simulated Mindustry world.

| # | Chapter | You'll learn |
|---|---------|--------------|
| 12 | [Meet the simulator](12-simulator.md) | The world, blocks, the `.world.toml` sidecar |
| 13 | [Reading the world](13-sensing.md) | `SENSOR` and `@`-properties |
| 14 | [Acting on the world](14-controlling.md) | `CONTROL-ENABLED` & friends |
| 15 | [A control loop](15-control-loop.md) | Sense → decide → act → `WAIT` |
| 16 | [Capstone: the sorter](16-capstone.md) | Build a real controller, milestone by milestone |
| 17 | [Where next](17-where-next.md) | Compile to real mlog, optimize, and keep going |

Ready? Start with [Chapter 1 — The stack & postfix](01-stack.md).
