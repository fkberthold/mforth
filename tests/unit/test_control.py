"""Unit tests for the CONTROL (block-side) v1 primitives — bead mforth-cto.

Pins the contract for the five per-sub-command CONTROL-* words across:

* Dictionary registration (name, stack effect, tag).
* MockWorld.control() shape + ControlEvent emission.
* Host primitive invocation (data-stack pop order + event payload).
* mlog emit slot-form (all five) and lifting (the two most common pairs).
* Negative cases (invalid sub-command on world; non-existent block on
  enabled/config state mutation).

Per-sub-command words were chosen over one umbrella CONTROL with a
string-tag dispatcher: the static stack effects differ per sub-command
(enabled is (2, 0); shoot is (4, 0); color is (4, 0)) and per-word
dictionary entries give clean LSP completion + static stack analysis.
"""

from __future__ import annotations

import pytest

from mforth.backend.mlog.emit import emit
from mforth.backend.mlog.slots import allocate_slots
from mforth.backend.world import Block, ControlEvent, MockWorld
from mforth.dictionary import BuiltinWord, StackEffect, standard_dictionary
from mforth.parse import parse
from mforth.stackcheck import stackcheck


# ---------------------------------------------------------------------------
# Dictionary registration
# ---------------------------------------------------------------------------


CONTROL_WORDS = [
    ("CONTROL-ENABLED", StackEffect(2, 0)),
    ("CONTROL-CONFIG", StackEffect(2, 0)),
    ("CONTROL-SHOOT", StackEffect(4, 0)),
    ("CONTROL-SHOOTP", StackEffect(3, 0)),
    ("CONTROL-COLOR", StackEffect(4, 0)),
]


@pytest.mark.parametrize("name,effect", CONTROL_WORDS)
def test_dictionary_registers_control_word(name: str, effect: StackEffect) -> None:
    d = standard_dictionary()
    entry = d.lookup(name)
    assert isinstance(entry, BuiltinWord), f"{name} should be a BuiltinWord"
    assert entry.stack_effect == effect, (
        f"{name} expected {effect}, got {entry.stack_effect}"
    )
    assert entry.tag == "mindustry-control", (
        f"{name} should be tagged 'mindustry-control', got {entry.tag!r}"
    )
    assert entry.doc, f"{name} must have a non-empty doc string"


def test_dictionary_lookup_is_case_insensitive() -> None:
    d = standard_dictionary()
    for name, _ in CONTROL_WORDS:
        assert d.lookup(name.lower()) is d.lookup(name)


# ---------------------------------------------------------------------------
# MockWorld.control + ControlEvent
# ---------------------------------------------------------------------------


def test_control_event_is_frozen_dataclass() -> None:
    """ControlEvent must be a frozen dataclass with fields op, block_name,
    args, timestamp (inherited)."""
    ev = ControlEvent(timestamp=1.0, op="enabled", block_name="cv1", args=(1,))
    assert ev.op == "enabled"
    assert ev.block_name == "cv1"
    assert ev.args == (1,)
    assert ev.timestamp == 1.0
    with pytest.raises(Exception):
        ev.op = "config"  # frozen — must reject mutation


def test_mockworld_control_enabled_emits_event_and_mutates_state() -> None:
    world = MockWorld()
    world.add_link(Block.switch("sw1", on=False))
    world.control("enabled", "sw1", 1)
    events = [e for e in world.events if isinstance(e, ControlEvent)]
    assert len(events) == 1
    ev = events[0]
    assert ev.op == "enabled"
    assert ev.block_name == "sw1"
    assert ev.args == (1,)
    # State mutation: the switch's "on" should now be True.
    assert world.lookup_block("sw1").state["on"] is True


def test_mockworld_control_config_emits_event_and_mutates_state() -> None:
    world = MockWorld()
    world.add_link(Block.generic("sorter1"))
    world.control("config", "sorter1", "@copper")
    events = [e for e in world.events if isinstance(e, ControlEvent)]
    assert len(events) == 1
    assert events[0].op == "config"
    assert events[0].block_name == "sorter1"
    assert events[0].args == ("@copper",)
    # State mutation: config recorded on the block.
    assert world.lookup_block("sorter1").state["config"] == "@copper"


def test_mockworld_control_shoot_records_event_no_state_mutation() -> None:
    world = MockWorld()
    world.add_link(Block.generic("turret1"))
    world.control("shoot", "turret1", 10, 20, 1)
    events = [e for e in world.events if isinstance(e, ControlEvent)]
    assert len(events) == 1
    assert events[0].op == "shoot"
    assert events[0].block_name == "turret1"
    assert events[0].args == (10, 20, 1)


def test_mockworld_control_on_missing_block_still_emits_event() -> None:
    """Mirrors the printflush-to-nonexistent-block convention (.12): the
    event is still observable even though no state mutates."""
    world = MockWorld()
    world.control("enabled", "ghost", 1)
    events = [e for e in world.events if isinstance(e, ControlEvent)]
    assert len(events) == 1
    assert events[0].block_name == "ghost"


# ---------------------------------------------------------------------------
# Host primitive dispatch — through the Executor + register_all
# ---------------------------------------------------------------------------


def _run_source(source: str, *blocks: Block) -> MockWorld:
    """Run a `.fs` source string against a MockWorld pre-seeded with
    `blocks` and return the world for inspection."""
    from mforth.backend.host import Executor
    from mforth.backend.primitives import register_all
    from mforth.dictionary import UserVariable, resolve
    from mforth.parse import SrcLoc

    world = MockWorld()
    for b in blocks:
        world.add_link(b)

    program = parse(source, file="<test>")
    dictionary = standard_dictionary()
    src_loc = SrcLoc("<test>", 1, 1)
    for b in blocks:
        dictionary.add_variable(UserVariable(name=b.name, src_loc=src_loc))
    dictionary = resolve(program, dictionary=dictionary)
    executor = Executor(world=world, dictionary=dictionary)
    register_all(executor)
    # Wire link names as pre-bound user variables that push their own
    # name (matching how the Runner does sidecar pre-seeding).
    for b in blocks:
        executor.register_primitive(
            b.name, (lambda name: lambda ex: ex.data_stack.append(name))(b.name)
        )
    result = stackcheck(program, dictionary=dictionary)
    executor.execute(result)
    return world


def test_host_control_enabled_pops_args_in_forth_order() -> None:
    """CONTROL-ENABLED ( block flag -- ). After `cv1 1 CONTROL-ENABLED`
    the world should record the event with block="cv1", args=(1,)."""
    world = _run_source(
        "cv1 1 CONTROL-ENABLED",
        Block.switch("cv1", on=False),
    )
    evs = [e for e in world.events if isinstance(e, ControlEvent)]
    assert len(evs) == 1
    assert evs[0].op == "enabled"
    assert evs[0].block_name == "cv1"
    assert evs[0].args == (1,)
    assert world.lookup_block("cv1").state["on"] is True


def test_host_control_config_pops_args_in_forth_order() -> None:
    world = _run_source(
        'sorter1 S" @copper" CONTROL-CONFIG',
        Block.generic("sorter1"),
    )
    evs = [e for e in world.events if isinstance(e, ControlEvent)]
    assert len(evs) == 1
    assert evs[0].op == "config"
    assert evs[0].block_name == "sorter1"
    assert evs[0].args == ("@copper",)


def test_host_control_shoot_pops_four_args() -> None:
    world = _run_source(
        "turret1 50 60 1 CONTROL-SHOOT",
        Block.generic("turret1"),
    )
    evs = [e for e in world.events if isinstance(e, ControlEvent)]
    assert len(evs) == 1
    assert evs[0].op == "shoot"
    assert evs[0].block_name == "turret1"
    assert evs[0].args == (50, 60, 1)


def test_host_control_color_pops_rgb() -> None:
    world = _run_source(
        "illum1 255 128 0 CONTROL-COLOR",
        Block.generic("illum1"),
    )
    evs = [e for e in world.events if isinstance(e, ControlEvent)]
    assert len(evs) == 1
    assert evs[0].op == "color"
    assert evs[0].args == (255, 128, 0)


# ---------------------------------------------------------------------------
# mlog emit — slot-form fallback + lifting for the two common pairs
# ---------------------------------------------------------------------------


def _emit_source(source: str, *link_names: str) -> list:
    """Parse + resolve + stackcheck + emit `source`; returns the
    instruction tuple list. `link_names` registers UserVariables for any
    block names referenced bare in the source."""
    from mforth.dictionary import UserVariable, resolve
    from mforth.parse import SrcLoc

    program = parse(source, file="<test>")
    d = standard_dictionary()
    loc = SrcLoc("<test>", 1, 1)
    for n in link_names:
        d.add_variable(UserVariable(name=n, src_loc=loc))
    d = resolve(program, dictionary=d)
    result = stackcheck(program, dictionary=d)
    slots = allocate_slots(result)
    return emit(result, slots)


def test_emit_control_enabled_lifted_link_and_literal() -> None:
    """`cv1 1 CONTROL-ENABLED` → single `control enabled cv1 1 0 0 0` line."""
    instrs = _emit_source("cv1 1 CONTROL-ENABLED", "cv1")
    controls = [i for i in instrs if i[1] == "control"]
    assert controls == [(None, "control", ("enabled", "cv1", "1", "0", "0", "0"))]


def test_emit_control_config_lifted_link_and_at_identifier() -> None:
    """`sorter1 @copper CONTROL-CONFIG` → `control config sorter1 @copper 0 0 0`."""
    instrs = _emit_source("sorter1 @copper CONTROL-CONFIG", "sorter1")
    controls = [i for i in instrs if i[1] == "control"]
    assert controls == [
        (None, "control", ("config", "sorter1", "@copper", "0", "0", "0"))
    ]


def test_emit_control_config_lifted_link_and_litstr() -> None:
    instrs = _emit_source('sorter1 S" @lead" CONTROL-CONFIG', "sorter1")
    controls = [i for i in instrs if i[1] == "control"]
    assert controls == [
        (None, "control", ("config", "sorter1", "@lead", "0", "0", "0"))
    ]


def test_emit_control_enabled_slot_form_fallback() -> None:
    """When both operands are computed (block via GETLINK, flag via
    arithmetic), the emitter falls back to slot refs and pads with
    zeros to reach the 5-operand mlog `control` shape."""
    instrs = _emit_source("0 GETLINK 2 3 + CONTROL-ENABLED")
    controls = [i for i in instrs if i[1] == "control"]
    assert len(controls) == 1
    (_, op, ops) = controls[0]
    assert op == "control"
    assert ops[0] == "enabled"
    # block slot + flag slot + three padding zeros
    assert ops[1].startswith("s")
    assert ops[2].startswith("s")
    assert ops[3:] == ("0", "0", "0")


def test_emit_control_shoot_emits_four_operands_no_padding() -> None:
    """CONTROL-SHOOT uses all four control operand slots — no padding zeros.

    SHOOT/SHOOTP/COLOR don't have dedicated lifters in v1; their operand
    counts are larger and the slot-form emission is sufficient. The
    block source is GETLINK so v1's cell-free rule doesn't trip on a
    bare link uservar followed by intermediate computation. mlog's
    `control` instruction is always (sub + 5 operands) = 6 tokens
    after the opcode; SHOOT fills all 5 stack-derived operands so
    there are zero padding zeros in the tuple.
    """
    instrs = _emit_source("0 GETLINK 50 60 1 CONTROL-SHOOT")
    controls = [i for i in instrs if i[1] == "control"]
    assert len(controls) == 1
    (_, _, ops) = controls[0]
    assert ops[0] == "shoot"
    # Operand list: sub + block + x + y + shoot + pad = 6 tokens, with
    # the trailing pad as the only "0" (block/x/y/shoot are slot refs).
    assert len(ops) == 6
    # Last operand is the single padding zero (SHOOT only consumes 4
    # stack slots so 1 of the 5 mlog operand positions is unused).
    assert ops[-1] == "0"


def test_emit_control_color_emits_four_rgb_operands() -> None:
    instrs = _emit_source("0 GETLINK 255 128 0 CONTROL-COLOR")
    controls = [i for i in instrs if i[1] == "control"]
    assert len(controls) == 1
    (_, _, ops) = controls[0]
    assert ops[0] == "color"
    # sub + block + r + g + b + pad = 6 (COLOR consumes 4 stack slots,
    # one mlog operand position remains and is padded).
    assert len(ops) == 6
    assert ops[-1] == "0"


def test_emit_control_shootp_emits_three_args_plus_two_pads() -> None:
    """SHOOTP has 3 stack args (block + unit + shoot), needs 2 zero
    pads to fill mlog's 5-operand control shape."""
    instrs = _emit_source("0 GETLINK @unit 1 CONTROL-SHOOTP")
    controls = [i for i in instrs if i[1] == "control"]
    assert len(controls) == 1
    (_, _, ops) = controls[0]
    assert ops[0] == "shootp"
    # sub + block + unit + shoot + 2 zero pads = 6 tokens.
    assert len(ops) == 6
    assert ops[-2:] == ("0", "0")


# ---------------------------------------------------------------------------
# mlog interpreter dispatch
# ---------------------------------------------------------------------------


def test_interpreter_control_enabled_invokes_world_control() -> None:
    """Compiled `control enabled cv1 1 0 0 0` must reach world.control()."""
    from mforth.mlog_interp import MlogInterpreter

    world = MockWorld()
    world.add_link(Block.switch("cv1", on=False))
    text = (
        "# header\n"
        "control enabled cv1 1 0 0 0\n"
        "end\n"
    )
    interp = MlogInterpreter(world=world, text=text)
    interp.run(iterations=1)
    evs = [e for e in world.events if isinstance(e, ControlEvent)]
    assert len(evs) == 1
    assert evs[0].op == "enabled"
    assert evs[0].block_name == "cv1"
    assert evs[0].args == (1,)
    assert world.lookup_block("cv1").state["on"] is True


def test_interpreter_control_config_invokes_world_control() -> None:
    from mforth.mlog_interp import MlogInterpreter

    world = MockWorld()
    world.add_link(Block.generic("sorter1"))
    text = (
        "# header\n"
        "control config sorter1 @copper 0 0 0\n"
        "end\n"
    )
    interp = MlogInterpreter(world=world, text=text)
    interp.run(iterations=1)
    evs = [e for e in world.events if isinstance(e, ControlEvent)]
    assert len(evs) == 1
    assert evs[0].op == "config"
    assert evs[0].args == ("@copper",)


def test_interpreter_control_shoot_invokes_world_control() -> None:
    from mforth.mlog_interp import MlogInterpreter

    world = MockWorld()
    world.add_link(Block.generic("turret1"))
    text = (
        "# header\n"
        "control shoot turret1 50 60 1\n"
        "end\n"
    )
    interp = MlogInterpreter(world=world, text=text)
    interp.run(iterations=1)
    evs = [e for e in world.events if isinstance(e, ControlEvent)]
    assert len(evs) == 1
    assert evs[0].op == "shoot"
    assert evs[0].args == (50, 60, 1)


def test_interpreter_unknown_control_subcommand_raises() -> None:
    """A typo'd sub-command should raise a clear MlogInterpError rather
    than silently emit nothing."""
    from mforth.mlog_interp import MlogInterpError, MlogInterpreter

    world = MockWorld()
    text = "control bogus cv1 0 0 0 0\nend\n"
    interp = MlogInterpreter(world=world, text=text)
    with pytest.raises(MlogInterpError, match="control"):
        interp.run(iterations=1)
