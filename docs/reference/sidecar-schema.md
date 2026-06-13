# Sidecar schema (`.world.toml`)

Every `<name>.fs` source may be paired with a sibling `<name>.world.toml`
that declares the Mindustry environment the host REPL (and the in-repo
mlog interpreter) simulate against. The sidecar is also what the LSP
reads to resolve link names for hover and diagnostics.

This page catalogues every section, key, and value-shape the loader
(`mforth.backend.sidecar`) accepts, and shows a minimal failing example
for every error path. For the rationale of `target` vs `index`, see
[Why mforth](../explanation/why-mforth.md). For the recipe of pinning a
link by `index` (the opt-in, fragile mode), see the upcoming
`pin-sidecar-link-by-index` how-to.

## Top-level shape

```toml
[links.<mforth-name>]   # zero or more; one section per link
type   = "<link-type>"
target = "<in-game-name>"   # Mode A — recommended
# OR
index  = N                  # Mode B — opt-in, fragile to re-link order
# type-specific (optional):
size    = N                 # memory-cell capacity
enabled = bool              # switch initial state
# seeded sensor readings (optional):
sensors = { "@copper" = 240, "@totalItems" = 80 }

[clock]
ipt      = 2 | 8 | 25       # micro | logic | hyper processor
realtime = bool             # advance `wait` instantly in tests
```

An empty sidecar (`{}`) is valid: it yields no links and the default
clock (`ipt = 8`, `realtime = false`). Sections are independent — a
sidecar may declare only `[clock]`, only `[links.*]`, both, or neither.

Memory-cell *contents* (the v2 `[cells.X]` section) are planned but not
yet implemented; see bead `mforth-0s6`. The current loader does not
recognise `[cells.X]`; v2 will add an explicit `--mem=<cell-name>` CLI
flag plus a `[cells.<name>] init = [...]` shape.

## `[links.<mforth-name>]`

The left side of `=` in the section header is the **stable mforth-name**
— the identifier the `.fs` source references (e.g. `display` in
`display PRINT-FLUSH`). The right-side keys bind it to a concrete
in-game block.

### `type` (required)

One of `"message"`, `"memory-cell"`, `"switch"`, `"core"`, `"generic"`.

| Type           | What it models                                                |
| -------------- | ------------------------------------------------------------- |
| `message`      | A message block; receives `print` / `printflush`.             |
| `memory-cell`  | A memory cell or bank; `read` / `write` addressable storage.  |
| `switch`       | A switch block with a boolean `@enabled` sensor.              |
| `core`         | A core (vault / nucleus); item / liquid sensors.              |
| `generic`      | Anything else — sensors work; type-specific keys do not.      |

### Mode A — `target = "<in-game-name>"` (recommended)

Names the block by the label it shows up under in Mindustry's link
inspector. Stable across destroy/rebuild as long as the in-game label
is preserved. This is the mode tutorials use.

```toml
[links.display]
type   = "message"
target = "message1"
```

### Mode B — `index = N` (opt-in)

Addresses the link by its processor-slot number — the order the
processor's `getlink` instruction returns it in. Stable only when
re-link order is preserved across destroy/rebuild, which is fragile.
Surfaces in the generated mlog as a `getlink` prologue.

```toml
[links.slot0]
type  = "message"
index = 0
```

`target` and `index` are mutually exclusive. **Exactly one is
required** — both is an error, neither is an error.

### Type-specific optional keys

- `size = N` — capacity hint for `memory-cell` links. Integer.
- `enabled = bool` — initial state for `switch` links.

Both are accepted on any link type (the loader does not enforce
type-pairing), but only meaningful for the type they model. Future
schema versions may tighten this.

### Seeded sensor readings — `sensors` (optional)

By default a freshly-built block reads `0` for every property, so
`<block> @prop SENSOR` returns `0.0`. The optional `sensors` inline table
seeds initial readings, so `SENSOR` returns real data — which is what
lets a host-REPL (or `mforth check`) test exercise a controller against
the case it cares about, not just the empty baseline.

```toml
[links.vault1]
type    = "core"
target  = "vault1"
sensors = { "@copper" = 240, "@totalItems" = 80, "@itemCapacity" = 100 }
```

- **Keys** are the same SENSOR-readable `@`-property names the `.fs`
  source passes to `SENSOR`: the sensor properties (`@totalItems`,
  `@itemCapacity`, `@health`, …), item content handles (`@copper`,
  `@graphite`, …), and liquid content handles (`@water`, `@slag`, …).
  See the [dictionary reference](./dictionary.md) for the full readable
  set. A key outside that set is an error.
- **Values** are numbers (int or float; booleans are rejected). They are
  stored as floats.
- An unseeded property keeps its `0.0` default.

The seeded value is read **identically** by the host `SENSOR` primitive
and by the compiled mlog `sensor` instruction — both back ends consult
the one `MockWorld` that `build_world` seeds from this table — so the
REPL ↔ mlog equivalence property holds for sense-and-decide programs.

## `[clock]`

Configures the simulated logic-processor cadence the host REPL and
in-repo mlog interpreter advance at. Both keys are optional; defaults
shown.

| Key        | Type | Default | Values                                  |
| ---------- | ---- | ------- | --------------------------------------- |
| `ipt`      | int  | `8`     | `2` (micro), `8` (logic), `25` (hyper)  |
| `realtime` | bool | `false` | If `true`, `wait` advances wall-clock.  |

In tests `realtime = false` lets `wait` skip ahead instantly so
equivalence fixtures stay fast. In `--serve` mode, set `true` to make
the web visualizer match in-game pacing.

```toml
[clock]
ipt      = 25
realtime = true
```

## Error catalogue

Every condition below raises `mforth.backend.sidecar.SidecarError` with
the exact message shown. Source paths are prefixed automatically by
`load_sidecar`.

### Link declares both `target` and `index`

```toml
[links.x]
type   = "message"
target = "message1"
index  = 0
```

> `[links.x] cannot specify both 'target' and 'index' — exactly one is required`

### Link declares neither `target` nor `index`

```toml
[links.x]
type = "message"
```

> `[links.x] requires exactly one of 'target' or 'index' (neither was given)`

### Link missing `type`

```toml
[links.x]
target = "message1"
```

> `[links.x] missing 'type'`

### Unknown link type

```toml
[links.x]
type   = "conveyor"
target = "conveyor1"
```

> `[links.x] unknown type 'conveyor' (valid: ['core', 'generic', 'memory-cell', 'message', 'switch'])`

### `target` is not a string

```toml
[links.x]
type   = "message"
target = 42
```

> `[links.x].target must be a string`

### `index` is not an integer

```toml
[links.x]
type  = "memory-cell"
index = "two"
```

> `[links.x].index must be an integer`

### `size` is not an integer

```toml
[links.x]
type   = "memory-cell"
target = "cell1"
size   = "lots"
```

> `[links.x].size must be an integer`

### `enabled` is not a boolean

```toml
[links.x]
type    = "switch"
target  = "switch1"
enabled = "yes"
```

> `[links.x].enabled must be a boolean`

### `sensors` is not a table

```toml
[links.x]
type    = "core"
target  = "vault1"
sensors = 42
```

> `[links.x].sensors must be a table of `@property = value` entries`

### `sensors` key is not `@`-prefixed

```toml
[links.x]
type    = "core"
target  = "vault1"
sensors = { "copper" = 240 }
```

> `[links.x].sensors key 'copper' must be an `@`-prefixed property name (e.g. '@copper', '@totalItems')`

### `sensors` key is not a SENSOR-readable property

```toml
[links.x]
type    = "core"
target  = "vault1"
sensors = { "@notAProp" = 1 }
```

> `[links.x].sensors key '@notAProp' is not a known SENSOR-readable property — see the dictionary reference for the readable `@`-names (items, liquids, sensor stats)`

### `sensors` value is not a number

```toml
[links.x]
type    = "core"
target  = "vault1"
sensors = { "@copper" = "lots" }
```

> `[links.x].sensors['@copper'] must be a number (got 'lots')`

### `[links]` is not a table

```toml
links = "not a table"
```

> `[links] must be a table of tables`

### `[clock].ipt` not in `{2, 8, 25}`

```toml
[clock]
ipt = 99
```

> `[clock].ipt must be one of [2, 8, 25] (got 99; micro=2, logic=8, hyper=25)`

### `[clock].ipt` not an integer

```toml
[clock]
ipt = "fast"
```

> `[clock].ipt must be one of [2, 8, 25] (got 'fast'; micro=2, logic=8, hyper=25)`

### `[clock].realtime` not a boolean

```toml
[clock]
realtime = "yes"
```

> `[clock].realtime must be a boolean (got 'yes')`

### Malformed TOML

```toml
[unclosed table
```

> `TOML parse error: <tomllib diagnostic>`

### File missing

> `sidecar file not found: <path>`

## Public API

The loader lives in `mforth.backend.sidecar`. Stable surface:

| Symbol                                  | Shape                                                                                |
| --------------------------------------- | ------------------------------------------------------------------------------------ |
| `load_sidecar(path) -> WorldConfig`     | Read + parse a file; raises `SidecarError`.                                          |
| `parse_sidecar(data, source=...)`       | Validate an already-parsed TOML dict; same return + error contract.                  |
| `WorldConfig`                           | `links: list[LinkSpec]`, `clock: ClockConfig`.                                       |
| `LinkSpec`                              | `mforth_name, type, target, index, size, enabled, sensors` (frozen dataclass).       |
| `ClockConfig`                           | `ipt: int = 8`, `realtime: bool = False` (frozen dataclass).                         |
| `SidecarError`                          | Raised on every failure path above; `.message` and `.source` attributes.             |

## See also

- [Why mforth](../explanation/why-mforth.md) — the REPL ↔ mlog
  equivalence rule the sidecar serves.
- [Forth, the mental model](../explanation/forth-mental-model.md) —
  why `VARIABLE foo` compiles to a bare mlog variable, not a cell
  binding declared in the sidecar.
- The upcoming `pin-sidecar-link-by-index` how-to — recipe + tradeoffs
  for the opt-in `index` mode (bead `mforth-zmi`).
