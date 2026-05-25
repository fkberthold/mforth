# Event types

The `MockWorld` exposes one `EventStream` (`world.events`). Every
Mindustry primitive the host REPL executes — and every matching
instruction the in-repo mlog interpreter executes — emits a frozen
dataclass onto that stream. Subscribers (the web visualizer, the LSP
runtime diagnostics, the equivalence-test harness) attach via
`world.events.subscribe(callback)` or iterate the captured list after
the fact.

Source: [`src/mforth/backend/world.py`](https://github.com/fkberthold/mforth/blob/main/src/mforth/backend/world.py).

> **REPL ↔ mlog equivalence.** Every event listed below is also
> emitted by the in-repo mlog interpreter
> ([`src/mforth/mlog_interp.py`](https://github.com/fkberthold/mforth/blob/main/src/mforth/mlog_interp.py))
> when it executes the lowered instruction. That parity is what
> `tests/integration/test_equivalence.py` checks; a divergence in the
> emitted sequence is the highest-severity regression class
> ([why-mforth.md](../explanation/why-mforth.md)).

## Base shape

All events inherit one field from the base `Event` class.

| Field | Type | Note |
|---|---|---|
| `timestamp` | `float` | Stamped from `EventStream.tick` at emit time. The tick advances only when `WaitEvent` fires; everything in between shares a timestamp. |

Events are constructed via `EventStream.emit(cls, **payload)` — the
stream supplies `timestamp` itself, so callers pass only the
payload-specific keyword arguments.

## `MessagePrintEvent`

**Fired when:** the REPL primitive `PRINT` (Forth `.` and string
literals) consumes a value from the data stack and calls
`world.print(text)`. Integer-valued floats render without a trailing
`.0` to match the in-game `print` instruction's stringification rule
(bead `mforth-05h`).

| Field | Type | Note |
|---|---|---|
| `timestamp` | `float` | (base) |
| `text` | `str` | Already coerced to a string by `world.print`. |

**Subscribed by:** equivalence fixtures
(`tests/integration/test_equivalence.py`,
`tests/integration/test_blink_counter.py`); web viz
(`src/mforth/viz/server.py` — pushed as a `print` frame); unit tests
in `tests/unit/test_world.py` and `tests/unit/test_primitives.py`.

## `MessagePrintflushEvent`

**Fired when:** `PRINTFLUSH <block>` runs. The accumulated print
buffer is concatenated, the target block's buffer is replaced (matching
mlog: each printflush is a fresh message), and the event is emitted
**regardless of whether the named block exists** — missing-block
invocations still emit so subscribers can count attempts.

| Field | Type | Note |
|---|---|---|
| `timestamp` | `float` | (base) |
| `block_name` | `str` | The mforth-name (sidecar left-hand side), not the in-game name. |
| `buffer` | `str` | The concatenated print-queue contents at flush time. Empty string if nothing queued. |

**Subscribed by:** equivalence fixtures; web viz (drives the
message-block panel); `tests/unit/test_world.py`.

## `WaitEvent`

**Fired when:** `WAIT` runs. `world.wait(seconds)` advances
`EventStream.tick` by `float(seconds)` *before* emitting; subsequent
events carry the new tick.

| Field | Type | Note |
|---|---|---|
| `timestamp` | `float` | (base) — already includes the just-applied delta. |
| `seconds` | `float` | The float-coerced wait duration. |

**Subscribed by:** equivalence fixtures (the timestamp sequence is
part of the equivalence property); web viz (drives the simulated
clock); `tests/unit/test_world.py`.

## `SensorReadEvent`

**Fired when:** `SENSOR` runs (Forth `<block> <prop> SENSOR`). The
event is emitted on every call, including missing-block and
unknown-property cases (which resolve to `0.0`, matching the
community-lore mlog behaviour for invalid sensor targets).

| Field | Type | Note |
|---|---|---|
| `timestamp` | `float` | (base) |
| `block_name` | `str` | The mforth-name of the sensed block. |
| `prop` | `str` | The property identifier (e.g. `@copper`, `@itemCapacity`). |
| `value` | `float` | Read value; `0.0` on missing block or unknown property. Booleans are coerced (`1.0` / `0.0`). |

**Subscribed by:** equivalence fixtures; web viz (annotates the
linked-block panel); `tests/unit/test_world.py`.

## `LinkResolvedEvent`

**Fired when:** `GETLINK i` succeeds. Out-of-range indices return
`None` (mlog: `null`) and **do not** emit — this is the one
intentional asymmetry in the event surface.

| Field | Type | Note |
|---|---|---|
| `timestamp` | `float` | (base) |
| `index` | `int` | The link index that was looked up. |
| `block_name` | `str` | The resolved mforth-name. |

**Subscribed by:** equivalence fixtures; LSP runtime diagnostics (to
trace link discovery); `tests/unit/test_world.py`.

## `ControlEvent`

**Fired when:** any `CONTROL-*` primitive runs (`CONTROL-ENABLED`,
`CONTROL-CONFIG`, `CONTROL-SHOOT`, `CONTROL-SHOOTP`, `CONTROL-COLOR`).
Always emits — missing-block invocations still record the attempt;
only the state mutation is skipped. For `shoot` / `shootp` / `color`
the event *is* the only observable on the mock side.

| Field | Type | Note |
|---|---|---|
| `timestamp` | `float` | (base) |
| `op` | `str` | Sub-command name: `"enabled"`, `"config"`, `"shoot"`, `"shootp"`, `"color"`. |
| `block_name` | `str` | Target block's mforth-name. |
| `args` | `tuple` | Remaining operands captured verbatim from the data-stack pop / mlog operand list. Shape varies by `op`; see bead `mforth-cto`. |

**Subscribed by:** equivalence fixtures
(`tests/unit/test_control.py` carries the per-op shape assertions); web
viz (annotates the controlled block); LSP diagnostics.

## `VariableReadEvent`

**Fired when:** the host REPL or the mlog interpreter reads a
user-declared variable via `world.read_variable(name)`. Missing names
default to `0.0` (mlog: `null`).

| Field | Type | Note |
|---|---|---|
| `timestamp` | `float` | (base) |
| `name` | `str` | The variable name as written in source. |
| `value` | `float` | The read value (already float-coerced). |

**Important scope rule (bead `mforth-0qi`):** the mlog interpreter
emits this event only for names in its `user_variables` set — i.e.
source-declared `VARIABLE foo`. Compiler-internal names (`s0..sN`
stack slots, `__swap_tmp`, `@`-prefixed magic vars) bypass
instrumentation, and sidecar-pre-seeded link-name handles are *not*
instrumented either (the REPL never instruments them either, so the
two surfaces agree).

**Subscribed by:** equivalence fixtures (the read sequence is part of
the equivalence property for any program touching `VARIABLE`);
`tests/unit/test_world.py`.

## `VariableWriteEvent`

**Fired when:** the host REPL or the mlog interpreter writes a
user-declared variable via `world.write_variable(name, value)`. Same
scope rule as `VariableReadEvent`: only source-declared variables are
instrumented.

| Field | Type | Note |
|---|---|---|
| `timestamp` | `float` | (base) |
| `name` | `str` | The variable name as written in source. |
| `value` | `float` | The written value (already float-coerced). |

**Subscribed by:** equivalence fixtures; web viz (drives the
variable-state panel); `tests/unit/test_world.py`.

## Cross-bead contract notes

- The Mode A (sidecar) and Mode B (link prologue) link-resolution
  contracts (see drawer `drawer_mforth_decisions_a1a27472823dbf78f9cbd35e`)
  do **not** introduce new event types — both resolve link-name
  handles before any event is emitted, so downstream subscribers see a
  uniform shape regardless of which mode produced the link map.
- Adding a new Mindustry primitive means: (1) adding the matching
  dataclass here, (2) emitting it from `MockWorld`, (3) emitting it
  from the corresponding `mlog_interp.py` opcode handler, and (4)
  shipping an equivalence fixture pair. The event shape *is* the
  contract those four sites agree on.
