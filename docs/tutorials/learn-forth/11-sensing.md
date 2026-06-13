# 11. Reading the world

> **You will:** use `SENSOR` to pull a block's properties onto the
> stack, learn the `@`-property names for the things you can read, and
> turn a reading into a decision your earlier `IF`s can branch on. About
> ten minutes.
>
> **You will need:** [chapter 10](./10-simulator.md) (the sidecar and
> message blocks) and your comfort with comparisons (`< > =`) from
> [chapter 4](./04-arithmetic.md).

A message block is output. To make a *controller* you also need input —
the processor has to know how full the vault is, how hurt the turret is,
how charged the battery is. That reading-in is what `SENSOR` does.

## `SENSOR` ( block prop -- value )

`SENSOR` takes two things off the stack — a **block** and a
**property** — and pushes back the value of that property on that block:

```forth
vault1 @copper SENSOR
```

Left to right: `vault1` pushes the block handle (a name your sidecar
binds), `@copper` pushes the property to read, and `SENSOR` consumes both
and pushes the copper level. The result is an ordinary number on the
stack — you do arithmetic and comparisons on it exactly like any other
value.

The property is one of the many `@`-prefixed built-in names. Two kinds
show up here:

- **Content names** — `@copper`, `@graphite`, `@water`, … — ask "how much
  of *this item/liquid* does the block hold?"
- **Stat names** — `@totalItems`, `@itemCapacity`, `@health`,
  `@progress`, `@efficiency`, … — ask about the block itself.

The [dictionary reference](../../reference/dictionary.md) lists every
sensor property; you don't memorize them, you look them up.

## What a block reads — and how you set it

A block you have just wired up, holding nothing, senses **0** for every
stocked property: an empty vault has `@totalItems` of 0, a copper level
of 0, a brand-new drill `@progress` of 0. That zero is the honest
*default* — the baseline a real controller must handle, since "the vault
is empty" is precisely when you want the miner running.

But a controller you are testing rarely wants the empty case. To check
that "over half full" really fires, you need a vault that *is* over half
full. So the sidecar lets you **seed** a block's readings: add a `sensors`
table to the link and `SENSOR` returns the value you set.

```toml
[links.vault1]
type    = "core"
target  = "vault1"
sensors = { "@copper" = 240, "@totalItems" = 80, "@itemCapacity" = 100 }
```

Now `vault1 @copper SENSOR` reads `240`, `@totalItems` reads `80`, and a
property you didn't seed still reads its `0` default. The keys are the
same `@`-property names you pass to `SENSOR` (see the
[dictionary reference](../../reference/dictionary.md) for the readable
list); a typo'd or non-sensor `@`-name is a clear sidecar error. The
seeded value is what *both* the host REPL and the compiled mlog read, so
your tested controller behaves the same when you paste it into the game.

The right thing to do with a reading is rarely to print it raw — it's to
*decide something about it*:

```forth
\ Is the vault out of copper?  (240 seeded above -> not empty -> 0)
vault1 @copper SENSOR 0 =
```

`SENSOR` pushes the copper level (`240` from the seed above); `0 =` turns
it into a flag — `1` when the level equals zero, `0` otherwise. That
`1`/`0` is exactly what `IF` consumes (you met this encoding in
[chapter 5](./05-branching.md)). A reading became a decision.

## Two readings, one comparison

Most real decisions weigh two readings against each other. "Is the vault
more than half full?" compares the item count to the capacity:

```forth
\ Over half full?  items*2 > capacity
vault1 @totalItems SENSOR 2 *
vault1 @itemCapacity SENSOR
>
```

Sense the count and double it; sense the capacity; compare with `>`. No
fractions needed — `items*2 > capacity` says "more than half" using only
whole numbers, which suits a language whose `/` is float division and
whose comparisons are cheapest on integers. With the vault seeded to `80`
items in a `100`-capacity vault, `160 > 100` is true and the answer is
`1`. Re-seed it to `30` items and the same word answers `0` — the
decision tracks the data the sidecar sets, which is exactly how you test
a controller against the case you care about.

Notice the shape. Each `SENSOR` reads one value onto the stack; the
arithmetic and comparison words consume them in order. You are not
inventing named scratch variables for `count` and `capacity` the way
hand-written mlog must — the stack *is* the scratch space, and you met
that idea back in [chapter 1](./01-stack.md). It pays off most exactly
here, where every reading is used once, on the next line.

## Capturing a reading you need twice

When you need a reading more than once, give it a name with a `VARIABLE`
(from [chapter 7](./07-state.md)) instead of juggling copies on the
stack:

```forth
VARIABLE items
vault1 @totalItems SENSOR items !   \ capture once
items @ 10 <                        \ low?  ( -- flag )
```

`SENSOR … !` stores the reading; `items @` reads it back as often as you
like. For a value used once, skip the variable and leave it on the stack;
for one used twice or more, the name reads better than a stack of `DUP`s.

## Exercises

Same flow as chapter 10: write a `.fs` starting with the
`\ @exercise <id>` marker, run `mforth check <file>`, look for the `✓`.
`--scaffold <id>` gives you a stub; `--solution <id>` reveals the answer.
Both exercises bundle a sidecar that binds `vault1` to a core block **and
seeds its readings** (a stocked copper level for 11.1, an `80`-of-`100`
vault for 11.2), so your word senses real data — no `.world.toml` to
write.

### Exercise 11.1 — `copper-empty?` ( -- flag )

`SENSOR` the `@copper` level of `vault1` and leave `1` if it is empty
(level = 0), else `0`. (The vault is seeded with copper, so it is *not*
empty — the checker expects `0`.)

```
\ @exercise sim-101/03-sense-empty
```

```bash
mforth check copper-empty.fs
```

```
✓ sim-101/03-sense-empty — 1/1 cases pass
```

### Exercise 11.2 — `over-half?` ( -- flag )

`SENSOR` `vault1`'s `@totalItems` and `@itemCapacity`; leave `1` if
`items * 2 > capacity`, else `0`. (The checker seeds an `80`-of-`100`
vault — over half full — so the answer is `1`.)

```
\ @exercise sim-101/04-half-full
```

```bash
mforth check over-half.fs
```

```
✓ sim-101/04-half-full — 1/1 cases pass
```

---

You can read the world and turn readings into flags. The last piece of a
controller is doing something with that flag — flipping a switch, aiming
a turret, configuring a sorter. Next:
[chapter 12, Acting on the world](./12-controlling.md), where
`CONTROL-ENABLED` turns a decision into an action. Then
[chapter 13](./13-control-loop.md) wires sense → decide → act into one
loop.
