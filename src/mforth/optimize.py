"""Optimization-level orchestrator for the mforth mlog backend (bead mforth-10t.40).

This module wires the seven already-unit-tested optimization passes into
the four optimization levels the CLI exposes — ``-O0``, ``-O1``,
``-Ofast`` (the CLI default), and ``-Osize`` — and threads an
``opt_level`` parameter through a single library compile entry point.

Why a separate orchestrator (and why the library default is O0)
==============================================================

The headline mforth property is REPL ↔ mlog **event-stream equivalence**
(CLAUDE.md hard rule). The in-repo mlog interpreter emits
``VariableReadEvent`` / ``VariableWriteEvent`` for every source ``VARIABLE``
read/write so the compiled output matches the host REPL's instrumentation
event-for-event. CSE and LICM legitimately ELIDE redundant ``@`` fetches,
so an optimized program emits FEWER instrumentation events than the REPL —
a *deliberate*, *correct* divergence on the instrumentation channel.

The project decision (bead ``mforth-ump``, Option A) resolves this:

* **STRICT teaching equivalence** (the property harness + fixture
  equivalence + golden harness) compiles at **O0** so the compiled output
  stays byte/event-identical to the REPL. Therefore every *library* compile
  entry point defaults to ``OptLevel.O0`` — existing tests stay unchanged
  and GREEN.
* Only the **CLI** defaults to ``-Ofast``.
* Optimized levels are validated by a *behavior*-equivalence test that
  compares SINK events (print / world-control / printflush / wait) across
  O0 and Ofast while ALLOWING the VariableRead/VariableWrite counts to
  differ.

Level → pass mapping
====================

============  ==================================================================
Level         Passes (in pipeline-stage order)
============  ==================================================================
``O0``        none
``O1``        AST: fold → dce              ; post-emit: dead-copy → peephole
``Ofast``     O1 + AST: cse → licm
``Osize``     Ofast + ``@counter`` subroutine emission (size fallback)
============  ==================================================================

AST pass order is **fold → dce → cse → licm** (fold first so dce sees the
literal flags it prunes on). After the AST passes run, stackcheck is
re-run — a transformed AST must stay stack-valid before it reaches the
slot allocator (CLAUDE.md: "Re-run stackcheck after AST passes").

The post-emit slot/peephole passes are *opt-in* equivalence-preserving
transforms over the emitted instruction stream (``(label, opcode,
operands)`` tuples). They are NOT run at O0.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from mforth.backend.mlog.emit import emit
from mforth.backend.mlog.finalize import (
    emit_prologue,
    resolve_labels,
    substitute_sidecar,
    write_mlog,
)
from mforth.backend.mlog.peephole import peephole
from mforth.backend.mlog.slots import allocate_slots, eliminate_dead_copies
from mforth.backend.mlog.subroutines import (
    SubroutineConfig,
    emit_with_subroutines,
    resolve_subroutine_labels,
)
from mforth.backend.sidecar import WorldConfig
from mforth.cse import cse_program
from mforth.dce import dead_code_eliminate
from mforth.dictionary import Dictionary, standard_dictionary
from mforth.fold import fold_constants
from mforth.licm import licm
from mforth.parse import Program
from mforth.stackcheck import StackcheckResult, stackcheck


# ---------------------------------------------------------------------------
# Optimization levels
# ---------------------------------------------------------------------------


class OptLevel:
    """Integer optimization levels, ordered so ``>=`` gates pass inclusion.

    The numeric values are an internal ordering, NOT the CLI spelling —
    the CLI flags are ``-O0`` / ``-O1`` / ``-Ofast`` / ``-Osize`` and map
    onto these via :func:`from_flag`.
    """

    O0 = 0
    O1 = 1
    OFAST = 2
    OSIZE = 3


# CLI flag spelling → internal level. The default CLI flag is ``-Ofast``.
_FLAG_TO_LEVEL: dict[str, int] = {
    "O0": OptLevel.O0,
    "O1": OptLevel.O1,
    "Ofast": OptLevel.OFAST,
    "Osize": OptLevel.OSIZE,
}
_LEVEL_TO_FLAG: dict[int, str] = {v: k for k, v in _FLAG_TO_LEVEL.items()}


def from_flag(flag: str) -> int:
    """Map a CLI flag spelling (``"O0"``, ``"Ofast"``, …) to an
    :class:`OptLevel` integer. Accepts an optional leading ``-``."""
    key = flag[1:] if flag.startswith("-") else flag
    if key not in _FLAG_TO_LEVEL:
        raise ValueError(
            f"unknown optimization level {flag!r}; "
            f"expected one of {sorted('-' + k for k in _FLAG_TO_LEVEL)}"
        )
    return _FLAG_TO_LEVEL[key]


def level_name(level: int) -> str:
    """Return the canonical flag spelling for an :class:`OptLevel`
    integer (``OptLevel.OFAST`` → ``"Ofast"``)."""
    return _LEVEL_TO_FLAG.get(level, f"O?({level})")


# ---------------------------------------------------------------------------
# AST-level optimization (pre-codegen)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OptimizedAst:
    """Result of running the AST-stage passes.

    Attributes
    ----------
    program
        The transformed (still stack-valid) program.
    dictionary
        The dictionary, possibly with dead user-definitions evicted by
        DCE. The codegen pass shares this dictionary, so a later
        resolution never points at a definition the optimizer removed.
    result
        The :class:`StackcheckResult` of re-checking ``program`` against
        ``dictionary`` — codegen consumes this directly so the caller
        need not re-stackcheck.
    """

    program: Program
    dictionary: Dictionary
    result: StackcheckResult


def optimize_ast(
    program: Program,
    level: int,
    dictionary: Optional[Dictionary] = None,
) -> OptimizedAst:
    """Run the AST-stage optimization passes appropriate for ``level``.

    Pass order is **fold → dce → cse → licm**, each gated on ``level``:

    * ``>= O1``: ``fold_constants`` then ``dead_code_eliminate``.
    * ``>= OFAST``: additionally ``cse_program`` then ``licm``.

    After the passes run, stackcheck is re-run so the returned
    :class:`StackcheckResult` reflects the transformed AST — a
    transformed program must stay stack-valid before codegen
    (CLAUDE.md). The input ``program`` and ``dictionary`` are not
    mutated by the AST passes (each returns a fresh ``Program``); DCE may
    evict dead definitions from the dictionary, so we hand it a copy.
    """
    d = dictionary if dictionary is not None else standard_dictionary()
    # DCE mutates the dictionary it is given (evicting dead defs). Copy so
    # the caller's dictionary is never disturbed by an optimizer side
    # effect — the codegen path gets the (possibly-trimmed) copy via the
    # returned StackcheckResult.dictionary instead.
    d = d.copy() if hasattr(d, "copy") else d

    if level >= OptLevel.O1:
        program = fold_constants(program)
        program = dead_code_eliminate(program, dictionary=d)
    if level >= OptLevel.OFAST:
        program = cse_program(program, dictionary=d)
        program = licm(program, dictionary=d)

    # Re-run stackcheck on the (possibly transformed) AST. At O0 this is a
    # no-op transform but a real re-check; harmless and keeps one code
    # path. Any optimizer bug that produces a stack-invalid program
    # surfaces HERE as a StackError rather than as silent miscodegen.
    result = stackcheck(program, dictionary=d)
    return OptimizedAst(program=program, dictionary=d, result=result)


# ---------------------------------------------------------------------------
# Post-emit (instruction-stream) optimization
# ---------------------------------------------------------------------------


def optimize_instrs(instrs: list, level: int) -> list:
    """Run the post-emit instruction-stream passes for ``level``.

    At ``>= O1``: dead-copy elimination (slot liveness + dead-store drop +
    slot reuse) then peephole collapse. Both are equivalence-preserving by
    construction (see their module docstrings) — the returned stream
    yields an identical observable SINK-event sequence under the in-repo
    interpreter.

    At ``O0`` the input list is returned unchanged.
    """
    if level < OptLevel.O1:
        return list(instrs)
    instrs = eliminate_dead_copies(instrs)
    instrs = peephole(instrs)
    return instrs


# ---------------------------------------------------------------------------
# End-to-end optimized emit (AST passes → emit → post-emit passes)
# ---------------------------------------------------------------------------


def optimize_and_emit(
    result: StackcheckResult,
    level: int,
    *,
    subroutine_config: Optional[SubroutineConfig] = None,
    force_promote: Optional[list] = None,
) -> list:
    """Apply AST + codegen + post-emit optimization at ``level`` and
    return the emitted (still symbolic-label) instruction stream.

    Parameters
    ----------
    result
        A clean :class:`StackcheckResult` (parse → resolve → stackcheck).
    level
        An :class:`OptLevel` integer.
    subroutine_config / force_promote
        Forwarded to :func:`emit_with_subroutines` at ``OSIZE``.

    Returns
    -------
    list
        Instruction tuples with symbolic labels still present —
        :func:`finalize_optimized` (or the caller's finalize chain)
        resolves them. ``OSIZE`` output may carry ``set @counter <label>``
        / ``set __ret_* <label>`` operands that require the
        subroutine-aware resolver.
    """
    opt = optimize_ast(result.program, level, result.dictionary)

    if level >= OptLevel.OSIZE:
        # Subroutine emission is the size fallback. It re-fuses,
        # re-stackchecks, and re-allocates internally, so feed it the
        # optimized StackcheckResult. The post-emit slot/peephole passes
        # are NOT run on the subroutine stream: their slot-liveness model
        # does not account for the @counter call/return control flow, so
        # running them could elide a slot that survives a subroutine
        # round-trip. The AST-level Tier-B passes already ran above.
        return emit_with_subroutines(
            opt.result,
            config=subroutine_config,
            force_promote=force_promote,
        )

    instrs = emit(opt.result)
    instrs = optimize_instrs(instrs, level)
    return instrs


# ---------------------------------------------------------------------------
# Finalize (label resolution aware of subroutine @counter operands)
# ---------------------------------------------------------------------------


def finalize_optimized(
    instrs: list,
    level: int,
    *,
    world_config: WorldConfig,
    source_path: Union[str, Path],
    sidecar_path: Optional[Union[str, Path]] = None,
    emit_comments: bool = False,
) -> str:
    """Render an optimized instruction stream to canonical mlog text.

    Mirrors :func:`mforth.backend.mlog.finalize.finalize` (sidecar
    substitution → prologue → label resolution → write) but, at ``OSIZE``,
    swaps the stock jump-only :func:`resolve_labels` for
    :func:`resolve_subroutine_labels`, which ALSO rewrites the
    ``set @counter <label>`` / ``set __ret_* <label>`` operands the
    subroutine emitter produces. For all other levels the standard
    jump-only resolver is correct and is used unchanged.
    """
    source_path = Path(source_path)
    sidecar_path = Path(sidecar_path) if sidecar_path is not None else None

    substituted = substitute_sidecar(
        instrs, world_config, source=str(sidecar_path or source_path)
    )
    with_prologue = emit_prologue(substituted, world_config)

    if level >= OptLevel.OSIZE:
        resolved = resolve_subroutine_labels(with_prologue)
    else:
        resolved = resolve_labels(with_prologue)

    return write_mlog(
        resolved,
        source_path=source_path,
        sidecar_path=sidecar_path,
        emit_comments=emit_comments,
    )


# ---------------------------------------------------------------------------
# The single library compile entry point — DEFAULTS TO O0.
# ---------------------------------------------------------------------------


def compile_text(
    text: str,
    *,
    opt_level: int = OptLevel.O0,
    world_config: Optional[WorldConfig] = None,
    dictionary: Optional[Dictionary] = None,
    source_path: Union[str, Path] = "<string>",
    sidecar_path: Optional[Union[str, Path]] = None,
    emit_comments: bool = False,
    subroutine_config: Optional[SubroutineConfig] = None,
    force_promote: Optional[list] = None,
) -> str:
    """Compile mforth source ``text`` to finalized mlog text.

    **The library default is ``OptLevel.O0``** — no passes — so callers
    that don't opt in (including the equivalence harness and the golden
    tests) get byte/event-identical-to-the-REPL output. The CLI passes
    ``-Ofast`` explicitly.

    Pipeline::

        parse → resolve → stackcheck
              → optimize_ast (AST passes, re-stackcheck)
              → emit → optimize_instrs (post-emit passes)
              → finalize (label resolution + write)

    Parameters
    ----------
    text
        The ``.fs`` source.
    opt_level
        An :class:`OptLevel` integer (default ``O0``).
    world_config
        Parsed sidecar. Defaults to an empty :class:`WorldConfig`.
    dictionary
        A pre-seeded dictionary (e.g. with sidecar link names). Defaults
        to a fresh :func:`standard_dictionary`.
    source_path / sidecar_path / emit_comments
        Forwarded to the finalize stage (header + Mode A/B handling).
    subroutine_config / force_promote
        ``OSIZE`` knobs forwarded to the subroutine emitter.
    """
    from mforth.dictionary import resolve  # local import to avoid a cycle
    from mforth.parse import parse

    world_config = world_config if world_config is not None else WorldConfig()
    dictionary = dictionary if dictionary is not None else standard_dictionary()

    program = parse(text, file=str(source_path))
    dictionary = resolve(program, dictionary=dictionary)
    result = stackcheck(program, dictionary=dictionary)

    instrs = optimize_and_emit(
        result,
        opt_level,
        subroutine_config=subroutine_config,
        force_promote=force_promote,
    )

    return finalize_optimized(
        instrs,
        opt_level,
        world_config=world_config,
        source_path=source_path,
        sidecar_path=sidecar_path,
        emit_comments=emit_comments,
    )


__all__ = [
    "OptLevel",
    "OptimizedAst",
    "from_flag",
    "level_name",
    "optimize_ast",
    "optimize_instrs",
    "optimize_and_emit",
    "finalize_optimized",
    "compile_text",
]
