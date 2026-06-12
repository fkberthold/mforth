"""Unit tests for the optimization-level orchestrator (bead mforth-10t.40).

Pins the contract of ``mforth.optimize``:

* The level mapping (``-O0`` / ``-O1`` / ``-Ofast`` / ``-Osize``) round-trips
  through :func:`from_flag` / :func:`level_name`.
* The library compile entry (:func:`compile_text`) DEFAULTS to ``O0`` and at
  ``O0`` produces byte-identical output to the stock inline
  ``emit → finalize`` pipeline the equivalence + golden harnesses use. This
  is the load-bearing guarantee that wiring the orchestrator did not perturb
  the headline-equivalence path.
* Each level emits *valid, parseable* mlog and a higher level never emits
  MORE static instructions than ``O0`` for an optimizable program.
"""

from __future__ import annotations

import pytest

from mforth import optimize
from mforth.backend.mlog.emit import emit
from mforth.backend.mlog.finalize import finalize
from mforth.backend.mlog.slots import allocate_slots
from mforth.backend.sidecar import WorldConfig
from mforth.dictionary import UserVariable, resolve, standard_dictionary
from mforth.optimize import OptLevel, compile_text, from_flag, level_name
from mforth.parse import SrcLoc, parse
from mforth.stackcheck import stackcheck


def _seeded_dictionary():
    """A standard dictionary pre-seeded with the ``display`` link name —
    mirrors how the runner / cli_compile pre-seed sidecar link names so a
    bare ``display PRINTFLUSH`` resolves the way it does in a real compile."""
    d = standard_dictionary()
    d.add_variable(UserVariable(name="display", src_loc=SrcLoc("<test>", 1, 1)))
    return d


# ---------------------------------------------------------------------------
# Level mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "flag,level",
    [
        ("O0", OptLevel.O0),
        ("-O0", OptLevel.O0),
        ("O1", OptLevel.O1),
        ("Ofast", OptLevel.OFAST),
        ("Osize", OptLevel.OSIZE),
    ],
)
def test_from_flag_maps_spelling_to_level(flag, level):
    assert from_flag(flag) == level


def test_from_flag_rejects_unknown():
    with pytest.raises(ValueError):
        from_flag("-O9")


def test_level_name_round_trips():
    for flag in ("O0", "O1", "Ofast", "Osize"):
        assert level_name(from_flag(flag)) == flag


def test_levels_are_ordered():
    assert OptLevel.O0 < OptLevel.O1 < OptLevel.OFAST < OptLevel.OSIZE


# ---------------------------------------------------------------------------
# Library default is O0 + O0 is byte-identical to the stock inline pipeline
# ---------------------------------------------------------------------------


def _stock_pipeline(text: str, *, source_path="<string>") -> str:
    """The inline pipeline the equivalence + golden harnesses use, with no
    optimization — the byte-for-byte reference for O0."""
    program = parse(text, file=str(source_path))
    dictionary = resolve(program, dictionary=_seeded_dictionary())
    result = stackcheck(program, dictionary=dictionary)
    slots = allocate_slots(result)
    instrs = emit(result, slots)
    return finalize(
        instrs,
        world_config=WorldConfig(),
        source_path=source_path,
        sidecar_path=None,
    )


def _compile(src, **kw):
    """``compile_text`` with the display-seeded dictionary."""
    return compile_text(src, dictionary=_seeded_dictionary(), **kw)


_PROGRAMS = [
    "1 2 + PRINT\ndisplay PRINTFLUSH\n",
    "5 2 / PRINT\ndisplay PRINTFLUSH\n",
    "VARIABLE x\n3 x !\nx @ PRINT\ndisplay PRINTFLUSH\n",
    "0 3 DO I PRINT LOOP\ndisplay PRINTFLUSH\n",
    ": sq DUP * ;\n4 sq PRINT\ndisplay PRINTFLUSH\n",
]


@pytest.mark.parametrize("src", _PROGRAMS)
def test_compile_text_defaults_to_O0(src):
    """The library default level is O0 — calling without ``opt_level`` must
    match an explicit O0 call AND the stock inline pipeline byte-for-byte."""
    default = _compile(src)
    explicit_o0 = _compile(src, opt_level=OptLevel.O0)
    assert default == explicit_o0
    assert default == _stock_pipeline(src)


# ---------------------------------------------------------------------------
# Every level emits valid mlog; higher levels never bloat static count.
# ---------------------------------------------------------------------------


def _instr_count(mlog_text: str) -> int:
    """Count executable instruction lines (skip the header `#` comment)."""
    return sum(
        1
        for line in mlog_text.splitlines()
        if line and not line.lstrip().startswith("#")
    )


@pytest.mark.parametrize("src", _PROGRAMS)
@pytest.mark.parametrize(
    "level",
    [OptLevel.O0, OptLevel.O1, OptLevel.OFAST, OptLevel.OSIZE],
)
def test_every_level_emits_nonempty_mlog(src, level):
    text = _compile(src, opt_level=level)
    # Header line always present; at least one executable line for these
    # non-trivial programs.
    assert text.endswith("\n")
    assert _instr_count(text) >= 1


@pytest.mark.parametrize("src", _PROGRAMS)
def test_ofast_never_larger_than_o0(src):
    """fast > small, but Tier A/B must never INCREASE the static instruction
    count relative to O0 for these programs (they fold/elide, never bloat)."""
    o0 = _instr_count(_compile(src, opt_level=OptLevel.O0))
    ofast = _instr_count(_compile(src, opt_level=OptLevel.OFAST))
    assert ofast <= o0


def test_const_fold_shrinks_arithmetic():
    """A pure constant expression collapses under O1+ (fold) to fewer
    instructions than O0's full stack-machine lowering."""
    src = "2 3 + 4 * PRINT\ndisplay PRINTFLUSH\n"
    o0 = _instr_count(_compile(src, opt_level=OptLevel.O0))
    o1 = _instr_count(_compile(src, opt_level=OptLevel.O1))
    assert o1 < o0
