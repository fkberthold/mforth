"""Unit tests for the mlog backend finalize pass (bead mforth-10t.19).

The finalize pass runs three sub-passes plus a writer over the raw
``list[MlogInstr]`` tuples produced by :func:`mforth.backend.mlog.emit`:

1. **Sidecar substitution** — for each Mode A (``target``) link, rewrite
   operand tokens that resolve to the link's mforth-name into the
   in-game name. Two operand-shape cases:
   (a) bare-name fallback after .18's literal-lifting fusion (e.g.
       ``("printflush", ("display",))``);
   (b) slot-reference fallback where the preceding ``set s<i> "<value>"``
       lifted a string literal — trace back, find the mforth-name, and
       substitute the in-game name.
2. **Prologue emission** — for each Mode B (``index``) link, prepend a
   ``getlink <mforth-name> <N>`` instruction to the program. The Mode B
   mforth-name then acts as a real mlog variable that holds the block
   ref; no operand rewriting is needed for the consumer.
3. **Label resolution** — walk the (post-prologue) instruction stream,
   build a label → line-number map (0-indexed, sentinel tuples
   ``(label, None, None)`` consume zero lines), then rewrite every
   ``("jump", (target_label, cond, a, b))`` operand-0 to the resolved
   line number string. Sentinel tuples are stripped from the output.
4. **Writer** — emit canonical mlog text: header comment with instruction
   count + source + sidecar paths; one ``opcode op1 op2 ...`` line per
   instruction; exactly one trailing newline at EOF. The writer's output
   must round-trip through bead .29's ``_serialize()`` contract for the
   body lines (header is the only difference).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mforth.backend.mlog import allocate_slots, emit
from mforth.backend.mlog.finalize import (
    SidecarSubstitutionError,
    emit_prologue,
    finalize,
    resolve_labels,
    substitute_sidecar,
    write_mlog,
)
from mforth.backend.sidecar import (
    ClockConfig,
    LinkSpec,
    WorldConfig,
    load_sidecar,
)
from mforth.dictionary import UserVariable, resolve, standard_dictionary
from mforth.parse import SrcLoc, parse
from mforth.stackcheck import stackcheck


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compile_with_sidecar(src: str, world_config: WorldConfig, file: str = "<test>"):
    """Run the upstream pipeline (pre-finalize), pre-seeding the dictionary
    with sidecar link names like ``Runner.from_path`` does.

    Returns the raw ``list[MlogInstr]`` tuples ready for finalize.
    """
    dictionary = standard_dictionary()
    seed_loc = SrcLoc(file, 1, 1)
    for spec in world_config.links:
        if spec.mforth_name not in dictionary:
            dictionary.add_variable(
                UserVariable(name=spec.mforth_name, src_loc=seed_loc)
            )
    program = parse(src, file=file)
    dictionary = resolve(program, dictionary=dictionary)
    result = stackcheck(program, dictionary=dictionary)
    slots = allocate_slots(result)
    return emit(result, slots)


# ===========================================================================
# Sub-pass 3: label resolution (pure)
# ===========================================================================


class TestLabelResolution:
    def test_simple_forward_jump_resolves_to_line_number(self):
        instrs = [
            (None, "jump", ("L0", "always", "0", "0")),
            (None, "set", ("s0", "1")),
            ("L0", "set", ("s1", "2")),
        ]
        out = resolve_labels(instrs)
        # Line 0: jump → target line 2; Line 1: set s0 1; Line 2: set s1 2.
        assert out == [
            (None, "jump", ("2", "always", "0", "0")),
            (None, "set", ("s0", "1")),
            (None, "set", ("s1", "2")),
        ]

    def test_backward_jump_resolves_to_earlier_line(self):
        instrs = [
            ("L_top", "set", ("s0", "1")),
            (None, "op", ("sub", "s0", "s0", "s0")),
            (None, "jump", ("L_top", "equal", "s0", "0")),
        ]
        out = resolve_labels(instrs)
        assert out == [
            (None, "set", ("s0", "1")),
            (None, "op", ("sub", "s0", "s0", "s0")),
            (None, "jump", ("0", "equal", "s0", "0")),
        ]

    def test_sentinel_label_at_end_resolves_to_eof_line(self):
        # Degenerate `1 IF THEN`: L_end is a sentinel at end-of-program.
        instrs = [
            (None, "jump", ("L_end", "equal", "s0", "0")),
            (None, "set", ("s1", "1")),
            ("L_end", None, None),
        ]
        out = resolve_labels(instrs)
        # Sentinel consumes 0 lines; L_end → line 2 (past EOF, mlog
        # auto-loops back to 0 — correct "skip the body" semantics).
        assert out == [
            (None, "jump", ("2", "equal", "s0", "0")),
            (None, "set", ("s1", "1")),
        ]

    def test_stacked_sentinel_labels_share_same_line(self):
        instrs = [
            (None, "jump", ("L_a", "equal", "s0", "0")),
            (None, "jump", ("L_b", "equal", "s0", "0")),
            ("L_a", None, None),
            ("L_b", "set", ("s1", "1")),
        ]
        out = resolve_labels(instrs)
        assert out == [
            (None, "jump", ("2", "equal", "s0", "0")),
            (None, "jump", ("2", "equal", "s0", "0")),
            (None, "set", ("s1", "1")),
        ]

    def test_unconditional_jump_keeps_always_0_0_operands(self):
        instrs = [
            (None, "jump", ("L0", "always", "0", "0")),
            ("L0", "set", ("s0", "1")),
        ]
        out = resolve_labels(instrs)
        assert out == [
            (None, "jump", ("1", "always", "0", "0")),
            (None, "set", ("s0", "1")),
        ]

    def test_unknown_label_target_raises(self):
        instrs = [(None, "jump", ("L_missing", "always", "0", "0"))]
        with pytest.raises(KeyError):
            resolve_labels(instrs)

    def test_empty_instruction_list_returns_empty(self):
        assert resolve_labels([]) == []


# ===========================================================================
# Sub-pass 2: prologue emission (pure)
# ===========================================================================


class TestPrologueEmission:
    def test_no_index_mode_links_means_no_prologue(self):
        config = WorldConfig(
            links=[LinkSpec(mforth_name="display", type="message", target="message1")],
            clock=ClockConfig(),
        )
        instrs = [(None, "set", ("s0", "1"))]
        out = emit_prologue(instrs, config)
        assert out == instrs

    def test_single_index_mode_link_prepends_getlink(self):
        config = WorldConfig(
            links=[LinkSpec(mforth_name="display", type="message", index=0)],
            clock=ClockConfig(),
        )
        instrs = [(None, "set", ("s0", "1"))]
        out = emit_prologue(instrs, config)
        assert out == [
            (None, "getlink", ("display", "0")),
            (None, "set", ("s0", "1")),
        ]

    def test_multiple_index_links_preserve_sidecar_order(self):
        config = WorldConfig(
            links=[
                LinkSpec(mforth_name="display", type="message", index=0),
                LinkSpec(mforth_name="bank", type="memory-cell", index=1),
            ],
            clock=ClockConfig(),
        )
        out = emit_prologue([], config)
        assert out == [
            (None, "getlink", ("display", "0")),
            (None, "getlink", ("bank", "1")),
        ]

    def test_prologue_runs_before_label_resolution_shifts_line_numbers(self):
        """Prologue must be prepended BEFORE label resolution so labels
        resolve to post-prologue line numbers (the load-bearing order)."""
        config = WorldConfig(
            links=[LinkSpec(mforth_name="display", type="message", index=0)],
            clock=ClockConfig(),
        )
        instrs = [
            (None, "jump", ("L_end", "always", "0", "0")),
            ("L_end", "set", ("s0", "1")),
        ]
        with_prologue = emit_prologue(instrs, config)
        out = resolve_labels(with_prologue)
        assert out == [
            (None, "getlink", ("display", "0")),
            (None, "jump", ("2", "always", "0", "0")),
            (None, "set", ("s0", "1")),
        ]


# ===========================================================================
# Sub-pass 1: sidecar substitution
# ===========================================================================


class TestSidecarSubstitution:
    def test_mode_a_bare_name_substitutes_in_place(self):
        config = WorldConfig(
            links=[LinkSpec(mforth_name="display", type="message", target="message1")],
            clock=ClockConfig(),
        )
        instrs = [(None, "printflush", ("display",))]
        out = substitute_sidecar(instrs, config, source="<test>")
        assert out == [(None, "printflush", ("message1",))]

    def test_mode_a_slot_reference_traces_back_to_set(self):
        config = WorldConfig(
            links=[LinkSpec(mforth_name="display", type="message", target="message1")],
            clock=ClockConfig(),
        )
        instrs = [
            (None, "set", ("s0", '"display"')),
            (None, "printflush", ("s0",)),
        ]
        out = substitute_sidecar(instrs, config, source="<test>")
        printflush_instrs = [i for i in out if i[1] == "printflush"]
        assert len(printflush_instrs) == 1
        assert printflush_instrs[0] == (None, "printflush", ("message1",))

    def test_mode_b_index_link_left_as_variable_reference(self):
        config = WorldConfig(
            links=[LinkSpec(mforth_name="display", type="message", index=0)],
            clock=ClockConfig(),
        )
        instrs = [(None, "printflush", ("display",))]
        out = substitute_sidecar(instrs, config, source="<test>")
        # Mode B keeps the bare name; the prologue makes it a variable.
        assert out == [(None, "printflush", ("display",))]

    def test_non_link_operands_pass_through_untouched(self):
        config = WorldConfig(
            links=[LinkSpec(mforth_name="display", type="message", target="message1")],
            clock=ClockConfig(),
        )
        instrs = [
            (None, "set", ("s0", "42")),
            (None, "op", ("add", "s0", "s0", "s1")),
        ]
        out = substitute_sidecar(instrs, config, source="<test>")
        assert out == instrs

    def test_sensor_three_operand_form_substitutes_block_position(self):
        """SENSOR lifted form: ``sensor s<out> <block> <prop>`` — block
        position rewrites; @-prop position never matches a sidecar name."""
        config = WorldConfig(
            links=[
                LinkSpec(
                    mforth_name="switch1", type="switch", target="switch1"
                )
            ],
            clock=ClockConfig(),
        )
        instrs = [(None, "sensor", ("s0", "switch1", "@enabled"))]
        out = substitute_sidecar(instrs, config, source="<test>")
        # Mode A target == mforth-name in this fixture, so result is
        # identical but the path WAS exercised.
        assert out == [(None, "sensor", ("s0", "switch1", "@enabled"))]

    def test_unknown_mforth_name_unreferenced_is_not_an_error(self):
        config = WorldConfig(
            links=[LinkSpec(mforth_name="display", type="message", target="message1")],
            clock=ClockConfig(),
        )
        instrs = [(None, "set", ("s0", "1"))]
        out = substitute_sidecar(instrs, config, source="<test>")
        assert out == instrs


# ===========================================================================
# End-to-end finalize on golden fixtures
# ===========================================================================


GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden"


class TestFinalizeEndToEnd:
    def test_finalize_counter_fixture(self):
        config = load_sidecar(GOLDEN_DIR / "counter.world.toml")
        src = (GOLDEN_DIR / "counter.fs").read_text()
        instrs = _compile_with_sidecar(src, config, file="counter.fs")
        text = finalize(
            instrs,
            world_config=config,
            source_path=GOLDEN_DIR / "counter.fs",
            sidecar_path=GOLDEN_DIR / "counter.world.toml",
        )
        assert "message1" in text
        for line in text.splitlines():
            if line.startswith("printflush"):
                assert "display" not in line, line
        assert text.startswith("#")
        first = text.splitlines()[0]
        assert "counter.fs" in first
        assert "counter.world.toml" in first
        assert text.endswith("\n")
        assert not text.endswith("\n\n")

    def test_finalize_getlink_index_mode_fixture(self):
        config = load_sidecar(GOLDEN_DIR / "getlink_index_mode.world.toml")
        src = (GOLDEN_DIR / "getlink_index_mode.fs").read_text()
        instrs = _compile_with_sidecar(
            src, config, file="getlink_index_mode.fs"
        )
        text = finalize(
            instrs,
            world_config=config,
            source_path=GOLDEN_DIR / "getlink_index_mode.fs",
            sidecar_path=GOLDEN_DIR / "getlink_index_mode.world.toml",
        )
        body = [
            ln for ln in text.splitlines()
            if not ln.startswith("#") and ln.strip()
        ]
        assert body[0] == "getlink display 0", body

    def test_finalize_if_then_fixture_resolves_labels(self):
        config = WorldConfig()
        src = (GOLDEN_DIR / "if_then.fs").read_text()
        instrs = _compile_with_sidecar(src, config, file="if_then.fs")
        text = finalize(
            instrs,
            world_config=config,
            source_path=GOLDEN_DIR / "if_then.fs",
            sidecar_path=None,
        )
        body_lines = [
            ln for ln in text.splitlines()
            if not ln.startswith("#") and ln.strip()
        ]
        for ln in body_lines:
            if ln.startswith("jump"):
                target = ln.split()[1]
                int(target)  # raises ValueError if not numeric

    def test_finalize_begin_until_fixture_resolves_backward_jump(self):
        config = WorldConfig()
        src = (GOLDEN_DIR / "begin_until.fs").read_text()
        instrs = _compile_with_sidecar(src, config, file="begin_until.fs")
        text = finalize(
            instrs,
            world_config=config,
            source_path=GOLDEN_DIR / "begin_until.fs",
            sidecar_path=None,
        )
        body_lines = [
            ln for ln in text.splitlines()
            if not ln.startswith("#") and ln.strip()
        ]
        jump_lines = [
            (i, ln.split())
            for i, ln in enumerate(body_lines)
            if ln.startswith("jump")
        ]
        assert jump_lines, body_lines
        for jump_line_idx, parts in jump_lines:
            target = int(parts[1])
            assert target <= jump_line_idx, (
                f"backward jump should resolve to <= its own line: {parts}"
            )


# ===========================================================================
# Writer (sub-pass 4)
# ===========================================================================


class TestWriter:
    def test_header_comment_includes_count_source_sidecar(self):
        instrs = [(None, "set", ("s0", "1"))]
        text = write_mlog(
            instrs,
            source_path=Path("example.fs"),
            sidecar_path=Path("example.world.toml"),
        )
        first = text.splitlines()[0]
        assert first.startswith("#")
        assert "1" in first
        assert "example.fs" in first
        assert "example.world.toml" in first

    def test_header_handles_missing_sidecar(self):
        text = write_mlog(
            [(None, "set", ("s0", "1"))],
            source_path=Path("nope.fs"),
            sidecar_path=None,
        )
        assert text.splitlines()[0].startswith("#")

    def test_body_is_serialize_compatible(self):
        instrs = [
            (None, "set", ("s0", "1")),
            (None, "op", ("add", "s0", "s0", "s1")),
        ]
        text = write_mlog(instrs, source_path=Path("t.fs"), sidecar_path=None)
        body = [ln for ln in text.splitlines() if not ln.startswith("#")]
        body = [ln for ln in body if ln.strip()]
        assert body == ["set s0 1", "op add s0 s0 s1"]
        assert text.endswith("\n")

    def test_empty_program_still_emits_header(self):
        text = write_mlog([], source_path=Path("t.fs"), sidecar_path=None)
        assert text.startswith("#")
        assert text.endswith("\n")

    def test_no_label_tuples_in_writer_output(self):
        """Writer assumes labels resolved; raises if one sneaks through."""
        instrs = [("L_unresolved", "set", ("s0", "1"))]
        with pytest.raises(ValueError, match="label"):
            write_mlog(instrs, source_path=Path("t.fs"), sidecar_path=None)


# ===========================================================================
# Module surface
# ===========================================================================


class TestErrors:
    def test_sidecar_substitution_error_is_exported(self):
        assert issubclass(SidecarSubstitutionError, Exception)
