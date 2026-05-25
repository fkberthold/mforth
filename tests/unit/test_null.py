"""Unit tests for the NULL literal — bead mforth-l8z.

Pins the contract for the v1 ``NULL`` Forth word that gives source-level
access to mlog's null sentinel. Surfaced 2026-05-23 while porting USR's
Sorter Picker (~/wiki/Mindustry.md), which uses

    set unload null
    control config unloader1 unload 0 0 0

to stop unloading when both upstream supplies are exhausted. Without
NULL, the full Sorter Picker logic couldn't be ported in mforth v1.

Surfaces pinned here:

* Dictionary registration (NULL with StackEffect(0, 1), tag
  ``mindustry``).
* Host primitive pushes :data:`NULL_VALUE` sentinel.
* ``str(NULL_VALUE)`` renders ``"null"`` — so PRINT/dot formatting
  routes through naturally.
* mlog backend: ``NULL`` in slot-form lowers to ``set s<i> null``.
* CONTROL-ENABLED / CONTROL-CONFIG lifters accept NULL as a value
  source → ``control <sub> <block> null 0 0 0``.
* mlog interpreter resolves the bare ``null`` operand back to
  :data:`NULL_VALUE` (load-bearing for REPL ↔ mlog equivalence — the
  ControlEvent.args tuple must match across backends).
* Negative cases: NULL is rejected as a block operand (only the value
  side accepts it).
"""

from __future__ import annotations

import pytest

from mforth.backend.host import Executor
from mforth.backend.mlog.emit import emit
from mforth.backend.mlog.slots import allocate_slots
from mforth.backend.world import (
    NULL_VALUE,
    Block,
    ControlEvent,
    MessagePrintEvent,
    MockWorld,
)
from mforth.dictionary import BuiltinWord, StackEffect, standard_dictionary
from mforth.mlog_interp import MlogInterpreter, _format_for_print, _parse_literal
from mforth.parse import parse
from mforth.stackcheck import stackcheck


# ---------------------------------------------------------------------------
# Dictionary registration
# ---------------------------------------------------------------------------


def test_dictionary_registers_null() -> None:
    d = standard_dictionary()
    entry = d.lookup("NULL")
    assert isinstance(entry, BuiltinWord)
    assert entry.stack_effect == StackEffect(0, 1)
    # Tag aligns with the rest of the Mindustry surface so LSP grouping
    # / docs / completion treat it consistently.
    assert entry.tag == "mindustry"


# ---------------------------------------------------------------------------
# NULL_VALUE singleton — identity + str rendering
# ---------------------------------------------------------------------------


def test_null_value_is_singleton() -> None:
    # The host primitive and the mlog interpreter must hand back the
    # SAME object so equivalence comparisons see identical tuple
    # payloads in ControlEvent.args.
    from mforth.backend import world as world_mod

    assert NULL_VALUE is world_mod.NULL_VALUE


def test_null_value_str_renders_null() -> None:
    assert str(NULL_VALUE) == "null"


def test_null_value_repr_is_legible() -> None:
    # A debug surface — when a test failure dumps an event list with a
    # NULL inside, the repr should make it obvious which value is the
    # sentinel rather than printing a bare 'null' that looks like a
    # variable name.
    assert "null" in repr(NULL_VALUE).lower()


def test_null_value_is_falsy() -> None:
    # Convenient property for `IF NULL THEN`-style branching — Python's
    # `if NULL_VALUE` should be falsy so the host stays consistent with
    # mlog's `jump notEqual <slot> null` semantics (null behaves like
    # 0 for branching).
    assert not NULL_VALUE


# ---------------------------------------------------------------------------
# Host primitive — pushes the sentinel, PRINT/dot render "null"
# ---------------------------------------------------------------------------


def _run(source: str, *blocks: Block) -> Executor:
    """Parse, stackcheck, register primitives, execute. Mirrors the
    pattern in tests/unit/test_control.py::_run_source."""
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
    for b in blocks:
        executor.register_primitive(
            b.name,
            (lambda name: lambda ex: ex.data_stack.append(name))(b.name),
        )
    result = stackcheck(program, dictionary=dictionary)
    executor.execute(result)
    return executor


def test_host_null_pushes_sentinel_on_stack() -> None:
    ex = _run("NULL")
    assert len(ex.data_stack) == 1
    assert ex.data_stack[0] is NULL_VALUE


def test_host_print_null_emits_null_text() -> None:
    ex = _run('NULL PRINT')
    prints = [e for e in ex.world.events if isinstance(e, MessagePrintEvent)]
    assert len(prints) == 1
    assert prints[0].text == "null"


def test_host_dot_null_emits_null_text() -> None:
    # `.` (pop-and-print) is host-REPL-only per mforth-va2; still must
    # format NULL as "null" for consistency with PRINT.
    ex = _run("NULL .")
    prints = [e for e in ex.world.events if isinstance(e, MessagePrintEvent)]
    assert len(prints) == 1
    assert prints[0].text == "null"


# ---------------------------------------------------------------------------
# CONTROL-CONFIG / CONTROL-ENABLED with NULL value (host side)
# ---------------------------------------------------------------------------


def test_host_control_config_with_null_emits_control_event() -> None:
    ex = _run(
        "unloader1 NULL CONTROL-CONFIG",
        Block.generic("unloader1"),
    )
    controls = [e for e in ex.world.events if isinstance(e, ControlEvent)]
    assert len(controls) == 1
    assert controls[0].op == "config"
    assert controls[0].block_name == "unloader1"
    assert controls[0].args == (NULL_VALUE,)


def test_host_control_enabled_with_null_emits_control_event() -> None:
    ex = _run(
        "blk1 NULL CONTROL-ENABLED",
        Block.generic("blk1"),
    )
    controls = [e for e in ex.world.events if isinstance(e, ControlEvent)]
    assert len(controls) == 1
    assert controls[0].op == "enabled"
    assert controls[0].block_name == "blk1"
    assert controls[0].args == (NULL_VALUE,)


# ---------------------------------------------------------------------------
# mlog emit — slot-form + lifted-form
# ---------------------------------------------------------------------------


def _emit_source(source: str, *link_names: str) -> list:
    """Mirrors tests/unit/test_control.py::_emit_source."""
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


def test_emit_null_slot_form() -> None:
    # `VARIABLE myvar NULL myvar !` — the slot-form path. NULL stages
    # a literal "null" into a slot, then `myvar !` (fused to VarRef
    # store) reads that slot. We use VARIABLE/! because the dot
    # primitive `.` is still host-REPL-only (mforth-va2).
    instrs = _emit_source("VARIABLE myvar NULL myvar !")
    sets = [i for i in instrs if i[1] == "set"]
    assert any(
        len(ops) >= 2 and ops[0].startswith("s") and ops[1] == "null"
        for (_, _, ops) in sets
    ), f"no `set s<i> null` in {instrs!r}"


def test_emit_control_config_with_null_lifted() -> None:
    # `<uservar> NULL CONTROL-CONFIG` — lifted path. Bare `null` as
    # the value operand, no preceding `set s<i> null`.
    instrs = _emit_source("unloader1 NULL CONTROL-CONFIG", "unloader1")

    controls = [i for i in instrs if i[1] == "control"]
    assert len(controls) == 1
    assert controls[0][2] == ("config", "unloader1", "null", "0", "0", "0")
    # And no `set s<i> null` since the lift bypasses slot staging.
    assert not any(
        i[1] == "set" and len(i[2]) >= 2 and i[2][1] == "null"
        for i in instrs
    ), f"lifted form should not stage null into a slot: {instrs!r}"


def test_emit_control_enabled_with_null_lifted() -> None:
    instrs = _emit_source("blk1 NULL CONTROL-ENABLED", "blk1")
    controls = [i for i in instrs if i[1] == "control"]
    assert len(controls) == 1
    assert controls[0][2] == ("enabled", "blk1", "null", "0", "0", "0")


def test_emit_control_config_with_litstr_block_and_null_value() -> None:
    # LitStr block, NULL value — confirms the lifter accepts NULL
    # alongside the existing LitStr/LitInt/@-id value sources.
    instrs = _emit_source('S" unloader1" NULL CONTROL-CONFIG')
    controls = [i for i in instrs if i[1] == "control"]
    assert len(controls) == 1
    assert controls[0][2] == ("config", "unloader1", "null", "0", "0", "0")


# ---------------------------------------------------------------------------
# mlog interpreter — resolves bare "null" token to NULL_VALUE
# ---------------------------------------------------------------------------


def test_mlog_interp_read_null_token_returns_null_value() -> None:
    # Load-bearing: the host pushes NULL_VALUE; the mlog backend emits
    # the bare `null` operand. For REPL ↔ mlog equivalence, the
    # interpreter's _read of that operand must produce the same
    # NULL_VALUE singleton so ControlEvent.args matches across
    # backends.
    interp = MlogInterpreter(world=MockWorld(), text="")
    assert interp._read("null") is NULL_VALUE


def test_mlog_format_for_print_null_value() -> None:
    assert _format_for_print(NULL_VALUE) == "null"


def test_parse_literal_null_token_is_bare_name() -> None:
    # Documentary: _parse_literal still treats "null" as a bare
    # identifier; the special-case happens at _read, not at
    # _parse_literal. This pins the layering — anyone reaching for
    # _parse_literal directly (the few tests that do) sees the bare
    # string, NOT the sentinel.
    assert _parse_literal("null") == "null"


# ---------------------------------------------------------------------------
# Negative cases
# ---------------------------------------------------------------------------


def test_null_rejected_as_control_block_operand() -> None:
    # `NULL <value> CONTROL-CONFIG` — NULL on the block side is
    # nonsense (you can't config the null block). The lifter should
    # refuse to fire, and the slot-form fallback should NOT silently
    # accept it either — the bare-uservar codegen guard should trip
    # first (NULL is a BuiltinWord, not a UserVariable, so the
    # cell-free guard at emit catches the impossible push-of-NULL-as-
    # block-handle).
    #
    # We allow either NotImplementedError (cell-free guard) or a
    # stackcheck pass + lifter-bypass that produces some mlog — as
    # long as the produced mlog doesn't pretend NULL is a valid block
    # name in the `control` instruction.
    instrs = _emit_source("NULL @copper CONTROL-CONFIG")

    controls = [i for i in instrs if i[1] == "control"]
    # If a control instruction is emitted, the block operand must NOT
    # be literal "null" — that would silently accept a nonsense
    # program.
    for ctrl in controls:
        # ctrl[2] = (sub, block, value, ...)
        assert ctrl[2][1] != "null", (
            f"NULL must not lift as the block operand: {ctrl!r}"
        )


def test_null_word_case_insensitive_lookup() -> None:
    # mforth's Dictionary is case-insensitive (every entry stored
    # `.lower()`'d) — `NULL`, `null`, and `Null` all resolve to the
    # same BuiltinWord. We document the source-level convention as
    # uppercase NULL (Forth tradition) but the dictionary doesn't
    # enforce it. mlog emit always lowers to the bare `null` token.
    d = standard_dictionary()
    upper = d.lookup("NULL")
    lower = d.lookup("null")
    assert upper is not None
    assert lower is upper
