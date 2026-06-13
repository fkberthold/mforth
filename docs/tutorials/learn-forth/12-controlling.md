# 12. Acting on the world

> **You will:** turn a decision into an action with `CONTROL-ENABLED`,
> meet its sibling control words, and write a complete sense → decide →
> act word that flips a switch based on what a vault holds. About ten
> minutes.
>
> **You will need:** [chapter 11](./11-sensing.md) (`SENSOR` and
> turning readings into flags) and `IF / ELSE / THEN` from
> [chapter 5](./05-branching.md).

You can read the world and you can compute a `1`/`0` decision from it.
The third move is acting: telling a block to do something. The simplest
action is turning a block on or off.

## `CONTROL-ENABLED` ( block flag -- )

`CONTROL-ENABLED` takes a block and a flag and enables the block when the
flag is `1`, disables it when `0`:

```forth
miner 1 CONTROL-ENABLED   \ turn the miner on
miner 0 CONTROL-ENABLED   \ turn the miner off
```

`miner` is a sidecar-bound block name (a switch or any controllable
block); the `1` or `0` is the enable flag. Unlike `PRINT`, this word
produces nothing on the stack — it's a pure action, an effect on the
world.

There is one rule that shapes how you call it in v1 mforth: **the flag
must be a literal `0` or `1`**, written right there in the source — not a
value computed on the stack. So you don't push a sensed flag straight
into `CONTROL-ENABLED`; instead you branch, and each arm calls it with a
literal:

```forth
\ Run the miner while the vault has room, stop it when full.
vault1 @totalItems SENSOR
vault1 @itemCapacity SENSOR
< IF   miner 1 CONTROL-ENABLED
  ELSE miner 0 CONTROL-ENABLED
  THEN
```

Sense the count, sense the capacity, compare with `<` to get "has room?",
and let `IF / ELSE / THEN` pick which literal-flag `CONTROL-ENABLED` to
run. (This is why the lifter wants a literal: each branch arm emits one
concrete `control` instruction. The mforth team tracks lifting a
stack-computed flag as a future improvement; for now the `IF/ELSE` is the
idiom, and it reads clearly.)

## The other CONTROL words

`CONTROL-ENABLED` is one of a small family — every block action mforth
exposes is a `CONTROL-` word with a fixed stack effect:

| Word | Stack effect | What it does |
|------|--------------|--------------|
| `CONTROL-ENABLED` | `( block flag -- )` | enable / disable a block |
| `CONTROL-CONFIG`  | `( block value -- )` | configure (e.g. a sorter's item) |
| `CONTROL-SHOOT`   | `( block x y shoot -- )` | aim+fire a turret at a point |
| `CONTROL-SHOOTP`  | `( block unit shoot -- )` | aim+fire at a unit |
| `CONTROL-COLOR`   | `( block r g b -- )` | set an illuminator's color |

You won't need the turret words in this series; they're here so you know
the shape — every one of them ends a sense → decide → act chain the same
way `CONTROL-ENABLED` does. The
[dictionary reference](../../reference/dictionary.md) has the full list.

## Sense → decide → act, in one word

Here is the whole arc in a single definition. It senses whether the vault
is empty, prints the decision so you can watch it, and acts — running the
miner to refill an empty vault, stopping it otherwise:

```forth
: restock ( -- )
  vault1 @totalItems SENSOR 0 =   \ ( -- empty? )
  DUP .                           \ show the flag
  IF   miner 1 CONTROL-ENABLED    \ empty: run the miner
  ELSE miner 0 CONTROL-ENABLED    \ stocked: stop it
  THEN
;
```

The `DUP .` is the same "keep a copy while consuming one" idiom you used
in Part I: `=` leaves one flag, `DUP` makes it two, `.` prints one copy,
and `IF` consumes the other to choose the action. Printing the decision
is also how the checker watches a control word work — `CONTROL-ENABLED`
itself produces no printed output, so a checkable controller surfaces its
*decision* on the way to acting on it. The exercise's sidecar seeds
`vault1` empty (`@totalItems = 0`) — a deliberately drained vault — so
`restock` prints `1` and enables the miner. Re-seed the vault with stock
and the same word prints `0` and stops it.

This is a complete controller. The only thing missing is making it *keep*
running — sensing, deciding, and acting once per tick, forever. That's a
real Mindustry processor's auto-loop, and it's the subject of
[chapter 13, A real control loop](./13-control-loop.md).

## Exercises

Write each `.fs` with its `\ @exercise <id>` marker and run
`mforth check <file>`. `--scaffold <id>` stubs it; `--solution <id>`
reveals the answer. Both exercises bundle a sidecar that **seeds the
vault's readings**, so your word senses real data.

### Exercise 12.1 — `should-run?` ( -- flag )

Leave `1` when `vault1` has room (its `@totalItems` is below its
`@itemCapacity`), else `0` — the flag a miner switch should be set to.
(The sidecar seeds an `80`-of-`100` vault, so it *has* room and the
checker expects `1`. This is the *decision* half; exercise 12.2 acts on
it.)

```
\ @exercise sim-101/05-decide
```

```bash
mforth check should-run.fs
```

```
✓ sim-101/05-decide — 1/1 cases pass
```

### Exercise 12.2 — `restock` ( -- )

`SENSOR` `vault1`'s `@totalItems`; if it is empty (`= 0`) print `1` and
`miner 1 CONTROL-ENABLED`, else print `0` and `miner 0 CONTROL-ENABLED`.
Remember the literal-flag rule: branch, and call `CONTROL-ENABLED` with a
literal in each arm. (The sidecar seeds the vault empty, so the checker
expects `1`.)

```
\ @exercise sim-101/06-act
```

```bash
mforth check restock.fs
```

```
✓ sim-101/06-act — 1/1 cases pass
```

---

You have now built a controller end to end: read the world, decide, act.
Next, [chapter 13](./13-control-loop.md) makes it tick — adding `WAIT`
and leaning on the processor's auto-loop so `restock` runs every tick the
way it would on a real logic block. After that,
[chapter 14](./14-capstone.md) is a milestone-checked capstone, and
[chapter 15](./15-where-next.md) hands you off to compiling for the game.
