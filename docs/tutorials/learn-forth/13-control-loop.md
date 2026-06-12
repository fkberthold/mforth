# 13 — A real control loop

> **You will:** put the three skills from Part II together —
> [sensing](11-sensing.md), [deciding](05-branching.md), and
> [acting](12-controlling.md) — into the shape every Mindustry
> controller has: **sense → decide → act → wait**, run over and over
> by the processor's auto-loop. You will write a controller word that
> announces its decision on every tick.

In [chapter 11](11-sensing.md) you read the world with `SENSOR`. In
[chapter 12](12-controlling.md) you acted on it with `CONTROL-ENABLED`.
This chapter is the glue: the **loop** that does both, forever.

## The shape of every controller

A logic processor does not run your program once and stop. It runs it
top to bottom, falls off the end, and starts again at the top — mlog's
**auto-loop**. You saw this back in [chapter 10](10-simulator.md): the
counter kept counting because the processor kept re-running it.

That changes how you write a program. You are not writing a script that
finishes. You are writing **one tick of a loop** that will run a few
times a second for as long as the processor is powered. Almost every
useful controller is the same four steps in that loop body:

1. **Sense** — read the current state of the world (`SENSOR`).
2. **Decide** — branch on what you read (`IF / ELSE / THEN`).
3. **Act** — change a block (`CONTROL-ENABLED`, `CONTROL-CONFIG`, …).
4. **Wait** — `WAIT` a beat so you are not hammering the block every
   single tick.

Then the processor loops and you do it again with fresh readings.

## The decide step, on its own

Before wiring up sensors and switches, look at just the **decide** step.
Here is a word that takes one reading — a temperature — and prints what
it would do about it:

```forth
: thermostat ( temp -- )
  30 > IF S" COOL" PRINT ELSE S" HOLD" PRINT THEN
;
35 thermostat
```

`30 >` consumes the temperature and the `30`, leaving a `1`/`0` flag
(from [chapter 04](04-arithmetic.md)). `IF` consumes that flag and picks
an arm (from [chapter 05](05-branching.md)). `S" ..." PRINT` prints a
string (from [chapter 08](08-output.md)). Nothing new — you are just
assembling pieces you already have. Running it with `35` on top prints
`COOL`; with anything `30` or below it prints `HOLD`.

That is the heart of a controller. The reading comes in, a decision goes
out. In a real processor the reading would come from a `SENSOR` instead
of being handed in — but the decision logic is identical, which is
exactly why the [`mforth check`](09-factoring.md) exercises can hand the
value in directly and still test the real thing.

## The whole loop

Now the full four steps. This controller keeps a tank topped up: when the
level drops below 20 it turns a pump on; otherwise it holds. Each tick it
flushes a status line to a message block and waits a second.

```forth
\ A tank-fill controller: sense → decide → act → wait.
VARIABLE level

: control-loop ( -- )
  level @                       \ 1. SENSE — read the current level
  20 < IF                       \ 2. DECIDE — is it low?
    pump 1 CONTROL-ENABLED      \ 3. ACT — pump on
    S" FILLING" PRINT
  ELSE
    pump 0 CONTROL-ENABLED      \ 3. ACT — pump off
    S" HOLDING" PRINT
  THEN
  display PRINTFLUSH            \ show the status line
  1 WAIT                        \ 4. WAIT — one second
;

control-loop
```

It needs a sidecar to name the two blocks (`pump` and `display`) — the
same `.world.toml` idea from [chapter 10](10-simulator.md):

```toml
[links.pump]
type   = "switch"
target = "switch1"
enabled = false

[links.display]
type   = "message"
target = "message1"

[clock]
ipt      = 8
realtime = false
```

Run one tick of it:

```bash
mforth run --no-loop control-loop.fs
```

Silent, as usual — the simulator read `level` (which starts at 0, so it
is "low"), turned the pump on, queued `FILLING`, flushed it to the
message block, and waited. Drop `--no-loop` and it would keep going:
sense, decide, act, wait, sense, decide, act, wait… That is a live
controller.

The bare `control-loop` on the last line is what kicks the first tick
off. The processor's auto-loop does the rest — it re-enters at the top
every time it falls off the end.

## Why the `WAIT` matters

Without `1 WAIT`, the loop runs as fast as the processor can go — dozens
of times a second. For a pump that is usually harmless, but for anything
that toggles a block on and off near a threshold it makes the block
*chatter*. The `WAIT` paces the loop to human speed and gives the world
time to actually change between readings. (When you need a controller
that *cannot* chatter even without a wait, you reach for hysteresis —
that is a [chapter 07](07-state.md) `VARIABLE` trick, and you will see it
again in the [next chapter's](14-capstone.md) wrap-up.)

## Exercises

Write each in a `.fs` file and run `mforth check <file>`. Stuck? Get a
starter with `mforth check --scaffold <id>`, or reveal the answer with
`mforth check --solution <id>`.

### Exercise 1 — `sim-102/01-thermostat`

The decide step, standing alone.

> Define `thermostat` `( temp -- )`: print `COOL` when `temp` is above
> 30, otherwise `HOLD`.

This is the worked example above. Type it yourself, then:

```bash
mforth check thermostat.fs
```

```
✓ sim-102/01-thermostat — 3/3 cases pass
```

### Exercise 2 — `sim-102/02-pump-controller`

Now the whole loop body in one word — decide, **act on a block**, and
announce it.

> Define `pump-controller` `( level -- )`: when `level` is below 20,
> turn the `pump` switch **on** and print `FILLING`; otherwise turn it
> **off** and print `FULL`.

This exercise ships a `sidecar`, so the `pump` block resolves when you
run the check — you do not have to write a `.world.toml` yourself. Both
arms of your `IF` must leave the stack empty, or the stack-checker (your
ever-present safety net from [chapter 09](09-factoring.md)) will stop
you before a single case runs.

```bash
mforth check pump-controller.fs
```

```
✓ sim-102/02-pump-controller — 3/3 cases pass
```

---

You can now write a controller: the sense → decide → act → wait loop
that every Mindustry automation is built from. The
[next chapter](14-capstone.md) is the **capstone** — you will build a
real one, the drain-the-bigger-pile sorter, in three checked milestones.
Then [chapter 15](15-where-next.md) hands you off to compiling these
controllers to mlog you can paste into the game.
