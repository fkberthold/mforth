# 14 — Capstone: the sorter

> **You will:** build a complete, real Mindustry controller — a sorter
> that drains whichever of two piles is larger, and stops when both are
> empty — in **three checked milestones**. Each milestone is a working
> word you can run through `mforth check`; together they assemble into a
> controller you could paste into the game.

Everything in this tutorial has been building to a program you could
actually run a factory with. This is it. It is a port of a real script
from the Mindustry community wiki — the *Sorter Picker* — and you will
build it the way a Forther builds anything: one small, checkable word at
a time, from the inside out.

The job: an unloader pulls items out of a vault. The vault holds two
items — surge alloy and blast compound — and we want to **balance the
pile** by always draining whichever side is larger. If *both* sides are
empty there is nothing to drain, so we stop the unloader.

That is three cases — three **arms**:

- surge is bigger → drain surge,
- blast is bigger → drain blast,
- both empty → stop.

We will grow into all three, one milestone per arm of complexity.

## Milestone 1 — pick the bigger pile

Start with the core decision and nothing else: given two pile levels,
which is larger? The two levels arrive on the stack; print the verdict.

```forth
: bigger-pile ( left right -- )
  > IF S" LEFT" PRINT ELSE S" RIGHT" PRINT THEN
;
900 100 bigger-pile
```

`>` is `( left right -- flag )` — `left` was underneath, `right` on top,
both consumed, a `1`/`0` flag left behind. A tie (`500 500`) is *not*
strictly greater, so it falls to the `ELSE` arm. This is the same
two-value comparison you have used since [chapter 04](04-arithmetic.md);
nothing new, just named.

> **Exercise 1 — `sim-102/03-bigger-pile`.** Define `bigger-pile`
> `( left right -- )`: print `LEFT` when `left` is strictly greater than
> `right`, otherwise `RIGHT`.

```bash
mforth check bigger-pile.fs
```

```
✓ sim-102/03-bigger-pile — 3/3 cases pass
```

## Milestone 2 — add the "both empty" arm

Now the third arm. Before we pick the bigger pile, we have to ask: *are
both piles empty?* If so, the answer is neither — it is "stop".

The wrinkle: the "both empty?" test and the "which is bigger?" test both
want the same two numbers, but each comparison **consumes** its inputs.
The fix is to make a copy of the pair up front. `OVER OVER` does exactly
that — recall from [chapter 02](02-juggling.md) that `OVER` copies the
second item to the top, so `OVER OVER` duplicates the whole pair:

```
surge blast            OVER OVER
surge blast surge blast
```

Now one copy can be spent testing "both empty?" while the original pair
waits underneath for the bigger-of-two test.

```forth
: pick-drain ( surge blast -- )
  OVER OVER + 0 = IF        \ both empty?  (sum is zero)
    DROP DROP               \   yes: spend the leftover copy
    S" STOP" PRINT
  ELSE
    > IF S" SURGE" PRINT     \   no: drain the bigger pile
    ELSE S" BLAST" PRINT
    THEN
  THEN
;
900 100 pick-drain
```

Walk the `IF` boundaries (your [chapter 09](09-factoring.md)
stack-checker walks them too):

- `OVER OVER + 0 =` leaves the original `surge blast` pair *plus* a flag
  on top. `IF` consumes the flag.
- The **STOP arm** still has the leftover `surge blast` copy from before
  the `+`, so it `DROP DROP`s them — leaving the stack empty.
- The **ELSE arm** keeps the original pair and lets bare `>` consume them
  into a flag for the inner `IF`. Each inner arm prints and leaves the
  stack empty.

Both outer arms end at depth zero. That is the rule from
[chapter 05](05-branching.md): every arm of a branch must agree on the
final stack depth. Get it wrong — forget a `DROP` — and mforth refuses
to compile, naming the mismatch.

> **Exercise 2 — `sim-102/04-pick-drain`.** Define `pick-drain`
> `( surge blast -- )`: if **both** levels are zero print `STOP`;
> otherwise print `SURGE` when surge is the larger pile, else `BLAST`.

```bash
mforth check pick-drain.fs
```

```
✓ sim-102/04-pick-drain — 4/4 cases pass
```

## Milestone 3 — make it act

The decision is done. The last milestone makes each arm *do* something:
configure the unloader to drain the chosen item — or to `NULL`, mlog's
"select no resource", which is how the stop arm halts the drain. We keep
the `PRINT` in each arm so we can watch what the controller chose.

```forth
: sorter-step ( surge blast -- )
  OVER OVER + 0 = IF
    DROP DROP
    unloader NULL CONTROL-CONFIG        \ stop draining
    S" STOP" PRINT
  ELSE
    > IF
      unloader @surge-alloy CONTROL-CONFIG
      S" SURGE" PRINT
    ELSE
      unloader @blast-compound CONTROL-CONFIG
      S" BLAST" PRINT
    THEN
  THEN
;
900 100 sorter-step
```

`@surge-alloy` and `@blast-compound` are item handles — first-class
values you push like any number (from [chapter 11](11-sensing.md)).
`unloader <item> CONTROL-CONFIG` tells the unloader which item to pull.
`NULL` is the source-level literal for mlog's `null`; configuring to it
clears the selection. This exercise ships a `sidecar` naming the
`unloader` block, so it resolves when you run the check.

> **Exercise 3 — `sim-102/05-sorter-step`.** Define `sorter-step`
> `( surge blast -- )`: same three arms as `pick-drain`, but each arm
> also configures the `unloader` — `@surge-alloy` / `@blast-compound`
> to drain that pile, or `NULL` to stop — and still prints
> `SURGE` / `BLAST` / `STOP`.

```bash
mforth check sorter-step.fs
```

```
✓ sim-102/05-sorter-step — 4/4 cases pass
```

## Closing the loop

You now have `sorter-step`: a word that takes two readings and acts. The
last move is to stop *handing it* the readings and instead **sense** them
from the world each tick — exactly the sense → decide → act → wait shape
from [chapter 13](13-control-loop.md):

```forth
\ The sorter as a live controller.
: sorter-step ( surge blast -- )
  OVER OVER + 0 = IF
    DROP DROP
    unloader NULL CONTROL-CONFIG
  ELSE
    > IF   unloader @surge-alloy CONTROL-CONFIG
    ELSE   unloader @blast-compound CONTROL-CONFIG
    THEN
  THEN
;

vault1 @surge-alloy SENSOR        \ sense pile 1
vault1 @blast-compound SENSOR     \ sense pile 2
sorter-step                       \ decide + act
1 WAIT                            \ pace the loop
```

with a sidecar naming `vault1` and `unloader`:

```toml
[links.vault1]
type   = "generic"
target = "vault1"

[links.unloader]
type   = "generic"
target = "unloader1"

[clock]
ipt      = 8
realtime = false
```

That is the whole controller. The factored word `sorter-step` carries
the logic you milestone-checked; the four lines below it are the loop the
processor re-runs forever. Run a single tick:

```bash
mforth run --no-loop sorter.fs
```

It runs silently against the simulator (the mock vault starts empty, so
this first tick takes the STOP arm). In the game, with a real vault
filling up, it would balance the two piles tick after tick.

You built a real Mindustry controller — and every piece of it passed a
check before it joined the whole. That is the payoff of factoring into
small words: each one is testable in isolation, and the assembly is just
naming them in order.

## Where you are

You have the full v1 mforth toolkit now: the stack, words, arithmetic,
branches, loops, state, output, the simulator, sensing, controlling, and
the control loop that ties them together. The
[final chapter](15-where-next.md) shows you how to take a controller
like this one out of the simulator and into the game — compiling it to
paste-ready mlog — and points you at where to go next.
