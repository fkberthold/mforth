"""Sidecar-seeded SENSOR values — bead mforth-0pg.

The `.world.toml` sidecar may declare initial sensor/property readings
per link (``sensors = { "@copper" = 240 }``). :func:`build_world` seeds
the matching :class:`Block.state` so the host ``SENSOR`` primitive reads
the declared value instead of the empty-block default of ``0.0``.

The load-bearing equivalence point: ``build_world`` is the SINGLE world
factory both the host REPL runner AND the equivalence harness's mlog path
call, so seeding there makes the host ``SENSOR`` and the compiled
``sensor`` instruction return the SAME seeded value — preserving the
headline REPL ↔ mlog equivalence property (CLAUDE.md hard rule).
"""

from __future__ import annotations

from mforth.backend.runner import build_world
from mforth.backend.sidecar import parse_sidecar
from mforth.backend.world import SensorReadEvent
from mforth.mlog_interp import MlogInterpreter


def _seeded_config():
    return parse_sidecar(
        {
            "links": {
                "vault1": {
                    "type": "core",
                    "target": "vault1",
                    "sensors": {"@copper": 240, "@totalItems": 80},
                }
            }
        }
    )


# ---------------------------------------------------------------------------
# Host side — build_world seeds Block.state; world.sensor reads it.
# ---------------------------------------------------------------------------


def test_build_world_seeds_sensor_value_into_block_state():
    world = build_world(_seeded_config())
    block = world.lookup_block("vault1")
    assert block is not None
    assert block.state["@copper"] == 240.0
    assert block.state["@totalItems"] == 80.0


def test_seeded_sensor_read_returns_value_not_zero():
    world = build_world(_seeded_config())
    value = world.sensor("vault1", "@copper")
    assert value == 240.0
    # And it still emits the SensorReadEvent with the seeded value.
    evts = [e for e in world.events if isinstance(e, SensorReadEvent)]
    assert evts[-1].value == 240.0


def test_unseeded_property_still_reads_zero():
    """A property not in the `sensors` table keeps the empty-block 0.0
    default, so the existing 0.0-baseline exercises stay correct."""
    world = build_world(_seeded_config())
    assert world.sensor("vault1", "@graphite") == 0.0


def test_world_without_sensors_table_unchanged():
    cfg = parse_sidecar(
        {"links": {"vault1": {"type": "core", "target": "vault1"}}}
    )
    world = build_world(cfg)
    assert world.sensor("vault1", "@copper") == 0.0


# ---------------------------------------------------------------------------
# mlog side — the SAME config drives the interpreter's MockWorld; the
# compiled `sensor` instruction reads the SAME seeded value.
# ---------------------------------------------------------------------------


def test_mlog_interp_reads_same_seeded_value():
    world = build_world(_seeded_config())
    # `sensor <result> <block> <prop>` — the shape the codegen emits.
    text = (
        "# header\n"
        "sensor s0 vault1 @copper\n"
        "print s0\n"
        "end\n"
    )
    interp = MlogInterpreter(world=world, text=text)
    interp.run(iterations=1)
    sensor_evts = [e for e in world.events if isinstance(e, SensorReadEvent)]
    assert sensor_evts[-1].value == 240.0
