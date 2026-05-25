"""mforth static stack-checker.

Per CLAUDE.md's hard rule "static stack analysis is mandatory", this pass
walks every Definition + main with a symbolic stack and:

* infers each user `Definition`'s `StackEffect` (in_arity, out_arity);
* annotates every `Term` with its incoming stack depth (used downstream
  by the mlog backend's slot allocator and by the host REPL executor);
* raises `StackError` on underflow at the top-level main, on IF/ELSE
  branch-depth mismatch, on BEGIN/UNTIL bodies that fail to produce a
  flag, on BEGIN/WHILE/REPEAT body non-neutrality, and on DO/LOOP body
  non-neutrality.

Recursion (self-call or cycle) is disallowed in v1 — the dialect lacks
`RECURSE` and the resolver pre-registers all definitions, so a recursive
reference resolves to the same Definition being analysed; the checker
raises `StackError` rather than looping.

User `Definition`s have their inferred effect surfaced in
`StackcheckResult.effects` so downstream passes (codegen, LSP hover) can
read them without re-walking the AST.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mforth.dictionary import (
    BuiltinWord,
    Dictionary,
    StackEffect,
    UnresolvedWordError,
    UserVariable,
    resolve,
    standard_dictionary,
)
from mforth.parse import (
    Begin,
    Definition,
    DoLoop,
    IfThen,
    LitInt,
    LitStr,
    Program,
    SrcLoc,
    VarRef,
    WordCall,
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class StackError(Exception):
    """Raised on stack-effect violations (underflow / branch mismatch / loop
    non-neutrality / recursive definition).
    """

    def __init__(self, message: str, src_loc: SrcLoc) -> None:
        super().__init__(f"{src_loc.file}:{src_loc.line}:{src_loc.col}: {message}")
        self.message = message
        self.src_loc = src_loc


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class StackcheckResult:
    program: Program
    effects: dict  # user-definition name → StackEffect
    _depths_in: dict = field(default_factory=dict)  # id(term) → incoming depth
    dictionary: Dictionary = None

    def depth_in(self, term) -> int:
        return self._depths_in[id(term)]


# ---------------------------------------------------------------------------
# Stack-checker
# ---------------------------------------------------------------------------


def stackcheck(
    program: Program,
    dictionary: Dictionary | None = None,
    initial_depth: int = 0,
) -> StackcheckResult:
    """Run the stack-checker over `program`.

    If `dictionary` is None a fresh standard dictionary is created and the
    resolver is run on the program first (registering user definitions and
    VARIABLE-declared names). Otherwise the caller is expected to have run
    resolution already.

    `initial_depth` seeds the simulated main-body depth. Defaults to 0 for
    AOT / single-shot compilation. The interactive REPL (bead mforth-10t.13)
    passes the current data-stack depth so a line like `+ .` correctly
    type-checks against values pushed by an earlier line. Underflow is
    measured against 0, NOT against `initial_depth` — a line that consumes
    more than the live stack contains is still a stack error.

    Returns a `StackcheckResult` carrying the user-def effects and per-term
    incoming-depth annotations.
    """
    if dictionary is None:
        dictionary = resolve(program)

    depths_in: dict = {}
    effects: dict = {}
    computing: set = set()

    def lookup_effect(wc: WordCall) -> StackEffect:
        entry = dictionary.lookup(wc.name)
        if entry is None:
            raise UnresolvedWordError(wc.name, wc.src_loc)
        if isinstance(entry, BuiltinWord):
            return entry.stack_effect
        if isinstance(entry, UserVariable):
            return StackEffect(0, 1)  # pushes address
        if isinstance(entry, Definition):
            in_a, out_a = compute_def_effect(entry)
            return StackEffect(in_a, out_a)
        raise TypeError(f"unknown dictionary entry type {type(entry).__name__}")

    def compute_def_effect(defn: Definition) -> tuple[int, int]:
        if defn.name in effects:
            eff = effects[defn.name]
            return eff.in_arity, eff.out_arity
        if defn.name in computing:
            raise StackError(
                f"recursive definition '{defn.name}' is not supported in v1",
                defn.src_loc,
            )
        computing.add(defn.name)
        try:
            depth, min_depth, _ = simulate(defn.body, initial_depth=0)
            in_arity = max(0, -min_depth)
            out_arity = depth + in_arity
            # Bead mforth-6dh: if the user declared a stack effect via a
            # `( in -- out )` comment after `:` name, verify the inferred
            # effect matches. The names inside the comment are doc-only;
            # only the counts are enforced. No declared effect = no check
            # (preserves the v1 permissive behavior; additive change).
            declared = getattr(defn, "declared_effect", None)
            if declared is not None:
                d_in, d_out = declared
                if (d_in, d_out) != (in_arity, out_arity):
                    raise StackError(
                        f"definition '{defn.name}' declared stack effect "
                        f"( {d_in} -- {d_out} ) but inferred "
                        f"( {in_arity} -- {out_arity} )",
                        defn.src_loc,
                    )
            effects[defn.name] = StackEffect(in_arity, out_arity)
            return in_arity, out_arity
        finally:
            computing.discard(defn.name)

    def simulate(
        body: list, initial_depth: int
    ) -> tuple[int, int, SrcLoc | None]:
        """Simulate executing `body` starting at depth `initial_depth`.

        Returns (final_depth, min_depth, src_loc_of_min_depth_term). The
        loc is the source location of the term that took the stack to its
        deepest point — useful for pinning the underflow message.
        """
        depth = initial_depth
        min_depth = initial_depth
        min_loc: SrcLoc | None = None

        def note_depth(new_depth: int, loc: SrcLoc) -> None:
            nonlocal min_depth, min_loc
            if new_depth < min_depth:
                min_depth = new_depth
                min_loc = loc

        for term in body:
            depths_in[id(term)] = depth

            if isinstance(term, (LitInt, LitStr)):
                depth += 1
                continue

            if isinstance(term, VarRef):
                # Fused variable access: fetch pushes the value, store
                # pops the value.  Added for the mlog emit pass
                # (bead mforth-10t.16) — the AST fusion pass turns
                # `<name> @` / `<name> !` sequences into VarRef nodes
                # so the slot allocator's depths match the emitted
                # instructions.
                if term.mode == "fetch":
                    depth += 1
                elif term.mode == "store":
                    after_pop = depth - 1
                    note_depth(after_pop, term.src_loc)
                    depth = after_pop
                else:
                    raise ValueError(f"unknown VarRef mode {term.mode!r}")
                continue

            if isinstance(term, WordCall):
                eff = lookup_effect(term)
                after_pop = depth - eff.in_arity
                note_depth(after_pop, term.src_loc)
                depth = after_pop + eff.out_arity
                continue

            if isinstance(term, IfThen):
                after_flag = depth - 1
                note_depth(after_flag, term.src_loc)
                then_final, then_min, then_min_loc = simulate(term.then_body, after_flag)
                else_final, else_min, else_min_loc = simulate(term.else_body, after_flag)
                if then_final != else_final:
                    raise StackError(
                        f"IF branches leave stack at different depths "
                        f"(then delta={then_final - after_flag:+d}, "
                        f"else delta={else_final - after_flag:+d})",
                        term.src_loc,
                    )
                if then_min < min_depth:
                    min_depth, min_loc = then_min, then_min_loc or term.src_loc
                if else_min < min_depth:
                    min_depth, min_loc = else_min, else_min_loc or term.src_loc
                depth = then_final
                continue

            if isinstance(term, Begin):
                if term.kind == "until":
                    body_final, body_min, body_min_loc = simulate(term.body, depth)
                    delta = body_final - depth
                    if delta != 1:
                        raise StackError(
                            f"BEGIN/UNTIL body must net-produce exactly one flag "
                            f"for UNTIL to consume (got delta={delta:+d})",
                            term.src_loc,
                        )
                    if body_min < min_depth:
                        min_depth, min_loc = body_min, body_min_loc or term.src_loc
                    # UNTIL consumes the flag; depth restored.
                else:  # 'while-repeat'
                    test_final, test_min, test_min_loc = simulate(term.body, depth)
                    test_delta = test_final - depth
                    if test_delta != 1:
                        raise StackError(
                            f"BEGIN/WHILE test must net-produce exactly one flag "
                            f"(got delta={test_delta:+d})",
                            term.src_loc,
                        )
                    if test_min < min_depth:
                        min_depth, min_loc = test_min, test_min_loc or term.src_loc
                    # WHILE pops flag → depth.
                    cb_final, cb_min, cb_min_loc = simulate(term.cond_body, depth)
                    cb_delta = cb_final - depth
                    if cb_delta != 0:
                        raise StackError(
                            f"BEGIN/WHILE/REPEAT body must be stack-neutral "
                            f"(got delta={cb_delta:+d})",
                            term.src_loc,
                        )
                    if cb_min < min_depth:
                        min_depth, min_loc = cb_min, cb_min_loc or term.src_loc
                continue

            if isinstance(term, DoLoop):
                after_do = depth - 2
                note_depth(after_do, term.src_loc)
                body_final, body_min, body_min_loc = simulate(term.body, after_do)
                if body_final != after_do:
                    raise StackError(
                        f"DO/LOOP body must be stack-neutral "
                        f"(got delta={body_final - after_do:+d})",
                        term.src_loc,
                    )
                if body_min < min_depth:
                    min_depth, min_loc = body_min, body_min_loc or term.src_loc
                depth = after_do
                continue

            raise TypeError(f"unknown Term type {type(term).__name__}")

        return depth, min_depth, min_loc

    # Compute effects for all user definitions (drives recursion via
    # lookup_effect inside simulate).
    for defn in program.definitions:
        compute_def_effect(defn)

    # Check main body — main starts at `initial_depth` (0 for AOT, REPL's
    # current data-stack depth for interactive lines). Underflow is measured
    # against absolute zero, NOT against `initial_depth`: a REPL line that
    # consumes more than the live stack contains is still an underflow.
    main_final, main_min, main_min_loc = simulate(
        program.main, initial_depth=initial_depth
    )
    if main_min < 0:
        raise StackError(
            f"stack underflow in main (depth went to {main_min})",
            main_min_loc if main_min_loc is not None else SrcLoc("<unknown>", 1, 1),
        )

    return StackcheckResult(
        program=program,
        effects=effects,
        _depths_in=depths_in,
        dictionary=dictionary,
    )


__all__ = [
    "StackError",
    "StackcheckResult",
    "stackcheck",
]
