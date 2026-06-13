"""Unit tests for the .world.toml sidecar loader.

Bead mforth-10t.9.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mforth.backend.sidecar import (
    ClockConfig,
    LinkSpec,
    SidecarError,
    WorldConfig,
    load_sidecar,
    parse_sidecar,
)


# ---------------------------------------------------------------------------
# Empty / minimal
# ---------------------------------------------------------------------------


def test_empty_table_yields_empty_links_and_default_clock():
    cfg = parse_sidecar({})
    assert cfg.links == []
    assert cfg.clock == ClockConfig(ipt=8, realtime=False)


def test_only_links_no_clock():
    data = {"links": {"display": {"type": "message", "target": "message1"}}}
    cfg = parse_sidecar(data)
    assert len(cfg.links) == 1
    assert cfg.clock == ClockConfig(ipt=8, realtime=False)


def test_only_clock_no_links():
    cfg = parse_sidecar({"clock": {"ipt": 25, "realtime": True}})
    assert cfg.links == []
    assert cfg.clock == ClockConfig(ipt=25, realtime=True)


# ---------------------------------------------------------------------------
# Link mode A (target — recommended)
# ---------------------------------------------------------------------------


def test_link_with_target():
    cfg = parse_sidecar(
        {"links": {"display": {"type": "message", "target": "message1"}}}
    )
    link = cfg.links[0]
    assert link.mforth_name == "display"
    assert link.type == "message"
    assert link.target == "message1"
    assert link.index is None


# ---------------------------------------------------------------------------
# Link mode B (index — opt-in)
# ---------------------------------------------------------------------------


def test_link_with_index():
    cfg = parse_sidecar(
        {"links": {"slot2": {"type": "memory-cell", "index": 2}}}
    )
    link = cfg.links[0]
    assert link.mforth_name == "slot2"
    assert link.index == 2
    assert link.target is None


# ---------------------------------------------------------------------------
# Validation: exactly one of target | index (load-bearing CLAUDE.md rule)
# ---------------------------------------------------------------------------


def test_link_with_both_target_and_index_raises():
    with pytest.raises(SidecarError) as exc:
        parse_sidecar(
            {
                "links": {
                    "x": {"type": "message", "target": "message1", "index": 0}
                }
            }
        )
    assert "exactly one" in str(exc.value).lower() or "both" in str(exc.value).lower()


def test_link_with_neither_target_nor_index_raises():
    with pytest.raises(SidecarError) as exc:
        parse_sidecar({"links": {"x": {"type": "message"}}})
    assert "exactly one" in str(exc.value).lower() or "neither" in str(exc.value).lower()


def test_link_missing_type_raises():
    with pytest.raises(SidecarError) as exc:
        parse_sidecar({"links": {"x": {"target": "message1"}}})
    assert "type" in str(exc.value).lower()


def test_link_unknown_type_raises():
    with pytest.raises(SidecarError) as exc:
        parse_sidecar(
            {"links": {"x": {"type": "fictional", "target": "message1"}}}
        )
    assert "fictional" in str(exc.value).lower() or "unknown" in str(exc.value).lower()


def test_link_target_must_be_string():
    with pytest.raises(SidecarError):
        parse_sidecar(
            {"links": {"x": {"type": "message", "target": 42}}}
        )


def test_link_index_must_be_int():
    with pytest.raises(SidecarError):
        parse_sidecar(
            {"links": {"x": {"type": "memory-cell", "index": "two"}}}
        )


def test_links_must_be_a_table():
    with pytest.raises(SidecarError):
        parse_sidecar({"links": "not a table"})


# ---------------------------------------------------------------------------
# Type-specific options
# ---------------------------------------------------------------------------


def test_memory_cell_with_size():
    cfg = parse_sidecar(
        {"links": {"big": {"type": "memory-cell", "target": "cell1", "size": 256}}}
    )
    assert cfg.links[0].size == 256


def test_switch_with_enabled():
    cfg = parse_sidecar(
        {"links": {"sw": {"type": "switch", "target": "switch1", "enabled": True}}}
    )
    assert cfg.links[0].enabled is True


def test_memory_cell_size_must_be_int():
    with pytest.raises(SidecarError):
        parse_sidecar(
            {"links": {"x": {"type": "memory-cell", "target": "c1", "size": "lots"}}}
        )


def test_switch_enabled_must_be_bool():
    with pytest.raises(SidecarError):
        parse_sidecar(
            {"links": {"x": {"type": "switch", "target": "s1", "enabled": "yes"}}}
        )


# ---------------------------------------------------------------------------
# Seeded sensor values (bead mforth-0pg)
# ---------------------------------------------------------------------------


def test_link_with_sensors_table():
    cfg = parse_sidecar(
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
    link = cfg.links[0]
    assert link.sensors == {"@copper": 240.0, "@totalItems": 80.0}


def test_link_without_sensors_defaults_to_empty_dict():
    cfg = parse_sidecar(
        {"links": {"vault1": {"type": "core", "target": "vault1"}}}
    )
    assert cfg.links[0].sensors == {}


def test_link_sensors_accepts_floats():
    cfg = parse_sidecar(
        {
            "links": {
                "reactor": {
                    "type": "generic",
                    "target": "reactor1",
                    "sensors": {"@heat": 0.5},
                }
            }
        }
    )
    assert cfg.links[0].sensors == {"@heat": 0.5}


def test_link_sensors_must_be_a_table():
    with pytest.raises(SidecarError) as exc:
        parse_sidecar(
            {
                "links": {
                    "x": {"type": "core", "target": "c1", "sensors": 42}
                }
            }
        )
    assert "sensors" in str(exc.value).lower()


def test_link_sensors_unknown_property_raises():
    with pytest.raises(SidecarError) as exc:
        parse_sidecar(
            {
                "links": {
                    "x": {
                        "type": "core",
                        "target": "c1",
                        "sensors": {"@notAProp": 1},
                    }
                }
            }
        )
    msg = str(exc.value).lower()
    assert "@notaprop" in msg
    assert "not a known sensor-readable" in msg


def test_link_sensors_property_must_start_with_at():
    with pytest.raises(SidecarError) as exc:
        parse_sidecar(
            {
                "links": {
                    "x": {
                        "type": "core",
                        "target": "c1",
                        "sensors": {"copper": 1},
                    }
                }
            }
        )
    assert "copper" in str(exc.value).lower()


def test_link_sensors_value_must_be_numeric():
    with pytest.raises(SidecarError) as exc:
        parse_sidecar(
            {
                "links": {
                    "x": {
                        "type": "core",
                        "target": "c1",
                        "sensors": {"@copper": "lots"},
                    }
                }
            }
        )
    assert "@copper" in str(exc.value).lower()


def test_link_sensors_value_bool_rejected():
    with pytest.raises(SidecarError) as exc:
        parse_sidecar(
            {
                "links": {
                    "x": {
                        "type": "core",
                        "target": "c1",
                        "sensors": {"@copper": True},
                    }
                }
            }
        )
    assert "@copper" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


def test_clock_ipt_microprocessor():
    cfg = parse_sidecar({"clock": {"ipt": 2}})
    assert cfg.clock.ipt == 2


def test_clock_ipt_hyper():
    cfg = parse_sidecar({"clock": {"ipt": 25}})
    assert cfg.clock.ipt == 25


def test_clock_invalid_ipt_raises():
    with pytest.raises(SidecarError) as exc:
        parse_sidecar({"clock": {"ipt": 99}})
    assert "ipt" in str(exc.value).lower()


def test_clock_ipt_not_an_int_raises():
    with pytest.raises(SidecarError):
        parse_sidecar({"clock": {"ipt": "fast"}})


def test_clock_realtime_must_be_bool():
    with pytest.raises(SidecarError):
        parse_sidecar({"clock": {"realtime": "yes"}})


# ---------------------------------------------------------------------------
# load_sidecar from file
# ---------------------------------------------------------------------------


def test_load_sidecar_from_file(tmp_path: Path):
    p = tmp_path / "blink.world.toml"
    p.write_text(
        '''[links.display]
type = "message"
target = "message1"

[clock]
ipt = 8
realtime = false
'''
    )
    cfg = load_sidecar(p)
    assert cfg.links[0].mforth_name == "display"
    assert cfg.links[0].target == "message1"
    assert cfg.clock.ipt == 8


def test_load_sidecar_missing_file_raises(tmp_path: Path):
    with pytest.raises(SidecarError) as exc:
        load_sidecar(tmp_path / "nonexistent.world.toml")
    assert "not found" in str(exc.value).lower() or "no such" in str(exc.value).lower()


def test_load_sidecar_invalid_toml_raises(tmp_path: Path):
    p = tmp_path / "broken.world.toml"
    p.write_text("[unclosed table")
    with pytest.raises(SidecarError) as exc:
        load_sidecar(p)
    assert "toml" in str(exc.value).lower() or "parse" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Example fixture shape (matches docs/examples)
# ---------------------------------------------------------------------------


def test_blink_world_toml_fixture_parses(tmp_path: Path):
    p = tmp_path / "blink.world.toml"
    p.write_text(
        '''# Sidecar for blink.fs — sends "hello" to a message block once per tick.

[links.display]
type   = "message"
target = "message1"

[clock]
ipt      = 8
realtime = false
'''
    )
    cfg = load_sidecar(p)
    assert len(cfg.links) == 1
    link = cfg.links[0]
    assert link.mforth_name == "display"
    assert link.type == "message"
    assert link.target == "message1"
    assert link.index is None
    assert cfg.clock == ClockConfig(ipt=8, realtime=False)
