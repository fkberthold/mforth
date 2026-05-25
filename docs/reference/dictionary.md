# Dictionary

Every name `mforth` resolves out of the box. Source of truth:
[`src/mforth/dictionary.py`](https://github.com/fkberthold/mforth/blob/main/src/mforth/dictionary.py)
(the registry) and
[`src/mforth/backend/primitives.py`](https://github.com/fkberthold/mforth/blob/main/src/mforth/backend/primitives.py)
(the host implementations). Lookups are case-insensitive.

This page is an austere catalogue: name, stack effect, one-line semantic, one
compact mlog form. For *why* the dialect chose this surface, see
[Why mforth](../explanation/why-mforth.md) and
[Forth, the mental model](../explanation/forth-mental-model.md). For the full
emission patterns (literal lifting, fusion, control-flow lowering), see the
mlog instruction-set reference page.

Notation in the stack-effect column follows Forth tradition: `( before -- after )`,
top of stack on the right.

## At a glance

| Bucket | Count |
| --- | --- |
| Stack juggle | 7 |
| Arithmetic | 5 |
| Comparison | 6 |
| Logical | 3 |
| I/O | 1 |
| Variables (`@`, `!`, `VARIABLE`) | 3 |
| DO/LOOP counters (`I`, `J`) | 2 |
| Mindustry primitives | 5 |
| Mindustry CONTROL sub-commands | 5 |
| **Forth built-ins, total** | **37** |
| `@`-magic vars | 29 |
| `@`-items | 22 |
| `@`-liquids | 11 |
| `@`-units | 22 |
| `@`-blocks | 15 |
| `@`-sensor properties | 71 |
| `@`-aliases (`@ticks` → `@tick`) | 1 |
| **Mindustry `@`-identifiers, total** | **171** |

In addition, every name declared in the program's `.world.toml` sidecar's
`[links.X]` table resolves as a `UserVariable` (block handle); every
`VARIABLE <name>` in source resolves the same way. Those are program-specific,
not catalogued here.

---

## Stack juggle

The bread-and-butter rearrangement words. Static stack effect is what makes
`stackcheck` decidable; that's the whole point.

| Name | Stack effect | Semantic | mlog form |
| --- | --- | --- | --- |
| `DUP` | `( a -- a a )` | duplicate top of stack | `set sN+1 sN` |
| `DROP` | `( a -- )` | discard top of stack | *(no instruction; slot dies)* |
| `SWAP` | `( a b -- b a )` | swap top two items | 3 × `set` via `__swap_tmp` |
| `OVER` | `( a b -- a b a )` | copy second item to top | `set sN+1 sN-1` |
| `ROT` | `( a b c -- b c a )` | rotate top three | 4 × `set` via `__swap_tmp` |
| `NIP` | `( a b -- b )` | remove second item | `set sN-1 sN` |
| `TUCK` | `( a b -- b a b )` | copy top under second | 4 × `set` via `__swap_tmp` |

## Arithmetic

`/` is **float division** (mlog `op div`), not Forth-traditional integer
division. This is a load-bearing dialect choice — the host REPL uses Python's
`/`, the mlog backend matches. Division by zero produces `inf` / `-inf` / `nan`
without raising; matches mlog's silent-on-error behaviour.

Source-level **integer** and **float** literals are both first-class. Integer
literals (`42`, `-7`, `+13`) lex as `LitInt`; decimal literals with at least
one digit on each side of the `.` (and optional `[eE][-+]?\d+` exponent)
lex as `LitFloat` — `0.95`, `3.14`, `-2.5`, `1.0e-3` (bead mforth-xk7). Both
have stack effect `( -- n )` / `( -- f )` and lower to `set s<i> <value>`.
The Forth `.` (pop-and-print) word stays a separate token because
whitespace-delimited tokenization keeps `3 . 14` as three tokens; the
disallowed shapes `3.` / `.5` / `3.14.15` fall through to `WordCall` and
fail dictionary resolution.

| Name | Stack effect | Semantic | mlog form |
| --- | --- | --- | --- |
| `+` | `( a b -- a+b )` | add | `op add sN-1 sN-1 sN` |
| `-` | `( a b -- a-b )` | subtract | `op sub sN-1 sN-1 sN` |
| `*` | `( a b -- a*b )` | multiply | `op mul sN-1 sN-1 sN` |
| `/` | `( a b -- a/b )` | float divide; zero ⇒ inf/nan | `op div sN-1 sN-1 sN` |
| `MOD` | `( a b -- a%b )` | modulo; zero ⇒ nan | `op mod sN-1 sN-1 sN` |

## Comparison

Pushes mlog's **0/1 encoding** (not Forth-traditional `0` / `-1`). mlog's
`op equal` / `op lessThan` / etc. write `0` or `1`; the REPL matches so the
equivalence property holds. mlog's own conditional `jump` consumes 0/1
natively, so this is also the cheapest encoding for `IF` and friends.

| Name | Stack effect | Semantic | mlog form |
| --- | --- | --- | --- |
| `=` | `( a b -- flag )` | equal | `op equal sN-1 sN-1 sN` |
| `<>` | `( a b -- flag )` | not equal | `op notEqual sN-1 sN-1 sN` |
| `<` | `( a b -- flag )` | less than | `op lessThan sN-1 sN-1 sN` |
| `>` | `( a b -- flag )` | greater than | `op greaterThan sN-1 sN-1 sN` |
| `<=` | `( a b -- flag )` | less than or equal | `op lessThanEq sN-1 sN-1 sN` |
| `>=` | `( a b -- flag )` | greater than or equal | `op greaterThanEq sN-1 sN-1 sN` |

## Logical

Bitwise on the 0/1 encoding — with 0/1 inputs these collapse to the boolean
truth table. With non-0/1 inputs `AND` / `OR` are bitwise integer ops; `NOT`
is `0 → 1`, anything truthy `→ 0`.

| Name | Stack effect | Semantic | mlog form |
| --- | --- | --- | --- |
| `AND` | `( a b -- a&b )` | logical/bitwise and | `op land sN-1 sN-1 sN` |
| `OR` | `( a b -- a\|b )` | logical/bitwise or | `op or sN-1 sN-1 sN` |
| `NOT` | `( a -- !a )` | logical not (0 ↔ 1) | `op not sN sN 0` |

## I/O

| Name | Stack effect | Semantic | mlog form |
| --- | --- | --- | --- |
| `.` | `( n -- )` | print top of stack (host only) | *host-only — not yet emitted by mlog backend* |

Integer-valued floats print without a trailing `.0` (matches the in-game
`print` instruction). Booleans print as `1` / `0`.

## Variables

mforth v1 is cell-free: `VARIABLE foo` declares a bare mlog variable named
`foo`. The `<name> @` and `<name> !` patterns fuse at emit time to a single
`set` — there is no addressable cell, no on-stack address. Writing
`foo DUP @` raises at compile time: there is no v1 lowering for an
address-on-stack.

| Name | Stack effect | Semantic | mlog form |
| --- | --- | --- | --- |
| `@` | `( addr -- value )` | fetch variable's value (fused) | `set sN <name>` (fused) |
| `!` | `( value addr -- )` | store value into variable (fused) | `set <name> sN-1` (fused) |
| `VARIABLE` | `( -- )` | declare a variable: `VARIABLE <name>` | *(no instruction; declaration only)* |

## DO/LOOP counters

Valid only inside a `DO` … `LOOP` body. `I` is the innermost counter, `J` the
next-outer. Out-of-context use raises at emit time.

| Name | Stack effect | Semantic | mlog form |
| --- | --- | --- | --- |
| `I` | `( -- n )` | push current DO/LOOP counter | `set sN __do_idx_<N>` |
| `J` | `( -- n )` | push outer DO/LOOP counter | `set sN __do_idx_<N-outer>` |

## Mindustry primitives

The five v1 Mindustry primitives. Block handles on the data stack are bare
mforth-name strings (matching the `.world.toml` `[links.X]` keys); the same
bare names appear unquoted in emitted mlog. Each primitive supports a
**literal-lifting fast path** — when its operand is a compile-time literal or
a sidecar link name, the operand folds inline and the otherwise-required
`set sN <value>` is elided. The slot-form fallback is what's shown below.

| Name | Stack effect | Semantic | mlog form |
| --- | --- | --- | --- |
| `PRINT` | `( v -- )` | queue value to print buffer | `print sN` |
| `PRINTFLUSH` | `( block -- )` | flush print buffer to message block | `printflush sN` |
| `WAIT` | `( seconds -- )` | pause execution for N seconds | `wait sN` |
| `SENSOR` | `( block prop -- value )` | read block property | `sensor sN-1 sN-1 sN` |
| `GETLINK` | `( i -- block )` | retrieve i-th linked block | `getlink sN sN` |

`GETLINK` with `i` out of range pushes `None` (mlog `null`) so the static
stack effect holds. `SENSOR` on a missing block or missing property yields
`0.0` (mlog community-lore behaviour, pinned in `MockWorld`).

## Mindustry CONTROL sub-commands

mlog's `control` instruction routes via a sub-command keyword. mforth exposes
each as its own word so every CONTROL call has a static stack effect. mlog's
`control` always takes 5 operands after the sub-command; mforth pads unused
positions with `0`.

| Name | Stack effect | Semantic | mlog form |
| --- | --- | --- | --- |
| `CONTROL-ENABLED` | `( block flag -- )` | enable/disable a block | `control enabled sN-1 sN 0 0 0` |
| `CONTROL-CONFIG` | `( block value -- )` | configure (e.g. sorter target) | `control config sN-1 sN 0 0 0` |
| `CONTROL-SHOOT` | `( block x y shoot -- )` | aim+fire turret at coordinate | `control shoot sN-3 sN-2 sN-1 sN 0` |
| `CONTROL-SHOOTP` | `( block unit shoot -- )` | aim+fire turret at a unit | `control shootp sN-2 sN-1 sN 0 0` |
| `CONTROL-COLOR` | `( block r g b -- )` | set illuminator color | `control color sN-3 sN-2 sN-1 sN 0` |

`UCONTROL` (unit control) is v2; only block-side `CONTROL` ships in v1.

---

## Mindustry `@`-identifiers

Every `@`-prefixed name mforth recognizes pushes a single value
(stack effect `( -- v )`). The host REPL uses deterministic stub values
(below) so the REPL ↔ mlog equivalence property holds despite Mindustry's
runtime values being inherently non-deterministic; the mlog interpreter
pre-seeds the same stubs. Content names and sensor properties push their bare
`@name` string as an opaque tag — the same form mlog uses in its emitted
source.

When an `@`-identifier immediately precedes `SENSOR`, `PRINTFLUSH`, or `PRINT`,
the bare `@name` lifts inline (`set sN @copper` is elided; `sensor sN block @copper`
appears directly). The slot-form column below shows the unlifted fallback.

### Magic vars (29)

Pushed values are deterministic stubs for REPL ↔ mlog equivalence; the
in-game runtime values are inherently non-deterministic.

| Name | Stub | Semantic |
| --- | --- | --- |
| `@counter` | `0` | instruction-pointer index (zero-indexed; writable for jumps) |
| `@this` | `"@this"` | the processor block itself, as a building handle |
| `@thisx` | `0.0` | processor's world x coordinate |
| `@thisy` | `0.0` | processor's world y coordinate |
| `@ipt` | `8` | instructions per tick — micro=2, logic=8, hyper=25 |
| `@links` | `0` | number of buildings linked to this processor |
| `@unit` | `null` | the currently bound unit (set by `ubind`); null if no bind |
| `@time` | `0.0` | microseconds since the save was loaded |
| `@tick` | `0` | ticks since save loaded (raw `state.tick`) |
| `@second` | `0.0` | seconds elapsed since save loaded |
| `@minute` | `0.0` | minutes elapsed since save loaded |
| `@waveNumber` | `1` | current wave number |
| `@waveTime` | `0.0` | seconds remaining in current wave |
| `@mapw` | `40` | map width in tiles |
| `@maph` | `40` | map height in tiles |
| `@pi` | `math.pi` | π (`Mathf.PI`) |
| `@e` | `math.e` | Euler's number (`Mathf.E`) |
| `@degToRad` | `π/180` | degree → radian conversion factor |
| `@radToDeg` | `180/π` | radian → degree conversion factor |
| `@server` | `0` | 1 if running on server, else 0 |
| `@client` | `1` | 1 if running on client, else 0 |
| `@air` | `"@air"` | sentinel: tile is air (buildable/walkable) |
| `@solid` | `"@solid"` | sentinel: tile is solid (wall/terrain) |
| `@ctrlProcessor` | `1` | control-source constant: processor |
| `@ctrlPlayer` | `2` | control-source constant: player |
| `@ctrlCommand` | `3` | control-source constant: command center |
| `@commandAttack` | `"@commandAttack"` | command-center config: attack |
| `@commandRally` | `"@commandRally"` | command-center config: rally |
| `@commandIdle` | `"@commandIdle"` | command-center config: idle |

**Aliases.** `@ticks` is an alias for `@tick` — both look up the same entry
(Wiki / source disagreement; mforth accepts both).

### Items (22)

| Name | Semantic |
| --- | --- |
| `@copper` | item: copper |
| `@lead` | item: lead |
| `@metaglass` | item: metaglass |
| `@graphite` | item: graphite |
| `@sand` | item: sand |
| `@coal` | item: coal |
| `@titanium` | item: titanium |
| `@thorium` | item: thorium |
| `@scrap` | item: scrap |
| `@silicon` | item: silicon |
| `@plastanium` | item: plastanium |
| `@phase-fabric` | item: phase fabric (Java: `phaseFabric`) |
| `@surge-alloy` | item: surge alloy (Java: `surgeAlloy`) |
| `@spore-pod` | item: spore pod (Java: `sporePod`) |
| `@blast-compound` | item: blast compound (Java: `blastCompound`) |
| `@pyratite` | item: pyratite |
| `@beryllium` | item: beryllium (Erekir) |
| `@tungsten` | item: tungsten (Erekir) |
| `@oxide` | item: oxide (Erekir) |
| `@carbide` | item: carbide (Erekir) |
| `@fissile-matter` | item: fissile matter (Erekir; Java: `fissileMatter`) |
| `@dormant-cyst` | item: dormant cyst (Erekir; Java: `dormantCyst`) |

### Liquids (11)

| Name | Semantic |
| --- | --- |
| `@water` | liquid: water |
| `@slag` | liquid: slag |
| `@oil` | liquid: oil |
| `@cryofluid` | liquid: cryofluid |
| `@neoplasm` | liquid: neoplasm |
| `@arkycite` | liquid: arkycite |
| `@gallium` | liquid: gallium |
| `@ozone` | liquid: ozone |
| `@hydrogen` | liquid: hydrogen |
| `@nitrogen` | liquid: nitrogen |
| `@cyanogen` | liquid: cyanogen |

### Units (22)

Essential v1 subset (Serpulo ground T1–T5, support T1–T4, air T1–T5, drones
T1–T5, three player-controllables). Remaining ~40 Erekir + naval + ground-legs
units are deferred to v2.

| Name | Semantic |
| --- | --- |
| `@dagger` | unit: dagger (Serpulo ground T1) |
| `@mace` | unit: mace (Serpulo ground T2) |
| `@fortress` | unit: fortress (Serpulo ground T3) |
| `@scepter` | unit: scepter (Serpulo ground T4) |
| `@reign` | unit: reign (Serpulo ground T5) |
| `@nova` | unit: nova (Serpulo support T1) |
| `@pulsar` | unit: pulsar (Serpulo support T2) |
| `@quasar` | unit: quasar (Serpulo support T3) |
| `@vela` | unit: vela (Serpulo support T4) |
| `@flare` | unit: flare (Serpulo air T1) |
| `@horizon` | unit: horizon (Serpulo air T2) |
| `@zenith` | unit: zenith (Serpulo air T3) |
| `@antumbra` | unit: antumbra (Serpulo air T4) |
| `@eclipse` | unit: eclipse (Serpulo air T5) |
| `@mono` | unit: mono (drone T1) |
| `@poly` | unit: poly (drone T2) |
| `@mega` | unit: mega (drone T3) |
| `@quad` | unit: quad (drone T4) |
| `@oct` | unit: oct (drone T5) |
| `@alpha` | unit: alpha (player-controllable) |
| `@beta` | unit: beta (player-controllable) |
| `@gamma` | unit: gamma (player-controllable) |

### Blocks (15)

Essential v1 subset. The remaining ~225 block types are deferred to v2.

| Name | Semantic |
| --- | --- |
| `@micro-processor` | block: micro processor (2 ipt) |
| `@logic-processor` | block: logic processor (8 ipt) |
| `@hyper-processor` | block: hyper processor (25 ipt) |
| `@world-processor` | block: world processor (privileged) |
| `@message` | block: message (target for `printflush`) |
| `@switch` | block: switch (sensor target — `@enabled`) |
| `@memory-cell` | block: memory cell (64 doubles) |
| `@memory-bank` | block: memory bank (512 doubles) |
| `@logic-display` | block: logic display (drawing target) |
| `@large-logic-display` | block: large logic display |
| `@core-shard` | block: core (shard) |
| `@core-foundation` | block: core (foundation) |
| `@core-nucleus` | block: core (nucleus) |
| `@container` | block: container (storage) |
| `@vault` | block: vault (storage) |

### Sensor properties (71)

Read with `SENSOR` (`( block prop -- value )`). Names come from mlog's
`LAccess` enum. Settable sensors (`@enabled`, `@shoot`, `@configure`, `@color`)
are read-only in v1 — the `CONTROL-*` words above are the write path.

#### Inventory & resource

| Name | Semantic |
| --- | --- |
| `@totalItems` | total item count in block |
| `@firstItem` | first/dominant item (content handle) |
| `@totalLiquids` | total liquid amount in block |
| `@totalPower` | total power stored |
| `@itemCapacity` | max items the block can hold |
| `@liquidCapacity` | max liquid the block can hold |
| `@powerCapacity` | max power the block can hold |
| `@powerNetStored` | power network total stored |
| `@powerNetCapacity` | power network total capacity |
| `@powerNetIn` | power network input rate |
| `@powerNetOut` | power network output rate |
| `@ammo` | current ammo count (turrets) |
| `@ammoCapacity` | max ammo |
| `@currentAmmoType` | current ammo type (content handle) |
| `@memoryCapacity` | memory cell/bank capacity |

#### Entity state

| Name | Semantic |
| --- | --- |
| `@health` | current hit points |
| `@maxHealth` | max hit points |
| `@heat` | heat (reactors) |
| `@shield` | shield amount |
| `@armor` | armor stat |
| `@efficiency` | production efficiency 0..1 |
| `@progress` | production progress 0..1 |
| `@timescale` | time multiplier from overdrive |
| `@rotation` | rotation in degrees |
| `@x` | world x coordinate |
| `@y` | world y coordinate |
| `@velocityX` | velocity x (units) |
| `@velocityY` | velocity y (units) |
| `@shootX` | aim point x (turrets/units) |
| `@shootY` | aim point y |
| `@cameraX` | player camera x |
| `@cameraY` | player camera y |
| `@cameraWidth` | player viewport width |
| `@cameraHeight` | player viewport height |
| `@displayWidth` | display block pixel width |
| `@displayHeight` | display block pixel height |
| `@size` | block size in tiles (1/2/3/4) |
| `@dead` | 1 if entity destroyed |
| `@range` | effective range (turrets/units) |
| `@shooting` | 1 if currently shooting |
| `@boosting` | 1 if unit is boosting |

#### Mining / building / movement

| Name | Semantic |
| --- | --- |
| `@mineX` | mine target x |
| `@mineY` | mine target y |
| `@mining` | 1 if mining |
| `@buildX` | build target x |
| `@buildY` | build target y |
| `@building` | 1 if building |
| `@breaking` | 1 if deconstructing |
| `@pingX` | ping marker x |
| `@pingY` | ping marker y |
| `@pingText` | ping marker text |
| `@speed` | movement speed |

#### Identity & classification

| Name | Semantic |
| --- | --- |
| `@team` | entity's team handle |
| `@type` | UnitType or block type handle |
| `@flag` | user-set flag value (units) |
| `@controlled` | control source (matches `@ctrl*`) |
| `@controller` | the controlling entity |
| `@name` | player name (units only) |
| `@id` | entity id |

#### Payload (Erekir)

| Name | Semantic |
| --- | --- |
| `@payloadCount` | number of payloads held |
| `@payloadType` | type of held payload |
| `@totalPayload` | total payload mass |
| `@payloadCapacity` | max payload capacity |
| `@maxUnits` | max simultaneously controllable units |

#### Ammo / projectile

| Name | Semantic |
| --- | --- |
| `@bufferSize` | mass driver / link buffer size |
| `@operations` | buffered operations count |
| `@bulletLifetime` | bullet lifetime stat |
| `@bulletTime` | bullet age |

#### Block-specific config

| Name | Semantic |
| --- | --- |
| `@selectedBlock` | currently selected block |
| `@selectedRotation` | selected rotation for placement |
| `@config` | block's current config value |

---

## Things not in the dictionary

Naming the deliberate absences is itself part of the reference surface.

- **`POSTPONE`, `IMMEDIATE`, `DOES>`, `EXECUTE`** — meta-compilation. The
  pragmatic-Forth dialect choice rules these out so static stack analysis
  stays decidable. See [Why mforth](../explanation/why-mforth.md#what-it-gives-up).
- **Memory cells** (`@`/`!` against an addressable cell) — v1 is cell-free.
  `VARIABLE` compiles to a bare mlog variable, not a cell address. v2 reopens
  this with an explicit `--mem=<cell>` flag.
- **Return stack words** (`>R`, `R>`, `R@`) — no return stack in v1; every
  user-defined word is inlined.
- **`UCONTROL`** (unit control) — deferred to v2.
- **Privileged / world-processor magic vars** (`@wait`, `@client*`) — deferred
  to v2.
- **Settable LAccess via `SENSOR`** (`@enabled`, `@shoot`, `@configure`,
  `@color` as writes) — read-only in v1; the corresponding `CONTROL-*` word
  is the write path.
- **Unicode `π` alias** — type `@pi`.

## See also

- [Why mforth](../explanation/why-mforth.md) — the REPL ↔ mlog equivalence
  contract this surface serves.
- [Forth, the mental model](../explanation/forth-mental-model.md) — what the
  stack-effect column is for.
- [Reference index](index.md) — sibling pages (CLI, sidecar schema, event
  types, mlog instructions).
