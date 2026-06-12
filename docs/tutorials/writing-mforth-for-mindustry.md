# Writing mforth for Mindustry

> **You will:** ladder up through six small programs, each verified
> end-to-end against the host REPL simulator and compiled to mlog
> you can paste into a Mindustry logic processor. By the end, you
> will have ported one of the hand-written mlog scripts from the
> Mindustry community wiki into mforth and compared the two
> side-by-side. About one hour.
>
> **You will need:** mforth installed (see
> [Getting started](./getting-started.md)), a terminal, and
> Mindustry on hand if you want to paste the compiled output into
> the game. You do *not* need prior Forth experience — Forth ideas
> are introduced as they appear.

This tutorial assumes you know Mindustry (you have a processor in
mind to paste into) but not Forth. Each part is a single `.fs`
program plus its `.world.toml` sidecar. Every snippet has been
compiled and run as part of writing this tutorial; if a snippet
errors for you, that is a bug — please file one.

The seven parts (Part 0 is a 5-minute Forth primer; the rest build the programs):

| Part | What you build | Time |
|------|---------------|------|
| [0](#part-0-forth-at-a-glance) | The stack mental model, in 5 minutes | ~5 min |
| [1](#part-1-hello-message-block) | `hello, mforth` to a message block | ~5 min |
| [2](#part-2-a-counter-you-can-paste-in-game) | A counter that ticks on a message block | ~10 min |
| [3](#part-3-just-charge-hysteresis-on-a-power-node) | Port the wiki *Just Charge* — keep a battery charged with hysteresis | ~10 min |
| [4](#part-4-the-sorter-picker-rewrite) | Port a wiki Sorter Picker — side-by-side | ~15 min |
| [5](#part-5-all-in-as-a-definition) | Port "All In" using a Forth definition | ~10 min |
| [6](#part-6-whats-next) | Honest appendix: what is v2-only | ~5 min |

Do them in order. Each part introduces the smallest new idea on top
of the previous one.

A note on the running shape. Each part is one `.fs` file plus one
`.world.toml` sidecar (same basename, same directory). To run a
single pass through the simulator: `mforth run --no-loop FILE.fs`.
To compile to paste-ready mlog: `mforth compile FILE.fs -o FILE.mlog`.
Drop `--no-loop` and the simulator will keep looping — same as a
real logic processor in-game.

---

## Part 0 — Forth at a glance

You can skip this part if you already know Forth. Otherwise, five
minutes here will save you from re-reading Part 4 twice.

Forth is a stack language. Every value lives on a single data stack;
every word (Forth's name for *function*) consumes from the top and
pushes results back. There is no `x = f(y, z)`. The values arrive
first, the operator picks them up.

### The stack mental model

Read left-to-right. Each token either pushes a value or runs a word
that consumes values and pushes new ones.

```forth
5 3 +
```

| token | stack after | what happened |
|-------|-------------|---------------|
| `5`   | `5`         | pushed `5` |
| `3`   | `5 3`       | pushed `3` (now on top) |
| `+`   | `8`         | `+` popped `3` and `5`, pushed `5 + 3` |

That is the entire trick. `5 3 +` and `3 5 +` produce the same `8`
because `+` is commutative. `5 3 -` produces `2` — `5` was *under*
`3`, so `+` and `-` both compute `(under) op (top)`.

### Stack-effect notation

Every Forth word has a fixed stack effect, written as a comment:

```forth
+      ( a b -- c )       \ consumes 2, produces 1
DUP    ( a -- a a )       \ consumes 1, produces 2 (a copy)
PRINT  ( str -- )         \ consumes 1, produces nothing
```

The hyphen-hyphen separates *consumes* (left) from *produces*
(right). The right side of the consumes list is what was *on top*
of the stack. So `( a b -- c )` means "`b` was on top, `a` was
underneath; both are gone, `c` is now on top".

When you run `mforth lsp` and hover over a word in your editor,
this is the notation the LSP shows you. The stack-checker uses
the same notation internally; every word your `.fs` source uses
has its effect declared, and the compiler refuses to emit code if
the depths don't add up at a branch boundary.

### The five stack-juggling words you will actually use

You will see all five in this tutorial. Each is a one-liner.

| word    | effect              | when you'd reach for it |
|---------|---------------------|--------------------------|
| `DUP`   | `( a -- a a )`      | you need to use the same value twice |
| `DROP`  | `( a -- )`          | the top value isn't needed anymore |
| `SWAP`  | `( a b -- b a )`    | the two top values arrived in the wrong order |
| `OVER`  | `( a b -- a b a )`  | peek at the underneath value without losing the top |
| `ROT`   | `( a b c -- b c a )`| bring the third-deepest value up to the top |

Two more, `NIP ( a b -- b )` and `TUCK ( a b -- b a b )`, exist as
shorthand combinations (`SWAP DROP` and `SWAP OVER`). They won't
appear in this tutorial.

### Why postfix

Two practical wins, the same ones that draw people to RPN
calculators:

- **No operator precedence, no parens.** `(5 + 3) * 2` is
  `5 3 + 2 *`. `5 + 3 * 2` is `5 3 2 * +`. The token order
  *is* the evaluation order.
- **The stack is the intermediate state.** No named temporaries.
  When you want to use the same value twice you say `DUP`; you
  don't invent a variable name.

Mlog's `op add result a b` form is doing the same work with named
result syntax. Forth lets the result stay on the stack until the
next consumer claims it. For block-control programs — where most
intermediate values are used once, on the next line — this elides
about half the variable names you'd otherwise need to invent.

For the deeper mental model — composition, factoring, why postfix
shapes programs differently from infix — see
[Forth, the mental model](../explanation/forth-mental-model.md)
(coming soon as part of the explanation quadrant).

---

## Part 1 — Hello, message block

The smallest end-to-end mforth program: queue a string into the
print buffer, then flush it onto a message block.

**`part1.fs`:**

```forth
\ Part 1 — Hello, message block.
S" hello, message block" PRINT
display PRINTFLUSH
```

**`part1.world.toml`:**

```toml
[links.display]
type   = "message"
target = "message1"

[clock]
ipt      = 8
realtime = false
```

Three new ideas in three lines:

- `S" ..."` is Forth's string literal syntax. The string is pushed
  onto the data stack.
- `PRINT` queues the top of the stack into the processor's print
  buffer (it does not flush yet).
- `display PRINTFLUSH` flushes the buffer onto the block named
  `display`. The sidecar binds the mforth-name `display` to the
  in-game block labelled `message1`. (For why mforth uses a
  sidecar instead of inline block names, see the [explanation
  index](../explanation/index.md).)

**Run it:**

```bash
mforth run --no-loop part1.fs
```

No stdout — the simulator wrote to its simulated message block
and stopped. Compile it instead and you can read what would happen
in-game:

```bash
mforth compile part1.fs -o part1.mlog
cat part1.mlog
```

You get exactly two mlog instructions:

```
print "hello, message block"
printflush message1
```

That is paste-ready. Open a logic processor in Mindustry, link
`message1` to it, paste, and the block will read
`hello, message block`. The auto-loop will re-flush the same
string forever — harmless, but in Part 2 we will make it move.

---

## Part 2 — A counter you can paste in-game

The first program that *does* something visible on the auto-loop.
A variable that increments on each tick; the new value is printed
to the same message block.

**`part2.fs`:**

```forth
\ Part 2 — A counter you can paste in-game.
VARIABLE n

: tick ( -- )
  n @ 1 + n !
  S" count=" PRINT
  n @ PRINT
  display PRINTFLUSH
  1 WAIT
;

tick
```

**`part2.world.toml`** — same as `part1.world.toml`. Copy it.

The new ideas:

- `VARIABLE n` declares a variable named `n`. In v1 mforth, this
  compiles to a bare mlog variable — not a memory cell — so it is
  cheap.
- `n @` (read "n fetch") pushes `n`'s current value onto the stack.
- `n !` (read "n store") pops the top of the stack and writes it
  into `n`.
- `: tick ( -- ) ... ;` defines a new Forth word called `tick`.
  The `( -- )` is the stack effect comment: `tick` consumes nothing
  and produces nothing. (mforth statically checks every word's
  stack effect — see [explanation](../explanation/index.md).)
- `1 WAIT` pauses the processor for one second.
- The bare word `tick` at the bottom *calls* `tick` once.

**Run it:**

```bash
mforth run --no-loop part2.fs
```

Silent again — the simulator advanced `n` from 0 to 1, formatted
`count=1`, flushed to `display`, then "waited" one simulated
second. Drop `--no-loop` and the simulator would keep ticking.

**Compile it:**

```bash
mforth compile part2.fs -o part2.mlog
cat part2.mlog
```

The output:

```
set s0 n
set s1 1
op add s0 s0 s1
set n s0
print "count="
set s0 n
print s0
printflush message1
set s0 1
wait s0
```

The variable name `n` from your `.fs` source flows through to the
mlog as a bare mlog variable — that is what "v1 mforth is cell-free"
means in practice: no memory cells, just plain processor variables.
The `s0` and `s1` are the static stack slots the compiler assigned;
your program never references them by name.

When the processor runs off the end it loops back to instruction 0
— mlog's auto-loop is what keeps `tick` ticking.

Paste this into a logic processor and watch the message block
count up: `count=1`, `count=2`, `count=3`, …

---

## Part 3 — *Just Charge*: hysteresis on a power node

The first program that ports a real wiki script — and the first
that closes a feedback loop. We sense a power network's stored
and total capacity, then enable a generator when storage drops
below 95% of capacity and *keep it on* until storage is full
again. That last clause — "keep it on until full" — is
**hysteresis**, and it needs state.

[The wiki source][wiki] for *Just Charge*:

[wiki]: https://github.com/Anuken/Mindustry/wiki

```
sensor max node1 @powerNetCapacity
sensor power node1 @powerNetStored
op lessThan notFull power max
op mul min max 0.95
op lessThan notMin power min
op notEqual notCharging true charging
op or charging charging notMin
op land charging charging notFull
control enabled generator1 charging 0 0 0
```

Nine lines that mean: *toggle the generator on when the battery
drops below 95%, then leave it on until the battery is full.* The
trick is the variable `charging` — it's read on the right side of
the `or`/`land` and re-written on the left, so its previous value
*from the prior tick* feeds the current decision. (The
`notCharging` line is dead — the wiki script computes it and never
reads it.)

That is hysteresis: the controller's output remembers its last
state, so it doesn't flutter on/off near the threshold.

**`part3.fs`:**

```forth
\ Part 3 — Just Charge port: keep a power network charged to ~95%
\ with hysteresis. The VARIABLE `charging` carries last tick's
\ decision into this tick — that's what makes the toggle sticky.
VARIABLE charging
VARIABLE max
VARIABLE power

node1 @powerNetCapacity SENSOR max !
node1 @powerNetStored   SENSOR power !

\ notMin = power < max * 0.95. v1 mforth has no float literals,
\ so use integer math: power*100 < max*95 says the same thing.
power @ 100 *  max @ 95 *  <        \ ( -- notMin )
charging @ OR                       \ ( -- charging|notMin )

\ notFull = power < max
power @  max @  <                   \ ( -- (charging|notMin) notFull )
AND                                 \ ( -- charging' )

DUP charging !                      \ save for next tick, keep flag on stack
IF generator1 1 CONTROL-ENABLED
ELSE generator1 0 CONTROL-ENABLED
THEN
```

**`part3.world.toml`:**

```toml
[links.node1]
type   = "generic"
target = "node1"

[links.generator1]
type   = "switch"
target = "switch1"
enabled = false

[clock]
ipt      = 8
realtime = false
```

The new ideas:

- **`SENSOR ... !` pattern.** `node1 @powerNetCapacity SENSOR`
  pushes the sensor reading; `max !` stores it into the
  VARIABLE `max` from Part 2. Capturing readings into VARIABLEs
  lets you reference them twice without juggling the stack.
- **`OR` and `AND`** are mforth's boolean combinators. They map
  to mlog's `op or` and `op land` and consume two flags
  (each `0` or `1`), pushing the combined flag.
- **VARIABLE-for-state.** The `charging` VARIABLE persists across
  ticks (the processor auto-loops, re-running the program top to
  bottom forever). Read it with `charging @`, fold it into the
  new decision, write the updated value with `charging !`. The
  same pattern your `n` counter used in Part 2, applied to a
  boolean.
- **`DUP charging !`** is the idiom for "save this value and keep
  using it". `!` consumes the top of the stack, so without the
  `DUP` you'd have to read `charging @` again on the next line.

**Run it:**

```bash
mforth run --no-loop part3.fs
```

Silent. The mock world starts with `power = 0` and `max = 0`,
which trivially exits the loop without firing the generator —
exactly what you want before the network exists.

**Compile it:**

```bash
mforth compile part3.fs -o part3.mlog
cat part3.mlog
```

23 instructions:

```
sensor s0 node1 @powerNetCapacity
set max s0
sensor s0 node1 @powerNetStored
set power s0
set s0 power
set s1 100
op mul s0 s0 s1
set s1 max
set s2 95
op mul s1 s1 s2
op lessThan s0 s0 s1
set s1 charging
op or s0 s0 s1
set s1 power
set s2 max
op lessThan s1 s1 s2
op land s0 s0 s1
set s1 s0
set charging s1
jump 22 equal s0 0
control enabled generator1 1 0 0 0
jump 23 always 0 0
control enabled generator1 0 0 0 0
```

| Metric | Wiki *Just Charge* | mforth source | mforth compiled |
|--------|---------------------|---------------|-----------------|
| Lines (non-blank, non-comment) | 9 | 11 | 23 |
| Named scratch variables | 5 (incl. 1 dead) | 3 (named state) | 3 (carried through) |
| Branches | 0 | 1 `IF/ELSE` | 2 jumps |

Be honest about the trade. The compiled mlog is **larger than the
wiki original** — by more than 2×. Three reasons, all tracked:

1. **`CONTROL-ENABLED` requires a literal flag in v1.** The wiki
   script ends with `control enabled generator1 charging 0 0 0`,
   passing the computed flag straight through. v1 mforth's
   `CONTROL-ENABLED` lifter needs the flag to be a literal `0` or
   `1`, which forces an `IF/ELSE` and two `control` instructions.
   Tracked as `mforth-vdt`.
2. **No float literals in v1.** The wiki uses `0.95`; we use
   `* 100` and `* 95` to keep the same math with integers. Two
   extra `set`/`op mul` pairs.
3. **VARIABLE-routed values reload through stack slots.** Each
   `max @` / `power @` does a `set s? max` before it can be used.
   The optimizer will fold these (see `mforth-10t` v2 roadmap),
   but v1 emits them straight.

What you gained anyway:

- **Hysteresis is legible.** `charging @ OR ... AND` reads as
  "*if we were charging, stay charging — unless we're full.*"
  The wiki version says the same thing across three `op` lines
  with a dead variable in the middle.
- **The dead variable is gone.** mforth's stack-checker would
  refuse to compile a word that produces an unused value at the
  top of an expression, so the equivalent of the wiki's
  `notCharging` line can't survive a re-port.
- **The compiler catches stack mistakes.** Drop one of the `@`s
  and mforth refuses at compile time with a clear diagnostic.
  In the wiki mlog, the same typo silently reads a stale value
  from a previous tick.

A quick aside on what mforth caught for us *while writing this
section*. The first draft tried `generator1 SWAP CONTROL-ENABLED`
to push the computed flag straight to the lifter. mforth refused
with:

```
VARIABLE address 'generator1' is being consumed by 'SWAP' rather
than by @ or ! — v1 mforth is cell-free and does not support
manipulating variable addresses as stack values
```

That diagnostic is what makes mforth a *teaching* compile target,
not just an mlog frontend. In raw mlog the same mistake would
have silently produced wrong behaviour at runtime.

---

## Part 4 — The Sorter Picker rewrite

Part 3 already ported a wiki script. This part ports a longer one
— the *Sorter Picker* — and puts the original mlog and the mforth
source side-by-side so you can see the leverage. Part 3 won on
hysteresis legibility but lost on instruction count; here, mforth
wins on both fronts.

The job: an unloader on `unloader1` pulls items out of `vault1`.
We pick what to drain based on which item vault1 holds more of —
balance the pile by reducing whichever side is larger. Items
considered: `@surge-alloy` and `@blast-compound`. There's a third
arm: when *both* piles are empty there is nothing to drain, so we
stop the unloader by configuring it to `null` (the mlog idiom for
"no resource selected").

**The original mlog (from the wiki):**

```
sensor surgeAvail vault2 @surge-alloy
sensor blastAvail vault2 @blast-compound
sensor hasSurge vault1 @surge-alloy
sensor hasBlast vault1 @blast-compound
sensor unloading unloader1 @config
op sub diffHas hasSurge hasBlast
op abs diffHas diffHas hasBlast
op max hasMost hasSurge hasBlast
op min availLeast surgeAvail blastAvail
op lessThan sameIsh diffHas 100
op lessThan emptyIsh hasMost 900
op equal justOne availLeast 0
op or needAll sameIsh emptyIsh
op or justFill justOne needAll
jump 20 equal justFill true
jump 18 greaterThan hasBlast hasSurge
set unload @blast-compound
jump 21 always blastAvail blastAvail
set unload @surge-alloy
jump 21 always blastAvail blastAvail
set unload null
jump 23 equal unload unloading
control config unloader1 unload 0 0 0
end
```

24 instructions, eight named scratch variables, four nested
branches. Underneath the scratch-variable thicket the logic is
three-armed: pick the bigger item, or — when both piles are empty
— null out the unloader so it stops. mforth ports all three arms.

**The mforth equivalent (`part4.fs`):**

```forth
\ Part 4 — Sorter Picker port (full three-arm version).
\ Drain whichever item vault1 holds more of; if BOTH are gone,
\ stop the unloader by configuring it to null.
vault1 @surge-alloy SENSOR       \ ( -- hasSurge )
vault1 @blast-compound SENSOR    \ ( hasSurge -- hasSurge hasBlast )
OVER OVER                        \ ( hasSurge hasBlast -- hasSurge hasBlast hasSurge hasBlast )
+ 0 = IF
  \ Both piles empty — stop unloading.
  DROP DROP
  unloader1 NULL CONTROL-CONFIG
ELSE
  > IF
    unloader1 @blast-compound CONTROL-CONFIG
  ELSE
    unloader1 @surge-alloy CONTROL-CONFIG
  THEN
THEN
```

**`part4.world.toml`:**

```toml
[links.vault1]
type   = "generic"
target = "vault1"

[links.unloader1]
type   = "generic"
target = "unloader1"

[clock]
ipt      = 8
realtime = false
```

The two readings push their values onto the stack in order.
`OVER OVER` duplicates the pair so the outer test can consume one
copy while the inner test still has the originals. `+ 0 =` sums
the two levels and asks "is the total zero?" — i.e. are both piles
empty. The outer `IF` takes the stop arm: it `DROP DROP`s the
leftover copies and pushes `NULL` as the unloader's config. The
`ELSE` keeps the original pair and the bare `>` consumes them,
leaving a comparison result for the inner `IF` to pick the bigger
item. `NULL` is the source-level literal for mlog's `null`; it
landed in the dialect via bead `mforth-l8z`, which is what makes
this full three-arm port possible.

**Compile it:**

```bash
mforth compile part4.fs -o part4.mlog
cat part4.mlog
```

15 instructions:

```
sensor s0 vault1 @surge-alloy
sensor s1 vault1 @blast-compound
set s2 s0
set s3 s1
op add s2 s2 s3
set s3 0
op equal s2 s2 s3
jump 10 equal s2 0
control config unloader1 null 0 0 0
jump 15 always 0 0
op greaterThan s0 s0 s1
jump 14 equal s0 0
control config unloader1 @blast-compound 0 0 0
jump 15 always 0 0
control config unloader1 @surge-alloy 0 0 0
```

| Metric | Wiki mlog | mforth source | mforth compiled |
|-------|----------|--------------|-----------------|
| Lines (non-blank, non-comment) | 24 | 13 | 15 |
| Named scratch variables | 8 | 0 | 0 |
| Branches | 4 jumps | 2 `IF/ELSE` | 4 jumps |

A note on faithfulness. This is now a *full* port: all three arms
of USR's script — drain the bigger pile, or null out the unloader
when both supplies are exhausted (the `set unload null` arm) — are
present. The null arm became expressible when the `NULL` literal
landed in the dialect (bead `mforth-l8z`). You can see this exact
program, with a status line added, run as an equivalence fixture at
`tests/integration/fixtures/equivalence/sorter_picker_null.fs`; the
suite asserts the host REPL and the compiled mlog produce the same
event sequence against the same world.

What you gained moving from raw mlog to mforth, even on this short
script:

- **Names are values, not addresses.** The wiki mlog has to invent
  `diffHas`, `hasMost`, `availLeast`, `sameIsh`, `emptyIsh`,
  `justOne`, `needAll`, `justFill` as named scratch slots and
  thread them through `op` instructions. The mforth source keeps
  the only two values it cares about on the data stack and
  consumes them with `>` directly.
- **The compiler catches stack mistakes.** If you typo'd the
  port and left the stack empty before `>`, mforth would refuse
  at compile time with a clear diagnostic. Raw mlog silently runs
  with garbage.
- **The compiled mlog is paste-ready and re-derivable.** Edit the
  `.fs` source, recompile, paste again. The wiki mlog is a
  read-only artifact: if you want to swap `@blast-compound` for
  `@thorium`, you re-edit by hand and re-count the jump targets.

---

## Part 5 — 'All In' as a definition

The wiki's *All In* enables five conveyors, one per resource, each
when the vault has room for more of that resource. The mlog source
is 23 lines of near-duplicate bookkeeping. In mforth we name the
comparison once and call it per resource.

**`part5.fs`:**

```forth
\ Part 5 — 'All In' as a definition.
\ Enable each conveyor when the vault has room for more of that resource.
: room-for-more? ( amount capacity -- 1/0 )
  >
;

\ --- pair 1: graphite → conveyor1 ---
foundation1 @itemCapacity SENSOR
foundation1 @graphite SENSOR room-for-more?
IF conveyor1 1 CONTROL-ENABLED ELSE conveyor1 0 CONTROL-ENABLED THEN

\ --- pair 2: metaglass → conveyor2 ---
foundation1 @itemCapacity SENSOR
foundation1 @metaglass SENSOR room-for-more?
IF conveyor2 1 CONTROL-ENABLED ELSE conveyor2 0 CONTROL-ENABLED THEN

\ --- pair 3: silicon → conveyor3 ---
foundation1 @itemCapacity SENSOR
foundation1 @silicon SENSOR room-for-more?
IF conveyor3 1 CONTROL-ENABLED ELSE conveyor3 0 CONTROL-ENABLED THEN

\ --- pair 4: plastanium → conveyor4 ---
foundation1 @itemCapacity SENSOR
foundation1 @plastanium SENSOR room-for-more?
IF conveyor4 1 CONTROL-ENABLED ELSE conveyor4 0 CONTROL-ENABLED THEN

\ --- pair 5: thorium → conveyor5 ---
foundation1 @itemCapacity SENSOR
foundation1 @thorium SENSOR room-for-more?
IF conveyor5 1 CONTROL-ENABLED ELSE conveyor5 0 CONTROL-ENABLED THEN
```

**`part5.world.toml`:**

```toml
[links.foundation1]
type   = "generic"
target = "foundation1"

[links.conveyor1]
type   = "switch"
target = "switch1"
enabled = false

[links.conveyor2]
type   = "switch"
target = "switch2"
enabled = false

[links.conveyor3]
type   = "switch"
target = "switch3"
enabled = false

[links.conveyor4]
type   = "switch"
target = "switch4"
enabled = false

[links.conveyor5]
type   = "switch"
target = "switch5"
enabled = false

[clock]
ipt      = 8
realtime = false
```

The new idea: the definition `room-for-more?` captures the
predicate once. Each pair lines up like a row in a table — two
sensors, a comparison, an enabled/disabled switch.

**Compile it:**

```bash
mforth compile part5.fs -o part5.mlog
wc -l part5.mlog
```

35 instructions of mlog for the five resources.

| Metric | Wiki "All In" mlog | mforth source | mforth compiled |
|-------|---------------------|--------------|-----------------|
| Lines (non-blank, non-comment) | 23 | 18 | 35 |
| Per-resource lines (source) | ~3.5 | 3 | 7 |

Be honest about the trade. The compiled mlog is *longer* than the
hand-written wiki version because v1 mforth's `CONTROL-ENABLED`
lifter requires a literal flag, so the comparison result has to go
through an `IF/ELSE` (two `control` instructions per pair, plus
jumps) instead of a single `control enabled cv flag 0 0 0` with a
named scratch. A tracked dialect gap — `mforth-vdt`, "Lifter gap:
CONTROL-ENABLED with stack-computed value" — would close that gap
and emit one `control` per pair, bringing the count back below the
hand-written original.

What you gained even with the larger compiled output:

- **The predicate has a name.** `room-for-more?` is searchable,
  refactorable, and self-documenting. The mlog version has
  `op lessThan graphF graph max` six times with a different
  variable letter each time.
- **Adding a resource is one block.** Three lines of source: a
  sensor, a sensor, the conditional. The wiki mlog version
  requires five lines, plus you have to pick a unique scratch
  variable name.
- **The stack effect is checked.** Try removing one `SENSOR` and
  mforth refuses to compile. Raw mlog would happily emit a `set`
  with one operand and explode at runtime.

---

## Part 6 — What's next

You have now written and compiled six mforth programs and ported
three of the simpler wiki scripts (*Just Charge*, *Sorter Picker*,
*All In*). The wiki has more programs, and some of them are out of
scope for v1 mforth on purpose.

### What v1 mforth can do today (block-side automation)

- Sense any block property (every Mindustry sensor property is a
  built-in word — `@itemCapacity`, `@health`, `@enabled`,
  `@progress`, `@totalItems`, `@power`, and more).
- Control any block: `CONTROL-ENABLED`, `CONTROL-CONFIG`,
  `CONTROL-SHOOT`, `CONTROL-SHOOTP`, `CONTROL-COLOR`.
- Reference items, liquids, units, and block kinds as first-class
  values (`@copper`, `@water`, `@flare`, `@vault`, …).
- Branch with `IF / ELSE / THEN`, loop with `DO / LOOP`, name
  values with `VARIABLE / @ / !`, factor patterns with `: ... ;`
  definitions.
- Print to message blocks: `PRINT`, `PRINTFLUSH`.
- Pace with `WAIT`.

### Wiki scripts you can write today

After this tutorial, you can write:

- `Just Charge` — Part 3 verbatim. Hysteresis on a power network.
- `ConveyorBlock` — the same pattern as Part 5, with whichever
  resources you actually use.
- `Sorter Picker` — Part 4's pattern; the simple "drain the
  bigger pile" version. (The full version needs `null`; see the
  dialect gaps below.)
- `Common config` — drop-in for Part 5's structure.
- `All In` — Part 5 verbatim.

### Wiki scripts that are v2-only

These need unit-binding primitives (`ubind`, `ucontrol`, `ulocate`)
or memory cells (`read`/`write` against a `bank1`) that v1
deliberately skipped. They are the v2 north star:

- `Heal Self`, `Heal Self When` — bind a unit and apply a heal.
- `Group Attack Heal` — coordinate a unit group's targeting.
- `Group Attack One Base` — assault-pattern coordination.
- `Circle Attack` — orbit a target.
- `Attack And Avoid Damaged` — combat micro.
- `Universal Balancer` — cross-processor balancing via memory
  cells.
- `VaultMover` — bind a unit and ferry items between vaults.

When v2 lands, this tutorial will get a sequel — for now, the
above are the explicit gap.

### Dialect gaps surfaced by this tutorial

While writing this tutorial, two dialect rough edges came up. Both
have v2 follow-up beads:

- **`null` literal for `CONTROL-CONFIG`** — the full Sorter Picker
  nulls out the unloader's config when supplies are exhausted. v1
  mforth has no `null`, so Part 4 ports the binary "pick the
  bigger pile" core without the null-out branch.
- **Stack-computed flag for `CONTROL-ENABLED`** (`mforth-vdt`) —
  the lifter currently requires a literal `0` or `1` for the
  enabled flag, which forces an `IF/ELSE` in Part 5 and inflates
  the compiled output. The bead is filed to close this gap.

### Where to go from here

- **[How-to: Use with Helix](../how-to/use-with-helix.md)** —
  syntax highlighting and the language server, so the diagnostics
  you saw in Part 3 appear inline as you type.
- **[How-to: Use with Neovim](../how-to/use-with-nvim.md)** —
  same, for Neovim.
- **[Reference](../reference/index.md)** — every word, every
  sidecar field, every CLI flag.
- **[Explanation: Why mforth](../explanation/why-mforth.md)**
  — why mforth chose the static-stack / cell-free / inline-everything
  shape it did.

Welcome aboard.
