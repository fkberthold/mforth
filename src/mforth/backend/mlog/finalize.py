"""mlog backend finalize pass — bead mforth-10t.19.

Takes the raw ``list[MlogInstr]`` tuples from
:func:`mforth.backend.mlog.emit` and turns them into final mlog text
ready for paste into Mindustry's in-game logic processor.

Sub-passes (run in this order — order is load-bearing):

1. **Sidecar substitution** (:func:`substitute_sidecar`) — rewrites
   operand tokens that resolve to a Mode A (``target``) link's
   mforth-name into the in-game name. The substitution handles two
   shapes that bead .18's literal-lifting fusion produces:

   * **Bare-name fallback** — ``("printflush", ("display",))``: the
     mforth-name appears verbatim because the parser/resolver registered
     it as a ``UserVariable`` (the sidecar pre-seeding pattern from
     ``Runner.from_path``) and the emitter copied it straight into the
     operand position. Rewrite the operand to the in-game name.
   * **Slot-reference fallback** — ``("set", ("s<i>", '"display"'))``
     followed by ``("printflush", ("s<i>",))``: emit.py's bead .16
     emission shape. Trace back through the preceding ``set s<i>
     "<value>"``; if ``<value>`` (quote-stripped) matches a Mode A
     link, rewrite the consumer's slot-ref to the in-game name (and
     elide the dead staging ``set``).

   Mode B links (``index = N``) leave the consumer untouched — the
   prologue (sub-pass 2) sets a real mlog variable named after the
   mforth-name, and the consumer references that variable directly.

2. **Prologue emission** (:func:`emit_prologue`) — for each Mode B
   link in the sidecar, prepend ``getlink <mforth-name> <N>`` to the
   instruction list. The prologue runs first at execution time so the
   variable is bound before any consumer uses it. This MUST run before
   label resolution so that label positions account for the prologue's
   line-number offset.

3. **Label resolution** (:func:`resolve_labels`) — walks the
   (post-prologue) instruction stream, builds a label → 0-indexed line
   number map, then rewrites every ``("jump", (target_label, cond, a,
   b))`` operand-0 to the resolved line number string. Sentinel tuples
   ``(label, None, None)`` (per the .17 control-flow contract) consume
   zero lines and are stripped from the output.

4. **Writer** (:func:`write_mlog`) — emits canonical mlog text: a
   header comment with instruction count + source + sidecar paths;
   then one ``opcode op1 op2 ...`` line per instruction; exactly one
   trailing newline at EOF. The body shape matches bead .29's
   ``_serialize()`` contract so the output round-trips through the
   golden harness.

The top-level :func:`finalize` chains all four sub-passes and returns
the final mlog text.

Cross-bead notes
================

* This module's output is what bead .31's in-repo mlog interpreter will
  execute. For Mode B fixtures to behave equivalently with the host
  REPL, .31's interpreter must execute ``getlink`` and assign the
  resolved block ref to the mforth-name variable; consumers (PRINTFLUSH,
  SENSOR) will then reference that variable correctly.

* No bounds check is added on Mode B prologue ``getlink`` — per the .18
  drawer, mlog's native ``getlink`` returns ``null`` past ``@links`` and
  the host primitive pushes ``None`` with no event, so the parity
  already holds. Adding a guard would diverge.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence, Union

from mforth.backend.sidecar import LinkSpec, WorldConfig


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SidecarSubstitutionError(Exception):
    """Raised when the sidecar references a name the program can't bind,
    or vice versa.

    The orchestrator's contract reserves this error for the "every
    mforth-name referenced in the program has a sidecar entry" check.
    In the v1 pipeline the upstream resolver pass already enforces that
    invariant (unresolved-word errors surface at parse-time via
    :class:`UnresolvedWordError`), so this error is a defensive fallback
    for cases the resolver missed (e.g. a literal string that happens
    to look like a missing link name).
    """

    def __init__(self, name: str, source: Optional[str] = None) -> None:
        prefix = f"{source}: " if source else ""
        super().__init__(
            f"{prefix}sidecar substitution failed for name {name!r}"
        )
        self.name = name
        self.source = source


# ---------------------------------------------------------------------------
# Sub-pass 1: sidecar substitution
# ---------------------------------------------------------------------------


# Opcodes whose operands take a Mindustry block handle (the kind of
# operand that a sidecar binding rewrites). Per .18, ``printflush``
# takes a block at position 0; ``sensor`` takes a block at position 1
# (with the result slot at position 0 and the @-prop at position 2);
# ``getlink`` (consumer-form, NOT the prologue we emit ourselves) takes
# a number at position 1 — not a block name.
_BLOCK_OPERAND_POSITIONS: Mapping[str, tuple[int, ...]] = {
    "printflush": (0,),
    "sensor": (1,),
}


def _strip_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1]
    return token


def substitute_sidecar(
    instrs: Sequence[tuple],
    world_config: WorldConfig,
    *,
    source: str = "<sidecar>",
) -> list:
    """Rewrite operand tokens to reflect Mode A sidecar bindings.

    The pass walks the instruction list, recognises the two shapes
    emit.py produces for Mindustry-block operands (bare-name and
    slot-reference), and rewrites the consumer instruction to use the
    in-game name. The dead staging ``set s<i> "<name>"`` instruction is
    elided when its slot is consumed by exactly one immediately-following
    block-operand position.

    Mode B (``index``) entries are NOT substituted here — the prologue
    (sub-pass 2) handles them.

    Parameters
    ----------
    instrs
        Raw instruction tuples from :func:`mforth.backend.mlog.emit`.
    world_config
        The parsed sidecar; only ``links`` with a non-None ``target``
        are considered for substitution.
    source
        Used for error messages.
    """
    # Build the substitution map: mforth-name → in-game name (Mode A only).
    target_map: dict[str, str] = {}
    index_names: set[str] = set()
    for spec in world_config.links:
        if spec.target is not None:
            target_map[spec.mforth_name] = spec.target
        elif spec.index is not None:
            index_names.add(spec.mforth_name)

    instrs = list(instrs)
    out: list = []
    # Track preceding `set s<i> "<value>"` instructions so the
    # slot-reference shape can be traced back. Map slot-name → (out
    # index, raw value with quotes stripped).
    set_index: dict[str, tuple[int, str]] = {}

    for label, opcode, operands in instrs:
        if opcode is None:
            # Label sentinel pass-through; sub-pass 3 (resolve_labels)
            # handles these.
            out.append((label, opcode, operands))
            continue

        if opcode == "set" and len(operands) == 2:
            slot, raw_value = operands
            unquoted = _strip_quotes(raw_value)
            # If this is staging a Mode A mforth-name, rewrite the value
            # to the in-game name in-place. Even if it's never consumed
            # by a block-operand, the rewrite keeps the program
            # behaviour consistent (e.g. printing the substituted name).
            if unquoted in target_map:
                new_value = (
                    f'"{target_map[unquoted]}"'
                    if raw_value.startswith('"')
                    else target_map[unquoted]
                )
                out.append((label, opcode, (slot, new_value)))
                # Record the ORIGINAL mforth-name (not the substituted
                # in-game name) so a downstream block-operand consumer's
                # trace-back lookup still matches target_map[<name>].
                set_index[slot] = (len(out) - 1, unquoted)
                continue
            # Plain set — record so a later block-operand consumer can
            # trace back. The recorded value is quote-stripped to make
            # the lookup uniform.
            out.append((label, opcode, operands))
            set_index[slot] = (len(out) - 1, unquoted)
            continue

        positions = _BLOCK_OPERAND_POSITIONS.get(opcode)
        if positions is not None:
            new_operands = list(operands)
            elided_set_slots: list[str] = []
            for pos in positions:
                if pos >= len(new_operands):
                    continue
                tok = new_operands[pos]
                # Case A: bare mforth-name (literal-lifted form).
                if tok in target_map:
                    new_operands[pos] = target_map[tok]
                    continue
                if tok in index_names:
                    # Mode B: leave the bare name; the prologue will
                    # have made it a real variable.
                    continue
                # Case B: slot-reference — trace back to the staging set.
                if tok in set_index:
                    set_pos, set_value = set_index[tok]
                    if set_value in target_map:
                        new_operands[pos] = target_map[set_value]
                        elided_set_slots.append(tok)
            # Drop the staging set instructions whose slot we just
            # consumed in-place. Only elide if the slot isn't read by
            # any later instruction in the buffer; the simple linear
            # case (immediate consumer) is the common one.
            if elided_set_slots:
                out = [
                    inst
                    for inst in out
                    if not (
                        inst[1] == "set"
                        and inst[2]
                        and inst[2][0] in elided_set_slots
                    )
                ]
                # The set_index entries for elided slots are stale; drop
                # them so a downstream same-slot rewrite doesn't get
                # confused.
                for s in elided_set_slots:
                    set_index.pop(s, None)
            out.append((label, opcode, tuple(new_operands)))
            continue

        # Default: pass through, but update set_index when the
        # instruction writes a slot (defensive — keeps trace-back
        # accurate for any future opcodes that produce slot values).
        out.append((label, opcode, operands))

    return out


# ---------------------------------------------------------------------------
# Sub-pass 2: prologue emission
# ---------------------------------------------------------------------------


def emit_prologue(
    instrs: Sequence[tuple],
    world_config: WorldConfig,
) -> list:
    """Prepend ``getlink <mforth-name> <N>`` instructions for each Mode B
    link in the sidecar.

    The prologue lines are full mlog instructions and consume one line
    each at execution time; their presence shifts every subsequent
    line's index, which is why this sub-pass MUST run before label
    resolution (sub-pass 3).
    """
    prologue: list = []
    for spec in world_config.links:
        if spec.index is not None:
            prologue.append(
                (None, "getlink", (spec.mforth_name, str(spec.index)))
            )
    return prologue + list(instrs)


# ---------------------------------------------------------------------------
# Sub-pass 3: label resolution
# ---------------------------------------------------------------------------


def resolve_labels(instrs: Sequence[tuple]) -> list:
    """Resolve symbolic jump targets to 0-indexed mlog line numbers.

    Walks the instruction stream twice:

    1. **Pass A**: build the label → line-number map. A label may appear
       as the first slot of an instruction tuple (the .17 convention:
       "label attaches to next instruction") OR as a standalone
       sentinel ``(label, None, None)``. Sentinels consume zero lines;
       all other tuples consume one line. A label that sits at the
       end of the program resolves to the line number *past* the last
       instruction — mlog's auto-loop semantics turn that into "jump
       back to line 0", which is correct for IF/THEN's fall-through
       case.

    2. **Pass B**: emit the resolved stream — sentinels are dropped;
       every ``("jump", (target, cond, a, b))`` operand-0 is rewritten
       to ``str(resolved_line)``. Other operands (``cond``, ``a``,
       ``b``) pass through verbatim.
    """
    label_lines: dict[str, int] = {}
    line = 0
    for label, opcode, _operands in instrs:
        if label is not None:
            # First label position wins; per .17 emitter labels are
            # unique by construction.
            label_lines[label] = line
        if opcode is None:
            # Sentinel — consumes no line.
            continue
        line += 1

    out: list = []
    for _label, opcode, operands in instrs:
        if opcode is None:
            continue
        if opcode == "jump":
            target = operands[0]
            if target not in label_lines:
                raise KeyError(
                    f"unresolved jump target {target!r}; known labels: "
                    f"{sorted(label_lines)}"
                )
            new_operands = (str(label_lines[target]), *operands[1:])
            out.append((None, opcode, new_operands))
            continue
        out.append((None, opcode, operands))
    return out


# ---------------------------------------------------------------------------
# Sub-pass 4: writer
# ---------------------------------------------------------------------------


def write_mlog(
    instrs: Sequence[tuple],
    *,
    source_path: Path,
    sidecar_path: Optional[Path],
    emit_comments: bool = False,
    source_comments: Optional[Sequence[tuple]] = None,
) -> str:
    """Render the (resolved) instruction list as canonical mlog text.

    The header is a single ``#`` comment line:

        # mforth output — <count> instructions; SOURCE=<path>; SIDECAR=<path>

    The body matches bead .29's ``_serialize`` contract for compatibility
    with the golden harness body-shape: ``opcode op1 op2 ...`` per line,
    space-joined, no trailing whitespace, exactly one trailing newline at
    EOF.

    If any instruction still carries a non-None label, raises
    :class:`ValueError` — sub-pass 3 (label resolution) should have
    stripped them all. This is a defensive guard against future emitter
    bugs that leak unresolved labels into the writer.

    Parameters
    ----------
    instrs
        Resolved instruction tuples (no symbolic labels).
    source_path
        The source ``.fs`` path; emitted in the header for traceability.
    sidecar_path
        The sidecar ``.world.toml`` path, or None if no sidecar.
    emit_comments
        If True, interleave per-instruction source-location comments
        (currently a no-op placeholder — the emit pass does not yet
        carry term-level locations into the tuple shape).
    source_comments
        Optional list of comment strings parallel to ``instrs`` — if
        supplied and ``emit_comments`` is True, each is emitted before
        the corresponding instruction. v1 leaves this unused; the
        ``--emit-comments`` flag wires through but produces empty
        comments until the tuple shape grows source-loc info.
    """
    instrs = list(instrs)
    for label, _opcode, _operands in instrs:
        if label is not None:
            raise ValueError(
                f"unresolved label {label!r} reached the writer; "
                "run resolve_labels() first"
            )

    count = len(instrs)
    source_str = str(source_path)
    sidecar_str = str(sidecar_path) if sidecar_path is not None else "<none>"
    header = (
        f"# mforth output — {count} instructions; "
        f"SOURCE={source_str}; SIDECAR={sidecar_str}"
    )

    body_lines: list[str] = []
    for idx, (_label, opcode, operands) in enumerate(instrs):
        if (
            emit_comments
            and source_comments is not None
            and idx < len(source_comments)
            and source_comments[idx]
        ):
            body_lines.append(f"# {source_comments[idx]}")
        body_lines.append(" ".join((opcode, *operands)))

    return "\n".join([header, *body_lines]) + "\n"


# ---------------------------------------------------------------------------
# Top-level chain
# ---------------------------------------------------------------------------


def finalize(
    instrs: Sequence[tuple],
    *,
    world_config: WorldConfig,
    source_path: Union[str, Path],
    sidecar_path: Optional[Union[str, Path]] = None,
    emit_comments: bool = False,
) -> str:
    """Run the full finalize pipeline and return canonical mlog text.

    Sub-pass order (load-bearing):

    1. :func:`substitute_sidecar` — Mode A operand rewriting.
    2. :func:`emit_prologue` — Mode B ``getlink`` prologue prepend.
    3. :func:`resolve_labels` — symbolic → line-number jump rewriting
       AND sentinel stripping.
    4. :func:`write_mlog` — final text emission with header + body.

    The prologue must happen before label resolution so that the
    prologue's own line consumption shifts every label-resolution result
    downstream. The sidecar substitution can happen either before or
    after the prologue (Mode A and Mode B don't share names by sidecar
    construction), but we run it first so substitution's trace-back
    sees only emit.py output (no synthetic prologue instructions).
    """
    source_path = Path(source_path)
    sidecar_path = Path(sidecar_path) if sidecar_path is not None else None
    substituted = substitute_sidecar(
        instrs, world_config, source=str(sidecar_path or source_path)
    )
    with_prologue = emit_prologue(substituted, world_config)
    resolved = resolve_labels(with_prologue)
    return write_mlog(
        resolved,
        source_path=source_path,
        sidecar_path=sidecar_path,
        emit_comments=emit_comments,
    )


__all__ = [
    "SidecarSubstitutionError",
    "emit_prologue",
    "finalize",
    "resolve_labels",
    "substitute_sidecar",
    "write_mlog",
]
