"""Unit tests for Mindustry @-identifier dictionary entries (bead mforth-eaz).

Wires 154 Mindustry magic vars + content names + sensor properties into
the v1 dictionary. Each entry is a BuiltinWord with mlog kebab-case +
@-prefix name (e.g. @phase-fabric, NOT phaseFabric), a known stack
effect, a one-line doc string, and a category tag.

Authoritative inventory: MemPalace drawer
`drawer_mforth_references_619a7ee4f1464f4bc65ef91a` (the @-identifier
research drawer compiled from Anuken/Mindustry source + sbxte/MLogWiki).

Naming convention (LOAD-BEARING): Java source uses camelCase
(`phaseFabric`); mlog source uses kebab-case with `@` prefix
(`@phase-fabric`). The dictionary uses the **mlog form** as canonical.
The naming-convention regression test pins this against the most common
miswrites.
"""

from __future__ import annotations

import math

import pytest

from mforth.backend.host import Executor
from mforth.backend.primitives import register_all
from mforth.backend.world import MockWorld
from mforth.dictionary import BuiltinWord, StackEffect, standard_dictionary


# ---------------------------------------------------------------------------
# Inventory: every category, every entry present
# ---------------------------------------------------------------------------


# Magic vars (§1a-1h of the research drawer)
MAGIC_VARS_PROCESSOR = [
    "@counter", "@this", "@thisx", "@thisy", "@ipt", "@links", "@unit",
]
MAGIC_VARS_TIME = [
    "@time", "@tick", "@second", "@minute", "@waveNumber", "@waveTime",
]
MAGIC_VARS_MAP = ["@mapw", "@maph"]
MAGIC_VARS_MATH = ["@pi", "@e", "@degToRad", "@radToDeg"]
MAGIC_VARS_NET = ["@server", "@client"]
MAGIC_VARS_TILE_SENTINELS = ["@air", "@solid"]
MAGIC_VARS_CTRL = ["@ctrlProcessor", "@ctrlPlayer", "@ctrlCommand"]
MAGIC_VARS_CMD = ["@commandAttack", "@commandRally", "@commandIdle"]

ALL_MAGIC_VARS = (
    MAGIC_VARS_PROCESSOR
    + MAGIC_VARS_TIME
    + MAGIC_VARS_MAP
    + MAGIC_VARS_MATH
    + MAGIC_VARS_NET
    + MAGIC_VARS_TILE_SENTINELS
    + MAGIC_VARS_CTRL
    + MAGIC_VARS_CMD
)

# Items (§2a — 22)
ITEMS = [
    "@copper", "@lead", "@metaglass", "@graphite", "@sand", "@coal",
    "@titanium", "@thorium", "@scrap", "@silicon", "@plastanium",
    "@phase-fabric", "@surge-alloy", "@spore-pod", "@blast-compound",
    "@pyratite", "@beryllium", "@tungsten", "@oxide", "@carbide",
    "@fissile-matter", "@dormant-cyst",
]

# Liquids (§2b — 11)
LIQUIDS = [
    "@water", "@slag", "@oil", "@cryofluid", "@neoplasm", "@arkycite",
    "@gallium", "@ozone", "@hydrogen", "@nitrogen", "@cyanogen",
]

# Units (§2c essential subset — 22)
UNITS = [
    "@dagger", "@mace", "@fortress", "@scepter", "@reign",
    "@nova", "@pulsar", "@quasar", "@vela",
    "@flare", "@horizon", "@zenith", "@antumbra", "@eclipse",
    "@mono", "@poly", "@mega", "@quad", "@oct",
    "@alpha", "@beta", "@gamma",
]

# Blocks (§2d essential subset — 15)
BLOCKS = [
    "@micro-processor", "@logic-processor", "@hyper-processor",
    "@world-processor",
    "@message", "@switch", "@memory-cell", "@memory-bank",
    "@logic-display", "@large-logic-display",
    "@core-shard", "@core-foundation", "@core-nucleus",
    "@container", "@vault",
]

# Sensor properties (§3a–§3g senseable subset). Note: @solid and a
# couple of other names also appear in §1f as tile sentinels; the
# dictionary registers each unique name once — duplicates between
# categories are resolved by registering under the first category that
# claims them (sentinels in §1f for @solid, sensor-prop in §3 for the
# rest).
SENSOR_PROPS = [
    # §3a
    "@totalItems", "@firstItem", "@totalLiquids", "@totalPower",
    "@itemCapacity", "@liquidCapacity", "@powerCapacity",
    "@powerNetStored", "@powerNetCapacity", "@powerNetIn", "@powerNetOut",
    "@ammo", "@ammoCapacity", "@currentAmmoType", "@memoryCapacity",
    # §3b (omit @solid here — registered as a sentinel in §1f)
    "@health", "@maxHealth", "@heat", "@shield", "@armor",
    "@efficiency", "@progress", "@timescale", "@rotation",
    "@x", "@y", "@velocityX", "@velocityY", "@shootX", "@shootY",
    "@cameraX", "@cameraY", "@cameraWidth", "@cameraHeight",
    "@displayWidth", "@displayHeight", "@size", "@dead", "@range",
    "@shooting", "@boosting",
    # §3c
    "@mineX", "@mineY", "@mining",
    "@buildX", "@buildY", "@building", "@breaking",
    "@pingX", "@pingY", "@pingText", "@speed",
    # §3d
    "@team", "@type", "@flag", "@controlled", "@controller",
    "@name", "@id",
    # §3e
    "@payloadCount", "@payloadType", "@totalPayload", "@payloadCapacity",
    "@maxUnits",
    # §3f
    "@bufferSize", "@operations", "@bulletLifetime", "@bulletTime",
    # §3g
    "@selectedBlock", "@selectedRotation", "@config",
]


# ---------------------------------------------------------------------------
# Tests — every entry is registered with correct shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_MAGIC_VARS)
def test_magic_var_registered(name: str):
    d = standard_dictionary()
    e = d.lookup(name)
    assert isinstance(e, BuiltinWord), f"{name} not registered as BuiltinWord"
    assert e.stack_effect == StackEffect(0, 1)
    assert e.doc, f"{name} has empty doc"
    assert e.tag == "mindustry-magic"


@pytest.mark.parametrize("name", ITEMS)
def test_item_registered(name: str):
    d = standard_dictionary()
    e = d.lookup(name)
    assert isinstance(e, BuiltinWord)
    assert e.stack_effect == StackEffect(0, 1)
    assert e.doc
    assert e.tag == "mindustry-item"


@pytest.mark.parametrize("name", LIQUIDS)
def test_liquid_registered(name: str):
    d = standard_dictionary()
    e = d.lookup(name)
    assert isinstance(e, BuiltinWord)
    assert e.stack_effect == StackEffect(0, 1)
    assert e.doc
    assert e.tag == "mindustry-liquid"


@pytest.mark.parametrize("name", UNITS)
def test_unit_registered(name: str):
    d = standard_dictionary()
    e = d.lookup(name)
    assert isinstance(e, BuiltinWord)
    assert e.stack_effect == StackEffect(0, 1)
    assert e.doc
    assert e.tag == "mindustry-unit"


@pytest.mark.parametrize("name", BLOCKS)
def test_block_registered(name: str):
    d = standard_dictionary()
    e = d.lookup(name)
    assert isinstance(e, BuiltinWord)
    assert e.stack_effect == StackEffect(0, 1)
    assert e.doc
    assert e.tag == "mindustry-block"


@pytest.mark.parametrize("name", SENSOR_PROPS)
def test_sensor_prop_registered(name: str):
    d = standard_dictionary()
    e = d.lookup(name)
    assert isinstance(e, BuiltinWord)
    assert e.stack_effect == StackEffect(0, 1)
    assert e.doc
    assert e.tag == "mindustry-sensor-prop"


def test_total_entry_count():
    """Sanity: every category is fully wired, no silent skips."""
    d = standard_dictionary()
    counts = {
        "magic": sum(
            1 for n in ALL_MAGIC_VARS
            if isinstance(d.lookup(n), BuiltinWord)
        ),
        "item": sum(1 for n in ITEMS if isinstance(d.lookup(n), BuiltinWord)),
        "liquid": sum(1 for n in LIQUIDS if isinstance(d.lookup(n), BuiltinWord)),
        "unit": sum(1 for n in UNITS if isinstance(d.lookup(n), BuiltinWord)),
        "block": sum(1 for n in BLOCKS if isinstance(d.lookup(n), BuiltinWord)),
        "sensor-prop": sum(
            1 for n in SENSOR_PROPS if isinstance(d.lookup(n), BuiltinWord)
        ),
    }
    assert counts["magic"] == len(ALL_MAGIC_VARS), counts
    assert counts["item"] == 22, counts
    assert counts["liquid"] == 11, counts
    assert counts["unit"] == 22, counts
    assert counts["block"] == 15, counts
    assert counts["sensor-prop"] == len(SENSOR_PROPS), counts


# ---------------------------------------------------------------------------
# Alias mechanism: @ticks -> @tick
# ---------------------------------------------------------------------------


def test_tick_alias_ticks_resolves_to_same_entry():
    d = standard_dictionary()
    primary = d.lookup("@tick")
    alias = d.lookup("@ticks")
    assert isinstance(primary, BuiltinWord)
    assert isinstance(alias, BuiltinWord)
    # Aliases MUST point at the same object so emit + host + LSP all see
    # identical behavior (canonical name, canonical doc, canonical effect).
    assert alias is primary
    assert alias.name == "@tick"


# ---------------------------------------------------------------------------
# Naming convention regression: NO Java camelCase smuggled in
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "java_name",
    [
        "phaseFabric", "surgeAlloy", "sporePod", "blastCompound",
        "fissileMatter", "dormantCyst",
        "@phaseFabric", "@surgeAlloy", "@sporePod", "@blastCompound",
        "@fissileMatter", "@dormantCyst",
    ],
)
def test_java_camelcase_NOT_in_dictionary(java_name: str):
    """Naming convention guard: Java field names must NOT appear in the
    dictionary. The mlog kebab-case form (`@phase-fabric`, etc.) is the
    only registered form."""
    d = standard_dictionary()
    assert d.lookup(java_name) is None, (
        f"Java camelCase '{java_name}' leaked into the dictionary — "
        f"the canonical mlog form is the kebab-case + @-prefix variant "
        f"(see research drawer drawer_mforth_references_619a7ee4f1464f4bc65ef91a)"
    )


def test_kebab_case_variants_DO_appear():
    d = standard_dictionary()
    for kebab in [
        "@phase-fabric", "@surge-alloy", "@spore-pod",
        "@blast-compound", "@fissile-matter", "@dormant-cyst",
    ]:
        assert isinstance(d.lookup(kebab), BuiltinWord), (
            f"missing kebab-case form '{kebab}'"
        )


# ---------------------------------------------------------------------------
# Host primitive behavior — push the canonical value / tag
# ---------------------------------------------------------------------------


def _run_one(name: str) -> Executor:
    """Execute a one-word program and return the executor."""
    from mforth.dictionary import resolve
    from mforth.parse import parse
    from mforth.stackcheck import stackcheck

    ex = Executor(world=MockWorld())
    register_all(ex)
    program = parse(name, file="t.fs")
    d = resolve(program)
    sc = stackcheck(program, dictionary=d)
    ex.execute(sc)
    return ex


def test_host_content_name_pushes_bare_tag():
    """Content names push the bare `@name` string onto the data stack —
    matches the .12 "block-handle as bare string" convention."""
    ex = _run_one("@copper")
    assert ex.data_stack == ["@copper"]

    ex2 = _run_one("@phase-fabric")
    assert ex2.data_stack == ["@phase-fabric"]


def test_host_sensor_prop_pushes_bare_tag():
    ex = _run_one("@health")
    assert ex.data_stack == ["@health"]


def test_host_magic_var_pushes_deterministic_stub():
    """Magic vars push deterministic stubs (so REPL ↔ mlog equivalence
    holds even though Mindustry's runtime values are non-deterministic).

    Stubs MUST match the mlog interpreter's pre-seeded `@<name>` values.
    """
    assert _run_one("@time").data_stack == [0.0]
    assert _run_one("@tick").data_stack == [0]
    # logic-processor default (mlog reference drawer)
    assert _run_one("@ipt").data_stack == [8]
    assert _run_one("@pi").data_stack == [math.pi]
    assert _run_one("@e").data_stack == [math.e]


def test_host_alias_ticks_same_value_as_tick():
    assert _run_one("@ticks").data_stack == [0]


# ---------------------------------------------------------------------------
# mlog emit — slot-form for magic-var / content-name in non-lift contexts
# ---------------------------------------------------------------------------


def test_emit_magic_var_slot_form():
    """`@time` standalone emits `set s0 @time` (slot-form push)."""
    from mforth.backend.mlog.emit import emit
    from mforth.dictionary import resolve
    from mforth.parse import parse
    from mforth.stackcheck import stackcheck

    program = parse("@time", file="t.fs")
    d = resolve(program)
    sc = stackcheck(program, dictionary=d)
    instrs = emit(sc)
    assert instrs == [(None, "set", ("s0", "@time"))]


def test_emit_content_name_slot_form():
    """`@copper` standalone emits `set s0 @copper`."""
    from mforth.backend.mlog.emit import emit
    from mforth.dictionary import resolve
    from mforth.parse import parse
    from mforth.stackcheck import stackcheck

    program = parse("@copper", file="t.fs")
    d = resolve(program)
    sc = stackcheck(program, dictionary=d)
    instrs = emit(sc)
    assert instrs == [(None, "set", ("s0", "@copper"))]


def test_emit_sensor_lifts_content_name_operand():
    """The lift fast-path: `<block-uservar> @copper SENSOR` emits one
    `sensor s<i> <block> @copper` (no intervening `set s<i> @copper`)."""
    from mforth.backend.mlog.emit import emit
    from mforth.dictionary import resolve
    from mforth.parse import parse
    from mforth.stackcheck import stackcheck

    # Need a VARIABLE link for the block so the existing uservar-lift
    # arm handles the block side. Then our new content-name arm handles
    # the prop side.
    src = "VARIABLE reactor reactor @copper SENSOR"
    program = parse(src, file="t.fs")
    d = resolve(program)
    sc = stackcheck(program, dictionary=d)
    instrs = emit(sc)
    sensors = [i for i in instrs if i[1] == "sensor"]
    assert len(sensors) == 1, instrs
    assert sensors[0][2][2] == "@copper", sensors[0]
    # No `set s<i> @copper` should appear — the lift elides it.
    for i in instrs:
        if i[1] == "set":
            assert i[2][1] != "@copper", f"unwanted set: {i}"


def test_emit_printflush_lifts_magic_var_uservariable():
    """`@unit PRINTFLUSH` lifts to `printflush @unit` (the lift mechanism
    fires for any @-prefixed BuiltinWord that pushes a tag — mlog itself
    is responsible for whether the operand semantically makes sense)."""
    from mforth.backend.mlog.emit import emit
    from mforth.dictionary import resolve
    from mforth.parse import parse
    from mforth.stackcheck import stackcheck

    program = parse("@unit PRINTFLUSH", file="t.fs")
    d = resolve(program)
    sc = stackcheck(program, dictionary=d)
    instrs = emit(sc)
    pf = [i for i in instrs if i[1] == "printflush"]
    assert len(pf) == 1
    assert pf[0][2] == ("@unit",)


# ---------------------------------------------------------------------------
# REPL ↔ mlog equivalence — magic vars + content names
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Negative cases: failure surfaces specific to the new dictionary entries
# ---------------------------------------------------------------------------


def test_unknown_at_identifier_unresolved():
    """An `@`-prefixed name that ISN'T a registered entry must raise
    `UnresolvedWordError` — the resolver doesn't get any silent fallback
    just because the user typed `@`."""
    from mforth.dictionary import UnresolvedWordError, resolve
    from mforth.parse import parse

    program = parse("@nonexistent-thing", file="t.fs")
    with pytest.raises(UnresolvedWordError) as exc_info:
        resolve(program)
    assert "@nonexistent-thing" in str(exc_info.value)


def test_at_identifier_in_arithmetic_emits_slot_form():
    """`@time @ticks +` exercises the non-lift path: both magic vars
    must materialise into slots so `op add` can read them."""
    from mforth.backend.mlog.emit import emit
    from mforth.dictionary import resolve
    from mforth.parse import parse
    from mforth.stackcheck import stackcheck

    program = parse("@time @ticks +", file="t.fs")
    d = resolve(program)
    sc = stackcheck(program, dictionary=d)
    instrs = emit(sc)
    # Expect: set s0 @time / set s1 @tick (alias resolves to canonical) /
    # op add s0 s0 s1.
    set_ops = [i for i in instrs if i[1] == "set"]
    assert ("s0", "@time") in [i[2] for i in set_ops]
    # Alias `@ticks` resolved to canonical `@tick` — emitted as @tick.
    assert ("s1", "@tick") in [i[2] for i in set_ops]
    ops = [i for i in instrs if i[1] == "op"]
    assert any(i[2][0] == "add" for i in ops)


def test_at_identifier_emit_alias_uses_canonical_name():
    """The alias `@ticks` MUST emit `@tick` (the canonical name) — emit
    output must not leak the alias spelling, only the canonical."""
    from mforth.backend.mlog.emit import emit
    from mforth.dictionary import resolve
    from mforth.parse import parse
    from mforth.stackcheck import stackcheck

    program = parse("@ticks", file="t.fs")
    d = resolve(program)
    sc = stackcheck(program, dictionary=d)
    instrs = emit(sc)
    assert instrs == [(None, "set", ("s0", "@tick"))], instrs


def test_at_identifier_case_insensitive_lookup():
    """Dictionary is case-insensitive (per Forth tradition); `@TIME`
    must resolve to the same entry as `@time`."""
    d = standard_dictionary()
    assert d.lookup("@time") is d.lookup("@TIME")
    assert d.lookup("@time") is d.lookup("@Time")


def test_every_dictionary_at_entry_has_a_host_primitive():
    """Inventory drift guard: every `@`-prefixed BuiltinWord in the
    dictionary MUST have a corresponding host primitive registered —
    otherwise running the .fs source would NotImplementedError at the
    primitive-dispatch step."""
    ex = _make_executor_for_drift_check()
    d = standard_dictionary()
    missing: list[str] = []
    for name_lc, entry in d._entries.items():  # noqa: SLF001
        if (
            isinstance(entry, BuiltinWord)
            and entry.name.startswith("@")
            and entry.tag != "control"
        ):
            if entry.name.upper() not in ex._primitives:  # noqa: SLF001
                missing.append(entry.name)
    assert not missing, f"missing host primitives: {missing}"


def _make_executor_for_drift_check() -> Executor:
    ex = Executor(world=MockWorld())
    register_all(ex)
    return ex


def test_solid_registered_as_magic_sentinel_not_sensor_prop():
    """The shared name `@solid` appears in BOTH §1f (tile sentinel) and
    §3b (sensor prop). Convention: register once, under the §1 magic
    tag. Regression guard against accidental dual-registration."""
    d = standard_dictionary()
    e = d.lookup("@solid")
    assert isinstance(e, BuiltinWord)
    assert e.tag == "mindustry-magic", (
        f"@solid should be tagged 'mindustry-magic' (sentinel), "
        f"got {e.tag!r}"
    )


def test_equivalence_content_name_sensor():
    """`S" reactor1" @copper SENSOR PRINT` host vs mlog — both backends
    must observe the same SENSOR with @copper as the prop."""
    from mforth.backend.host import Executor
    from mforth.backend.mlog.emit import emit
    from mforth.backend.mlog.finalize import finalize
    from mforth.backend.primitives import register_all
    from mforth.backend.world import MockWorld
    from mforth.dictionary import resolve
    from mforth.parse import parse
    from mforth.stackcheck import stackcheck

    src = 'S" reactor1" @copper SENSOR PRINT'

    # Host
    ex = Executor(world=MockWorld())
    register_all(ex)
    program = parse(src, file="t.fs")
    d = resolve(program)
    sc = stackcheck(program, dictionary=d)
    ex.execute(sc)
    host_sensor_reads = [
        e for e in ex.world.events if e.__class__.__name__ == "SensorReadEvent"
    ]
    assert len(host_sensor_reads) == 1
    assert host_sensor_reads[0].block_name == "reactor1"
    assert host_sensor_reads[0].prop == "@copper"

    # mlog text references @copper as a bare operand.
    from mforth.backend.sidecar import WorldConfig
    sc = stackcheck(program, dictionary=d)
    instrs = emit(sc)
    text = finalize(instrs, world_config=WorldConfig(), source_path="t.fs")
    assert "@copper" in text, text
