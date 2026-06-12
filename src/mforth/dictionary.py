"""mforth dictionary + word-resolution pass.

The dictionary maps word names (case-insensitive) to one of three entry
kinds:

* `BuiltinWord` — language primitives (stack ops, arithmetic, comparison,
  logical, IO, memory) and Mindustry primitives (PRINT, PRINTFLUSH, WAIT,
  SENSOR, GETLINK). Built-ins carry a stack effect (arity-in, arity-out),
  a one-line doc string for LSP hover, and a tag classifying the family.
* `Definition` — user `: name ... ;` definitions, registered from the
  parser's AST. Forth semantics: a later definition replaces an earlier
  one of the same name.
* `UserVariable` — names introduced via `VARIABLE <name>`. Pre-scanned
  before resolution so forward references work.

`resolve(program)` walks the AST once, asserts every `WordCall.name` can
be looked up, and returns the populated dictionary. Unresolved names
raise `UnresolvedWordError(name, src_loc)` so the LSP and CLI can point
the user at the exact source location.

Out of scope for this bead: stack-effect inference for user definitions
(bead mforth-10t.7), and synthesising `VarRef` nodes from VARIABLE/@/!
patterns (deferred — the parser's `VarRef` export is reserved for that
work).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

from mforth.parse import (
    Begin,
    Definition,
    DoLoop,
    IfThen,
    LitInt,
    LitStr,
    Program,
    SrcLoc,
    WordCall,
)


# ---------------------------------------------------------------------------
# Entry types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StackEffect:
    in_arity: int
    out_arity: int


@dataclass(frozen=True)
class BuiltinWord:
    name: str
    stack_effect: StackEffect
    doc: str
    tag: str  # 'arith' | 'stack' | 'control' | 'mindustry' | 'var' | 'io'


@dataclass(frozen=True)
class UserVariable:
    name: str
    src_loc: SrcLoc
    tag: str = "var"


DictEntry = Union[BuiltinWord, Definition, UserVariable]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class UnresolvedWordError(Exception):
    """Raised when a WordCall references a name not in the dictionary."""

    def __init__(self, name: str, src_loc: SrcLoc) -> None:
        super().__init__(
            f"{src_loc.file}:{src_loc.line}:{src_loc.col}: unresolved word '{name}'"
        )
        self.name = name
        self.src_loc = src_loc


# ---------------------------------------------------------------------------
# Dictionary
# ---------------------------------------------------------------------------


class Dictionary:
    """Case-insensitive mapping from word name → dictionary entry."""

    def __init__(self) -> None:
        self._entries: dict[str, DictEntry] = {}

    def add_builtin(self, word: BuiltinWord) -> None:
        self._entries[word.name.lower()] = word

    def add_definition(self, defn: Definition) -> None:
        self._entries[defn.name.lower()] = defn

    def add_variable(self, var: UserVariable) -> None:
        self._entries[var.name.lower()] = var

    def lookup(self, name: str) -> Optional[DictEntry]:
        return self._entries.get(name.lower())

    def __contains__(self, name: str) -> bool:
        return name.lower() in self._entries

    def __len__(self) -> int:
        return len(self._entries)


# ---------------------------------------------------------------------------
# Built-in registry
# ---------------------------------------------------------------------------


_BUILTINS: list[BuiltinWord] = [
    # Stack ops
    BuiltinWord("DUP", StackEffect(1, 2), "duplicate top of stack", "stack"),
    BuiltinWord("DROP", StackEffect(1, 0), "discard top of stack", "stack"),
    BuiltinWord("SWAP", StackEffect(2, 2), "swap top two items", "stack"),
    BuiltinWord("OVER", StackEffect(2, 3), "copy second item to top", "stack"),
    BuiltinWord("ROT", StackEffect(3, 3), "rotate top three: a b c -> b c a", "stack"),
    BuiltinWord("NIP", StackEffect(2, 1), "remove second item", "stack"),
    BuiltinWord("TUCK", StackEffect(2, 3), "copy top under second: a b -> b a b", "stack"),
    # Arithmetic
    BuiltinWord("+", StackEffect(2, 1), "add", "arith"),
    BuiltinWord("-", StackEffect(2, 1), "subtract", "arith"),
    BuiltinWord("*", StackEffect(2, 1), "multiply", "arith"),
    BuiltinWord("/", StackEffect(2, 1), "divide", "arith"),
    BuiltinWord("MOD", StackEffect(2, 1), "modulo", "arith"),
    # Comparison
    BuiltinWord("=", StackEffect(2, 1), "equal", "arith"),
    BuiltinWord("<>", StackEffect(2, 1), "not equal", "arith"),
    BuiltinWord("<", StackEffect(2, 1), "less than", "arith"),
    BuiltinWord(">", StackEffect(2, 1), "greater than", "arith"),
    BuiltinWord("<=", StackEffect(2, 1), "less than or equal", "arith"),
    BuiltinWord(">=", StackEffect(2, 1), "greater than or equal", "arith"),
    # Logical
    BuiltinWord("AND", StackEffect(2, 1), "logical and", "arith"),
    BuiltinWord("OR", StackEffect(2, 1), "logical or", "arith"),
    BuiltinWord("NOT", StackEffect(1, 1), "logical not", "arith"),
    # IO
    BuiltinWord(".", StackEffect(1, 0), "print top of stack", "io"),
    # Memory / variables
    BuiltinWord("@", StackEffect(1, 1), "fetch variable value: ( addr -- value )", "var"),
    BuiltinWord("!", StackEffect(2, 0), "store value into variable: ( value addr -- )", "var"),
    BuiltinWord(
        "VARIABLE",
        StackEffect(0, 0),
        "declare a variable: VARIABLE <name>",
        "var",
    ),
    # Mindustry primitives
    BuiltinWord("PRINT", StackEffect(1, 0), "queue value to print buffer", "mindustry"),
    BuiltinWord(
        "PRINTFLUSH",
        StackEffect(1, 0),
        "flush print buffer to a message block: ( block -- )",
        "mindustry",
    ),
    BuiltinWord("WAIT", StackEffect(1, 0), "pause execution for N seconds", "mindustry"),
    BuiltinWord(
        "SENSOR",
        StackEffect(2, 1),
        "read block property: ( block prop -- value )",
        "mindustry",
    ),
    BuiltinWord(
        "GETLINK",
        StackEffect(1, 1),
        "retrieve i-th linked block: ( i -- block )",
        "mindustry",
    ),
    # CONTROL block-instructions (bead mforth-cto). Per-sub-command words
    # so each has a static stack effect — see drawer
    # `drawer_mforth_findings_743016c030479990ba93e3e3` for the v1-scope
    # correction (UCONTROL stays v2; CONTROL is v1 block-side only).
    BuiltinWord(
        "CONTROL-ENABLED",
        StackEffect(2, 0),
        "enable/disable a block: ( block flag -- )",
        "mindustry-control",
    ),
    BuiltinWord(
        "CONTROL-CONFIG",
        StackEffect(2, 0),
        "configure a block (e.g. sorter target): ( block value -- )",
        "mindustry-control",
    ),
    BuiltinWord(
        "CONTROL-SHOOT",
        StackEffect(4, 0),
        "aim+fire a turret at coordinate: ( block x y shoot -- )",
        "mindustry-control",
    ),
    BuiltinWord(
        "CONTROL-SHOOTP",
        StackEffect(3, 0),
        "aim+fire a turret at a unit: ( block unit shoot -- )",
        "mindustry-control",
    ),
    BuiltinWord(
        "CONTROL-COLOR",
        StackEffect(4, 0),
        "set illuminator color: ( block r g b -- )",
        "mindustry-control",
    ),
    # NULL literal (bead mforth-l8z). Pushes the mlog null sentinel
    # onto the data stack. Composes with CONTROL-CONFIG to stop a
    # sorter/unloader (USR Sorter Picker pattern) and with PRINT to
    # render the literal text "null".
    BuiltinWord(
        "NULL",
        StackEffect(0, 1),
        "push the mlog null sentinel: ( -- null )",
        "mindustry",
    ),
    # DO/LOOP counters. Valid only inside a DO/LOOP body, but the dictionary
    # treats them as plain (0, 1) pushes for resolution + stack arithmetic.
    BuiltinWord("I", StackEffect(0, 1), "push current DO/LOOP counter", "control"),
    BuiltinWord("J", StackEffect(0, 1), "push outer DO/LOOP counter", "control"),
]


def standard_dictionary() -> Dictionary:
    """Build a fresh `Dictionary` populated with all v1 built-ins.

    Also registers the 171-entry Mindustry @-identifier surface (bead
    mforth-eaz; count reconciled in mforth-73h): 170 registry entries plus
    the 1 `@tick`/`@ticks` alias. Covers magic vars, content names
    (items/liquids/units/blocks), and sensor properties. Source of truth
    for the inventory + naming convention: MemPalace drawer
    `drawer_mforth_references_619a7ee4f1464f4bc65ef91a`.
    """
    d = Dictionary()
    for w in _BUILTINS:
        d.add_builtin(w)
    for w in _MINDUSTRY_IDENTIFIERS:
        d.add_builtin(w)
    # Aliases: register the alias name pointing at the SAME object as
    # the canonical entry so emit/host/LSP all see identical behavior.
    for alias, canonical in _MINDUSTRY_ALIASES.items():
        primary = d.lookup(canonical)
        if isinstance(primary, BuiltinWord):
            d._entries[alias.lower()] = primary  # noqa: SLF001
    return d


# ---------------------------------------------------------------------------
# Mindustry @-identifier surface (bead mforth-eaz; counts reconciled in
# mforth-73h — the figures below are the live registry sizes, asserted by
# tests/unit/test_dictionary_counts.py; a category that grows forces a
# matching edit here)
#
# 171 entries compiled from Anuken/Mindustry source + sbxte/MLogWiki:
# 170 registry entries (the six builders below) + 1 alias (@ticks→@tick).
# See MemPalace drawer `drawer_mforth_references_619a7ee4f1464f4bc65ef91a`
# for the authoritative inventory + per-name doc strings + the
# camelCase→kebab-case naming convention. Categories:
#
#   §1 (a–h)  Magic @-variables          — 29 entries, tag "mindustry-magic"
#   §2a       Items                      — 22 entries, tag "mindustry-item"
#   §2b       Liquids                    — 11 entries, tag "mindustry-liquid"
#   §2c       Units (essential subset)   — 22 entries, tag "mindustry-unit"
#   §2d       Blocks (essential subset)  — 15 entries, tag "mindustry-block"
#   §3a–§3g   Sensor properties          — 71 entries, tag "mindustry-sensor-prop"
#
#   registry subtotal = 29+22+11+22+15+71 = 170; + 1 alias = 171 total.
#
# Each entry has stack effect (0, 1) — they all push a single value/tag.
# Magic vars push a numeric stub or handle (host primitive returns
# deterministic values for equivalence-testing; the mlog interpreter
# pre-seeds the same stubs in MlogInterpreter.__init__).
# Content names + sensor props push their bare `@name` string as an
# opaque tag (matching the .12 block-handle "bare string" convention).
#
# v1 deferral notes (see research drawer §"v1 → v2 deferral summary"):
#   - Privileged / world-processor vars (@wait, @client*) — DEFERRED v2.
#   - Remaining ~40 unit types + ~225 block types — DEFERRED v2.
#   - Settable LAccess (@enabled, @shoot, @configure, @color) — DEFERRED
#     until a CONTROL / UCONTROL primitive ships.
#   - Unicode `π` alias — DEFERRED (user types @pi).
# ---------------------------------------------------------------------------


def _magic_entries() -> list[BuiltinWord]:
    # (name, doc) pairs — see research drawer §1.
    rows = [
        # §1a processor-specific
        ("@counter", "instruction-pointer index (zero-indexed; writable for jumps)"),
        ("@this", "the processor block itself, as a building handle"),
        ("@thisx", "processor's world x coordinate"),
        ("@thisy", "processor's world y coordinate"),
        ("@ipt", "instructions per tick — micro=2, logic=8, hyper=25"),
        ("@links", "number of buildings linked to this processor (1-indexed count)"),
        ("@unit", "the currently bound unit (set by ubind); null if no bind"),
        # §1b time
        ("@time", "microseconds since the save was loaded (state.tick * 1000/60)"),
        ("@tick", "ticks since save loaded (raw state.tick)"),
        ("@second", "seconds elapsed since save loaded (state.tick / 60)"),
        ("@minute", "minutes elapsed since save loaded (state.tick / 3600)"),
        ("@waveNumber", "current wave number"),
        ("@waveTime", "seconds remaining in current wave"),
        # §1c map
        ("@mapw", "map width in tiles"),
        ("@maph", "map height in tiles"),
        # §1d math constants
        ("@pi", "π (Mathf.PI)"),
        ("@e", "Euler's number (Mathf.E)"),
        ("@degToRad", "degree → radian conversion factor"),
        ("@radToDeg", "radian → degree conversion factor"),
        # §1e network state
        ("@server", "1 if running on server, else 0"),
        ("@client", "1 if running on client, else 0"),
        # §1f tile-type sentinels
        ("@air", "sentinel: tile is air (buildable/walkable)"),
        ("@solid", "sentinel: tile is solid (wall/terrain, not buildable)"),
        # §1g control constants
        ("@ctrlProcessor", "control source constant: processor (= 1)"),
        ("@ctrlPlayer", "control source constant: player (= 2)"),
        ("@ctrlCommand", "control source constant: command center (= 3)"),
        # §1h command center configs
        ("@commandAttack", "command center config: attack"),
        ("@commandRally", "command center config: rally"),
        ("@commandIdle", "command center config: idle"),
    ]
    return [
        BuiltinWord(name, StackEffect(0, 1), doc, "mindustry-magic")
        for name, doc in rows
    ]


def _item_entries() -> list[BuiltinWord]:
    rows = [
        ("@copper", "item: copper"),
        ("@lead", "item: lead"),
        ("@metaglass", "item: metaglass"),
        ("@graphite", "item: graphite"),
        ("@sand", "item: sand"),
        ("@coal", "item: coal"),
        ("@titanium", "item: titanium"),
        ("@thorium", "item: thorium"),
        ("@scrap", "item: scrap"),
        ("@silicon", "item: silicon"),
        ("@plastanium", "item: plastanium"),
        ("@phase-fabric", "item: phase fabric (Java: phaseFabric)"),
        ("@surge-alloy", "item: surge alloy (Java: surgeAlloy)"),
        ("@spore-pod", "item: spore pod (Java: sporePod)"),
        ("@blast-compound", "item: blast compound (Java: blastCompound)"),
        ("@pyratite", "item: pyratite"),
        ("@beryllium", "item: beryllium (Erekir)"),
        ("@tungsten", "item: tungsten (Erekir)"),
        ("@oxide", "item: oxide (Erekir)"),
        ("@carbide", "item: carbide (Erekir)"),
        ("@fissile-matter", "item: fissile matter (Erekir; Java: fissileMatter)"),
        ("@dormant-cyst", "item: dormant cyst (Erekir; Java: dormantCyst)"),
    ]
    return [
        BuiltinWord(name, StackEffect(0, 1), doc, "mindustry-item")
        for name, doc in rows
    ]


def _liquid_entries() -> list[BuiltinWord]:
    rows = [
        ("@water", "liquid: water"),
        ("@slag", "liquid: slag"),
        ("@oil", "liquid: oil"),
        ("@cryofluid", "liquid: cryofluid"),
        ("@neoplasm", "liquid: neoplasm"),
        ("@arkycite", "liquid: arkycite"),
        ("@gallium", "liquid: gallium"),
        ("@ozone", "liquid: ozone"),
        ("@hydrogen", "liquid: hydrogen"),
        ("@nitrogen", "liquid: nitrogen"),
        ("@cyanogen", "liquid: cyanogen"),
    ]
    return [
        BuiltinWord(name, StackEffect(0, 1), doc, "mindustry-liquid")
        for name, doc in rows
    ]


def _unit_entries() -> list[BuiltinWord]:
    # v1 essential subset (22) per research drawer §2c. The remaining
    # ~40 Erekir + naval + ground-legs units are deferred to v2.
    rows = [
        ("@dagger", "unit: dagger (Serpulo ground T1)"),
        ("@mace", "unit: mace (Serpulo ground T2)"),
        ("@fortress", "unit: fortress (Serpulo ground T3)"),
        ("@scepter", "unit: scepter (Serpulo ground T4)"),
        ("@reign", "unit: reign (Serpulo ground T5)"),
        ("@nova", "unit: nova (Serpulo support T1)"),
        ("@pulsar", "unit: pulsar (Serpulo support T2)"),
        ("@quasar", "unit: quasar (Serpulo support T3)"),
        ("@vela", "unit: vela (Serpulo support T4)"),
        ("@flare", "unit: flare (Serpulo air T1)"),
        ("@horizon", "unit: horizon (Serpulo air T2)"),
        ("@zenith", "unit: zenith (Serpulo air T3)"),
        ("@antumbra", "unit: antumbra (Serpulo air T4)"),
        ("@eclipse", "unit: eclipse (Serpulo air T5)"),
        ("@mono", "unit: mono (drone T1)"),
        ("@poly", "unit: poly (drone T2)"),
        ("@mega", "unit: mega (drone T3)"),
        ("@quad", "unit: quad (drone T4)"),
        ("@oct", "unit: oct (drone T5)"),
        ("@alpha", "unit: alpha (player-controllable)"),
        ("@beta", "unit: beta (player-controllable)"),
        ("@gamma", "unit: gamma (player-controllable)"),
    ]
    return [
        BuiltinWord(name, StackEffect(0, 1), doc, "mindustry-unit")
        for name, doc in rows
    ]


def _block_entries() -> list[BuiltinWord]:
    # v1 essential subset (15) per research drawer §2d.
    rows = [
        ("@micro-processor", "block: micro processor (2 ipt)"),
        ("@logic-processor", "block: logic processor (8 ipt)"),
        ("@hyper-processor", "block: hyper processor (25 ipt)"),
        ("@world-processor", "block: world processor (privileged)"),
        ("@message", "block: message (target for printflush)"),
        ("@switch", "block: switch (sensor target — enabled)"),
        ("@memory-cell", "block: memory cell (64 doubles)"),
        ("@memory-bank", "block: memory bank (512 doubles)"),
        ("@logic-display", "block: logic display (drawing target)"),
        ("@large-logic-display", "block: large logic display"),
        ("@core-shard", "block: core (shard)"),
        ("@core-foundation", "block: core (foundation)"),
        ("@core-nucleus", "block: core (nucleus)"),
        ("@container", "block: container (storage)"),
        ("@vault", "block: vault (storage)"),
    ]
    return [
        BuiltinWord(name, StackEffect(0, 1), doc, "mindustry-block")
        for name, doc in rows
    ]


def _sensor_prop_entries() -> list[BuiltinWord]:
    # Sensor properties from LAccess.java — senseable subset (§3a–§3g).
    # Note: @solid is registered in §1f as a tile-type sentinel; we do
    # NOT re-register it here (the dictionary holds one entry per name).
    rows = [
        # §3a inventory/resource
        ("@totalItems", "sensor: total item count in block"),
        ("@firstItem", "sensor: first/dominant item (content handle)"),
        ("@totalLiquids", "sensor: total liquid amount in block"),
        ("@totalPower", "sensor: total power stored"),
        ("@itemCapacity", "sensor: max items the block can hold"),
        ("@liquidCapacity", "sensor: max liquid the block can hold"),
        ("@powerCapacity", "sensor: max power the block can hold"),
        ("@powerNetStored", "sensor: power network total stored"),
        ("@powerNetCapacity", "sensor: power network total capacity"),
        ("@powerNetIn", "sensor: power network input rate"),
        ("@powerNetOut", "sensor: power network output rate"),
        ("@ammo", "sensor: current ammo count (turrets)"),
        ("@ammoCapacity", "sensor: max ammo"),
        ("@currentAmmoType", "sensor: current ammo type (content handle)"),
        ("@memoryCapacity", "sensor: memory cell/bank capacity"),
        # §3b entity state
        ("@health", "sensor: current hit points"),
        ("@maxHealth", "sensor: max hit points"),
        ("@heat", "sensor: heat (reactors)"),
        ("@shield", "sensor: shield amount"),
        ("@armor", "sensor: armor stat"),
        ("@efficiency", "sensor: production efficiency 0..1"),
        ("@progress", "sensor: production progress 0..1"),
        ("@timescale", "sensor: time multiplier from overdrive"),
        ("@rotation", "sensor: rotation in degrees"),
        ("@x", "sensor: world x coordinate"),
        ("@y", "sensor: world y coordinate"),
        ("@velocityX", "sensor: velocity x (units)"),
        ("@velocityY", "sensor: velocity y (units)"),
        ("@shootX", "sensor: aim point x (turrets/units)"),
        ("@shootY", "sensor: aim point y"),
        ("@cameraX", "sensor: player camera x"),
        ("@cameraY", "sensor: player camera y"),
        ("@cameraWidth", "sensor: player viewport width"),
        ("@cameraHeight", "sensor: player viewport height"),
        ("@displayWidth", "sensor: display block pixel width"),
        ("@displayHeight", "sensor: display block pixel height"),
        ("@size", "sensor: block size in tiles (1/2/3/4)"),
        ("@dead", "sensor: 1 if entity destroyed"),
        ("@range", "sensor: effective range (turrets/units)"),
        ("@shooting", "sensor: 1 if currently shooting"),
        ("@boosting", "sensor: 1 if unit is boosting"),
        # §3c mining / building / movement
        ("@mineX", "sensor: mine target x"),
        ("@mineY", "sensor: mine target y"),
        ("@mining", "sensor: 1 if mining"),
        ("@buildX", "sensor: build target x"),
        ("@buildY", "sensor: build target y"),
        ("@building", "sensor: 1 if building"),
        ("@breaking", "sensor: 1 if deconstructing"),
        ("@pingX", "sensor: ping marker x"),
        ("@pingY", "sensor: ping marker y"),
        ("@pingText", "sensor: ping marker text"),
        ("@speed", "sensor: movement speed"),
        # §3d identity / classification
        ("@team", "sensor: entity's team handle"),
        ("@type", "sensor: UnitType or block type handle"),
        ("@flag", "sensor: user-set flag value (units)"),
        ("@controlled", "sensor: control source (matches @ctrl*)"),
        ("@controller", "sensor: the controlling entity"),
        ("@name", "sensor: player name (units only)"),
        ("@id", "sensor: entity id"),
        # §3e payload (Erekir)
        ("@payloadCount", "sensor: number of payloads held"),
        ("@payloadType", "sensor: type of held payload"),
        ("@totalPayload", "sensor: total payload mass"),
        ("@payloadCapacity", "sensor: max payload capacity"),
        ("@maxUnits", "sensor: max simultaneously controllable units"),
        # §3f ammo/projectile
        ("@bufferSize", "sensor: mass driver / link buffer size"),
        ("@operations", "sensor: buffered operations count"),
        ("@bulletLifetime", "sensor: bullet lifetime stat"),
        ("@bulletTime", "sensor: bullet age"),
        # §3g block-specific config
        ("@selectedBlock", "sensor: currently selected block"),
        ("@selectedRotation", "sensor: selected rotation for placement"),
        ("@config", "sensor: block's current config value"),
    ]
    return [
        BuiltinWord(name, StackEffect(0, 1), doc, "mindustry-sensor-prop")
        for name, doc in rows
    ]


_MINDUSTRY_IDENTIFIERS: list[BuiltinWord] = (
    _magic_entries()
    + _item_entries()
    + _liquid_entries()
    + _unit_entries()
    + _block_entries()
    + _sensor_prop_entries()
)


# Aliases: alias_name → canonical_name (both lowercased; canonical entry
# is looked up after the main registration loop and the alias key is
# pointed at the same object).
#
# Wiki/source disagreement per research drawer Notes §1: GlobalVars.java
# registers `@tick` (singular); MLogWiki uses `@ticks` (plural). We accept
# both; the canonical entry stays `@tick`.
_MINDUSTRY_ALIASES: dict[str, str] = {
    "@ticks": "@tick",
}


# Deterministic stub values for magic vars — consumed by both
# backend.primitives (host REPL) and mlog_interp (mlog backend) so the
# REPL ↔ mlog equivalence property holds even though Mindustry's
# runtime values are inherently non-deterministic.
#
# Defaults chosen to be obviously-stub (0 / 0.0 / known constants) so a
# test that asserts on a magic-var value is auditable. @ipt defaults to
# the logic processor's 8 (the middle-tier; see mlog reference drawer
# §"@ipt"). Math constants come from `math` so they match mlog exactly.
import math as _math

MINDUSTRY_MAGIC_STUBS: dict[str, object] = {
    # §1a processor-specific
    "@counter": 0,
    "@this": "@this",       # building handle — bare string per .12
    "@thisx": 0.0,
    "@thisy": 0.0,
    "@ipt": 8,
    "@links": 0,
    "@unit": None,          # mlog null
    # §1b time
    "@time": 0.0,
    "@tick": 0,
    "@second": 0.0,
    "@minute": 0.0,
    "@waveNumber": 1,
    "@waveTime": 0.0,
    # §1c map
    "@mapw": 40,
    "@maph": 40,
    # §1d math constants
    "@pi": _math.pi,
    "@e": _math.e,
    "@degToRad": _math.pi / 180.0,
    "@radToDeg": 180.0 / _math.pi,
    # §1e network state
    "@server": 0,
    "@client": 1,
    # §1f tile-type sentinels (bare tag — comparable to sensor results)
    "@air": "@air",
    "@solid": "@solid",
    # §1g control constants (per GlobalVars.java — concrete ints)
    "@ctrlProcessor": 1,
    "@ctrlPlayer": 2,
    "@ctrlCommand": 3,
    # §1h command center configs (bare tags)
    "@commandAttack": "@commandAttack",
    "@commandRally": "@commandRally",
    "@commandIdle": "@commandIdle",
}


# ---------------------------------------------------------------------------
# Resolution pass
# ---------------------------------------------------------------------------


def _walk_terms(terms: list, visitor) -> None:
    for t in terms:
        visitor(t)
        if isinstance(t, IfThen):
            _walk_terms(t.then_body, visitor)
            _walk_terms(t.else_body, visitor)
        elif isinstance(t, Begin):
            _walk_terms(t.body, visitor)
            _walk_terms(t.cond_body, visitor)
        elif isinstance(t, DoLoop):
            _walk_terms(t.body, visitor)


def _collect_variable_declarations(program: Program) -> list[UserVariable]:
    """Pre-scan: every `WordCall("VARIABLE")` followed by a `WordCall(<name>)`
    declares <name>. Walks main and all definition bodies (Forth has no
    lexical scoping, so a VARIABLE inside a definition is still global).
    """
    declarations: list[UserVariable] = []

    def scan_list(terms: list) -> None:
        i = 0
        while i < len(terms):
            t = terms[i]
            if (
                isinstance(t, WordCall)
                and t.name.upper() == "VARIABLE"
                and i + 1 < len(terms)
                and isinstance(terms[i + 1], WordCall)
            ):
                nxt = terms[i + 1]
                declarations.append(UserVariable(name=nxt.name, src_loc=nxt.src_loc))
            # Recurse into nested control-flow bodies
            if isinstance(t, IfThen):
                scan_list(t.then_body)
                scan_list(t.else_body)
            elif isinstance(t, Begin):
                scan_list(t.body)
                scan_list(t.cond_body)
            elif isinstance(t, DoLoop):
                scan_list(t.body)
            i += 1

    scan_list(program.main)
    for d in program.definitions:
        scan_list(d.body)
    return declarations


def resolve(program: Program, dictionary: Optional[Dictionary] = None) -> Dictionary:
    """Resolve every `WordCall.name` in `program`.

    * Adds the program's `Definition`s to the dictionary (in source order;
      a later `:` redefines an earlier one of the same name).
    * Adds VARIABLE-declared names as `UserVariable` entries.
    * Walks every `WordCall` (in main and definition bodies, including
      nested control-flow) and verifies each name is in the dictionary.

    Raises `UnresolvedWordError` on the first miss. The AST itself is not
    modified. Returns the (now-populated) dictionary so callers can pass
    it to the next stage.
    """
    d = dictionary if dictionary is not None else standard_dictionary()

    for defn in program.definitions:
        d.add_definition(defn)

    for var in _collect_variable_declarations(program):
        d.add_variable(var)

    def check(t) -> None:
        if isinstance(t, WordCall) and t.name not in d:
            raise UnresolvedWordError(t.name, t.src_loc)

    _walk_terms(program.main, check)
    for defn in program.definitions:
        _walk_terms(defn.body, check)

    return d


__all__ = [
    "BuiltinWord",
    "Dictionary",
    "StackEffect",
    "UnresolvedWordError",
    "UserVariable",
    "resolve",
    "standard_dictionary",
]
