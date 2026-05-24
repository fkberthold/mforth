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

The six parts:

| Part | What you build | Time |
|------|---------------|------|
| [1](#part-1-hello-message-block) | `hello, mforth` to a message block | ~5 min |
| [2](#part-2-a-counter-you-can-paste-in-game) | A counter that ticks on a message block | ~10 min |
| [3](#part-3-sense-and-control-a-single-block) | Enable a conveyor when a vault is low | ~10 min |
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

## Part 3 — Sense and control a single block

This is the first program that *reads* the world. We sense how much
copper is in a vault, compare against a threshold, and enable a
conveyor when the vault is low.

**`part3.fs`:**

```forth
\ Part 3 — Sense vault, control conveyor.
\ vault1 @copper SENSOR pushes the current copper amount in vault1.
\ The CONTROL-ENABLED lifter wants a literal flag, so we choose 1
\ or 0 inside an IF/ELSE/THEN rather than computing it on the stack.
vault1 @copper SENSOR 500 < IF
  conveyor1 1 CONTROL-ENABLED
ELSE
  conveyor1 0 CONTROL-ENABLED
THEN
```

**`part3.world.toml`:**

```toml
[links.vault1]
type   = "generic"
target = "vault1"

[links.conveyor1]
type   = "switch"
target = "switch1"
enabled = false

[clock]
ipt      = 8
realtime = false
```

The new ideas:

- `vault1 @copper SENSOR` is mforth's spelling of mlog's
  `sensor result vault1 @copper`. The block name and the content
  identifier (`@copper` is a built-in word; mforth ships 154 of
  them — items, liquids, units, blocks, sensor properties) go on
  the stack first, then `SENSOR` consumes both and pushes the
  read value.
- `500 <` compares: the value below 500 leaves `1` (true) on the
  stack, otherwise `0` (false).
- `IF ... ELSE ... THEN` is Forth's conditional. It consumes the
  flag from the top of the stack and runs one of the two arms.
- `conveyor1 1 CONTROL-ENABLED` is mforth's spelling of mlog's
  `control enabled conveyor1 1 0 0 0`. Because both the block and
  the flag are literals, mforth's emitter recognises the pattern
  and emits a single tight instruction.

**Run it:**

```bash
mforth run --no-loop part3.fs
```

Silent. The mock vault has no copper seeded, so the comparison was
`0 < 500` → true → the `CONTROL-ENABLED` branch with flag `1`
fired.

**Compile it:**

```bash
mforth compile part3.fs -o part3.mlog
cat part3.mlog
```

Seven instructions:

```
sensor s0 vault1 @copper
set s1 500
op lessThan s0 s0 s1
jump 6 equal s0 0
control enabled conveyor1 1 0 0 0
jump 7 always 0 0
control enabled conveyor1 0 0 0 0
```

Paste this into a logic processor with a vault and a conveyor
switch linked, and the conveyor will turn on whenever the vault
has fewer than 500 units of copper.

A quick aside on what mforth caught for you. If you had tried
`conveyor1 SWAP CONTROL-ENABLED` to push the comparison's result
as the flag, mforth would have refused at compile time with:

```
VARIABLE address 'conveyor1' is being consumed by 'SWAP' rather
than by @ or ! — v1 mforth is cell-free and does not support
manipulating variable addresses as stack values
```

That diagnostic is what makes mforth a *teaching* compile target,
not just an mlog frontend. In raw mlog the same mistake would
have silently produced wrong behaviour at runtime.

---

## Part 4 — The Sorter Picker rewrite

Here is where mforth earns its keep. We are going to port a real
mlog script from the [Mindustry wiki][wiki] — the *Sorter Picker*
— and put the original mlog and the mforth source side-by-side.

[wiki]: https://github.com/Anuken/Mindustry/wiki

The job: an unloader on `unloader1` pulls items out of `vault1`.
We pick what to drain based on which item vault1 holds more of —
balance the pile by reducing whichever side is larger. Items
considered: `@surge-alloy` and `@blast-compound`.

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
branches. The thing it actually does is binary: pick the bigger
item; null out when supplies are exhausted upstream.

**The mforth equivalent (`part4.fs`):**

```forth
\ Part 4 — Sorter Picker port.
\ Drain whichever item vault1 holds more of, to keep them balanced.
vault1 @surge-alloy SENSOR       \ ( -- hasSurge )
vault1 @blast-compound SENSOR    \ ( hasSurge -- hasSurge hasBlast )

> IF
  unloader1 @blast-compound CONTROL-CONFIG
ELSE
  unloader1 @surge-alloy CONTROL-CONFIG
THEN
```

**`part4.world.toml`:**

```toml
[links.vault1]
type   = "generic"
target = "vault1"

[links.vault2]
type   = "generic"
target = "vault2"

[links.unloader1]
type   = "generic"
target = "unloader1"

[clock]
ipt      = 8
realtime = false
```

The two readings push their values onto the stack in order; the
bare `>` consumes both and leaves the comparison result. `IF`
picks the branch.

**Compile it:**

```bash
mforth compile part4.fs -o part4.mlog
cat part4.mlog
```

Seven instructions:

```
sensor s0 vault1 @surge-alloy
sensor s1 vault1 @blast-compound
op greaterThan s0 s0 s1
jump 6 equal s0 0
control config unloader1 @blast-compound 0 0 0
jump 7 always 0 0
control config unloader1 @surge-alloy 0 0 0
```

| Metric | Wiki mlog | mforth source | mforth compiled |
|-------|----------|--------------|-----------------|
| Lines (non-blank, non-comment) | 24 | 7 | 7 |
| Named scratch variables | 8 | 0 | 0 |
| Branches | 4 jumps | 1 `IF/ELSE` | 2 jumps |

A note on faithfulness. mforth's port is *narrower* than the wiki
original: USR's script also nulls out the unloader entirely when
both `vault2` supplies are exhausted (the `set unload null` arm).
v1 mforth has no `null` literal yet, so that branch is recorded
as future work — see Part 6.

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
two of the simpler wiki scripts. The wiki has more programs, and
some of them are out of scope for v1 mforth on purpose.

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

- `ConveyorBlock` — the same pattern as Part 5, with whichever
  resources you actually use.
- `Sorter Picker` — Part 4's pattern; the simple "drain the
  bigger pile" version. (The full version needs `null`; see the
  dialect gaps below.)
- `Common config` — drop-in for Part 5's structure.
- `All In` — Part 5 verbatim.

### Wiki scripts that are v2-only

These need unit-binding primitives (`ubind`, `ucontrol`, `ulocate`)
that v1 deliberately skipped. They are the v2 north star:

- `Just Charge` — bind a unit and route it to a power node.
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
- **[Explanation: Mental model](../explanation/mental-model.md)**
  — why mforth chose the static-stack / cell-free / inline-everything
  shape it did.

Welcome aboard.
