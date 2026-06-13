"""Sidecar `.world.toml` loader for mforth.

Each `<name>.fs` may be paired with a `<name>.world.toml` that declares
the Mindustry environment the host REPL (and later the in-repo mlog
interpreter) should simulate against. The schema is intentionally narrow:

```toml
[links.<mforth-name>]
type   = "message" | "memory-cell" | "switch" | "core" | "generic"
target = "<in-game-name>"   # mode A (default, recommended)
index  = N                  # mode B (opt-in, fragile to re-link order)
# type-specific (optional):
size    = N                 # memory-cell capacity
enabled = bool              # switch initial state
# initial sensor readings (optional, bead mforth-0pg):
sensors = { "@copper" = 240, "@totalItems" = 80 }

[clock]
ipt      = 2 | 8 | 25       # micro | logic | hyper processor
realtime = bool             # advance `wait` instantly in tests
```

Exactly one of `target` or `index` per link is required (the CLAUDE.md
hard-rule: parser errors on both or neither). The left side of `=` is
the stable mforth-name the `.fs` source references; the right side
binds it to a concrete in-game block. The `target` mode names the block
by its in-game label and is recommended for tutorials. The `index` mode
addresses the link by its processor-slot number — stable across destroy/
rebuild only if re-link order is preserved, so it gets a tradeoff
warning in the how-to docs.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SidecarError(Exception):
    """Raised on sidecar parse / validation failures."""

    def __init__(self, message: str, source: Optional[str] = None) -> None:
        prefix = f"{source}: " if source else ""
        super().__init__(f"{prefix}{message}")
        self.message = message
        self.source = source


# ---------------------------------------------------------------------------
# Schema dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LinkSpec:
    mforth_name: str
    type: str
    target: Optional[str] = None
    index: Optional[int] = None
    size: Optional[int] = None
    enabled: Optional[bool] = None
    # bead mforth-0pg: initial sensor/property readings for this block.
    # Maps a SENSOR-readable `@`-property name (e.g. ``@copper``,
    # ``@totalItems``) to the float value the host MockWorld AND the
    # in-repo mlog interpreter should both return for
    # ``<block> @prop SENSOR``. Empty when the sidecar omits the optional
    # ``sensors`` inline table. Stored as a plain dict (the dataclass is
    # frozen so the *reference* can't be reassigned; callers must not
    # mutate the dict in place).
    sensors: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ClockConfig:
    ipt: int = 8
    realtime: bool = False


@dataclass
class WorldConfig:
    links: list = field(default_factory=list)
    clock: ClockConfig = field(default_factory=ClockConfig)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_VALID_TYPES = {"message", "memory-cell", "switch", "core", "generic"}
_VALID_IPT = {2, 8, 25}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_sidecar(path: Union[str, Path]) -> WorldConfig:
    """Read and parse a `.world.toml` file. Raises `SidecarError` on missing
    file, malformed TOML, or schema violations.
    """
    p = Path(path)
    if not p.exists():
        raise SidecarError(f"sidecar file not found: {p}", source=str(p))
    try:
        with p.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise SidecarError(f"TOML parse error: {e}", source=str(p)) from e
    return parse_sidecar(data, source=str(p))


def parse_sidecar(data: dict, source: str = "<sidecar>") -> WorldConfig:
    """Validate `data` (a parsed TOML dict) and return a `WorldConfig`."""
    links = _parse_links(data.get("links", {}), source)
    clock = _parse_clock(data.get("clock", {}), source)
    return WorldConfig(links=links, clock=clock)


# ---------------------------------------------------------------------------
# Internal: links table
# ---------------------------------------------------------------------------


def _parse_links(links_table, source: str) -> list[LinkSpec]:
    if not isinstance(links_table, dict):
        raise SidecarError("[links] must be a table of tables", source=source)
    result: list[LinkSpec] = []
    for name, spec in links_table.items():
        if not isinstance(spec, dict):
            raise SidecarError(f"[links.{name}] must be a table", source=source)
        result.append(_parse_one_link(name, spec, source))
    return result


def _parse_one_link(name: str, spec: dict, source: str) -> LinkSpec:
    link_type = spec.get("type")
    if not link_type:
        raise SidecarError(f"[links.{name}] missing 'type'", source=source)
    if link_type not in _VALID_TYPES:
        raise SidecarError(
            f"[links.{name}] unknown type '{link_type}' "
            f"(valid: {sorted(_VALID_TYPES)})",
            source=source,
        )

    target = spec.get("target")
    index = spec.get("index")
    has_target = target is not None
    has_index = index is not None
    if has_target and has_index:
        raise SidecarError(
            f"[links.{name}] cannot specify both 'target' and 'index' "
            f"— exactly one is required",
            source=source,
        )
    if not has_target and not has_index:
        raise SidecarError(
            f"[links.{name}] requires exactly one of 'target' or 'index' "
            f"(neither was given)",
            source=source,
        )
    if has_target and not isinstance(target, str):
        raise SidecarError(
            f"[links.{name}].target must be a string", source=source
        )
    if has_index and (not isinstance(index, int) or isinstance(index, bool)):
        raise SidecarError(
            f"[links.{name}].index must be an integer", source=source
        )

    size = spec.get("size")
    enabled = spec.get("enabled")
    if size is not None and (not isinstance(size, int) or isinstance(size, bool)):
        raise SidecarError(
            f"[links.{name}].size must be an integer", source=source
        )
    if enabled is not None and not isinstance(enabled, bool):
        raise SidecarError(
            f"[links.{name}].enabled must be a boolean", source=source
        )

    sensors = _parse_sensors(name, spec.get("sensors"), source)

    return LinkSpec(
        mforth_name=name,
        type=link_type,
        target=target,
        index=index,
        size=size,
        enabled=enabled,
        sensors=sensors,
    )


def _parse_sensors(name: str, raw, source: str) -> dict:
    """Validate the optional per-link ``sensors`` inline table (bead
    mforth-0pg) and return a ``{@prop: float}`` dict.

    Each key must be a SENSOR-readable ``@``-property name (validated
    against :func:`mforth.dictionary.sensor_readable_names`); each value
    must be a finite number (``bool`` rejected — TOML ``true``/``false``
    are not sensor readings). Returns ``{}`` when the table is absent.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SidecarError(
            f"[links.{name}].sensors must be a table of "
            f"`@property = value` entries",
            source=source,
        )
    # Local import keeps the dictionary (a heavier module) off the
    # import path for callers that only parse link topology.
    from mforth.dictionary import sensor_readable_names

    valid = sensor_readable_names()
    out: dict = {}
    for prop, value in raw.items():
        if not (isinstance(prop, str) and prop.startswith("@")):
            raise SidecarError(
                f"[links.{name}].sensors key {prop!r} must be an "
                f"`@`-prefixed property name (e.g. '@copper', '@totalItems')",
                source=source,
            )
        if prop not in valid:
            raise SidecarError(
                f"[links.{name}].sensors key {prop!r} is not a known "
                f"SENSOR-readable property — see the dictionary reference "
                f"for the readable `@`-names (items, liquids, sensor stats)",
                source=source,
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SidecarError(
                f"[links.{name}].sensors[{prop!r}] must be a number "
                f"(got {value!r})",
                source=source,
            )
        out[prop] = float(value)
    return out


# ---------------------------------------------------------------------------
# Internal: clock table
# ---------------------------------------------------------------------------


def _parse_clock(clock_table, source: str) -> ClockConfig:
    if not isinstance(clock_table, dict):
        raise SidecarError("[clock] must be a table", source=source)
    ipt = clock_table.get("ipt", 8)
    if not isinstance(ipt, int) or isinstance(ipt, bool) or ipt not in _VALID_IPT:
        raise SidecarError(
            f"[clock].ipt must be one of {sorted(_VALID_IPT)} "
            f"(got {ipt!r}; micro=2, logic=8, hyper=25)",
            source=source,
        )
    realtime = clock_table.get("realtime", False)
    if not isinstance(realtime, bool):
        raise SidecarError(
            f"[clock].realtime must be a boolean (got {realtime!r})",
            source=source,
        )
    return ClockConfig(ipt=ipt, realtime=realtime)


__all__ = [
    "ClockConfig",
    "LinkSpec",
    "SidecarError",
    "WorldConfig",
    "load_sidecar",
    "parse_sidecar",
]
