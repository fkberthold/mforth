"""MockWorld + EventStream — the host-side simulation of Mindustry's
logic-processor environment.

This module is the single seam every host-side subscriber attaches to.
The REPL executor (bead .10), the web visualiser (.20), the LSP runtime
diagnostics (.23), and the integration test harness (.30 and .31) all
consume events from one `MockWorld`. The mlog backend's in-repo
interpreter (.31) drives the SAME method surface — that is how the
REPL ↔ mlog equivalence property (CLAUDE.md headline test class)
becomes mechanically checkable.

Method stubs here emit events with shape-correct payloads but no deep
behavioural fidelity beyond what blink+counter need; real behavioural
faithfulness comes in B5 (bead .12 / .13). The event shapes ARE the
contract downstream stages depend on, so they are stable from this
bead onward.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional


# ---------------------------------------------------------------------------
# Event dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    timestamp: float


@dataclass(frozen=True)
class MessagePrintEvent(Event):
    text: str


@dataclass(frozen=True)
class MessagePrintflushEvent(Event):
    block_name: str
    buffer: str


@dataclass(frozen=True)
class SensorReadEvent(Event):
    block_name: str
    prop: str
    value: float


@dataclass(frozen=True)
class LinkResolvedEvent(Event):
    index: int
    block_name: str


@dataclass(frozen=True)
class WaitEvent(Event):
    seconds: float


@dataclass(frozen=True)
class VariableReadEvent(Event):
    name: str
    value: float


@dataclass(frozen=True)
class VariableWriteEvent(Event):
    name: str
    value: float


# ---------------------------------------------------------------------------
# EventStream
# ---------------------------------------------------------------------------


@dataclass
class EventStream:
    events: list = field(default_factory=list)
    subscribers: list = field(default_factory=list)
    tick: float = 0.0

    def emit(self, cls, **payload) -> Event:
        """Construct an event of `cls` stamped with the current tick, append
        to the stream, and broadcast to subscribers."""
        event = cls(timestamp=self.tick, **payload)
        self.events.append(event)
        for cb in self.subscribers:
            cb(event)
        return event

    def subscribe(self, callback: Callable[[Event], None]) -> None:
        self.subscribers.append(callback)

    def __iter__(self) -> Iterator[Event]:
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)


# ---------------------------------------------------------------------------
# Block
# ---------------------------------------------------------------------------


@dataclass
class Block:
    name: str
    type: str
    state: dict = field(default_factory=dict)

    @classmethod
    def message(cls, name: str) -> "Block":
        return cls(name=name, type="message", state={"buffer": []})

    @classmethod
    def memory_cell(cls, name: str, size: int = 512) -> "Block":
        return cls(name=name, type="memory-cell", state={"data": [0.0] * size})

    @classmethod
    def switch(cls, name: str, on: bool = False) -> "Block":
        return cls(name=name, type="switch", state={"on": on})

    @classmethod
    def generic(cls, name: str) -> "Block":
        return cls(name=name, type="generic", state={})


# ---------------------------------------------------------------------------
# MockWorld
# ---------------------------------------------------------------------------


_DEFAULT_VARIABLES = {
    "@counter": 0.0,
    "@time": 0.0,
    "@ticks": 0.0,
    "@ipt": 8.0,  # logic-processor default; microprocessor=2, hyper=25
    "@links": 0.0,
}


def _default_variables() -> dict:
    return dict(_DEFAULT_VARIABLES)


@dataclass
class MockWorld:
    links: dict = field(default_factory=dict)
    variables: dict = field(default_factory=_default_variables)
    events: EventStream = field(default_factory=EventStream)
    config: dict = field(default_factory=dict)
    _print_queue: list = field(default_factory=list)

    def __post_init__(self) -> None:
        self.variables["@links"] = float(len(self.links))

    # ---- link management ----

    def add_link(self, block: Block) -> None:
        self.links[block.name] = block
        self.variables["@links"] = float(len(self.links))

    def lookup_block(self, name: str) -> Optional[Block]:
        return self.links.get(name)

    # ---- primitive method stubs (each emits its event) ----

    def print(self, text: Any) -> None:
        """Queue text for the next printflush. Emits MessagePrintEvent."""
        s = str(text)
        self._print_queue.append(s)
        self.events.emit(MessagePrintEvent, text=s)

    def printflush(self, block_name: str) -> None:
        """Send the accumulated print buffer to `block_name`. Replaces the
        target block's buffer (matching mlog: each printflush is a fresh
        message). Emits MessagePrintflushEvent. The event is emitted
        regardless of whether the block exists.
        """
        buffer_text = "".join(self._print_queue)
        block = self.lookup_block(block_name)
        if block is not None and block.type == "message":
            block.state["buffer"] = [buffer_text] if buffer_text else []
        self._print_queue = []
        self.events.emit(
            MessagePrintflushEvent, block_name=block_name, buffer=buffer_text
        )

    def wait(self, seconds: float) -> None:
        """Advance the simulation clock by `seconds`; emit WaitEvent."""
        delta = float(seconds)
        self.events.tick += delta
        self.events.emit(WaitEvent, seconds=delta)

    def sensor(self, block_name: str, prop: str) -> float:
        """Read property `prop` from `block_name`. Returns 0.0 on missing
        block or unknown property (matches the community-lore mlog
        behaviour for invalid sensor targets). Emits SensorReadEvent.
        """
        block = self.lookup_block(block_name)
        value = 0.0
        if block is not None and prop in block.state:
            raw = block.state[prop]
            value = float(raw) if not isinstance(raw, bool) else (1.0 if raw else 0.0)
        self.events.emit(
            SensorReadEvent, block_name=block_name, prop=prop, value=value
        )
        return value

    def getlink(self, i: int) -> Optional[str]:
        """Return the mforth-name of the i-th linked block, or None if i is
        out of range (mlog: null). Emits LinkResolvedEvent only for in-
        range lookups.
        """
        names = list(self.links.keys())
        if i < 0 or i >= len(names):
            return None
        name = names[i]
        self.events.emit(LinkResolvedEvent, index=i, block_name=name)
        return name

    def read_variable(self, name: str) -> float:
        """Read a user or magic variable. Defaults missing names to 0.0
        (mlog: null). Emits VariableReadEvent.
        """
        value = float(self.variables.get(name, 0.0))
        self.events.emit(VariableReadEvent, name=name, value=value)
        return value

    def write_variable(self, name: str, value: Any) -> None:
        """Write a user or magic variable. Emits VariableWriteEvent."""
        v = float(value)
        self.variables[name] = v
        self.events.emit(VariableWriteEvent, name=name, value=v)


__all__ = [
    "Block",
    "Event",
    "EventStream",
    "LinkResolvedEvent",
    "MessagePrintEvent",
    "MessagePrintflushEvent",
    "MockWorld",
    "SensorReadEvent",
    "VariableReadEvent",
    "VariableWriteEvent",
    "WaitEvent",
]
