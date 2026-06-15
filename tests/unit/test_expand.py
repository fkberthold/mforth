"""Unit + cross-backend tests for the phase-0 expand-then-check pass (bead
mforth-7h1.1).

``mforth.expand.expand`` is the keystone of mforth's metaprogramming
meta-layer: a *compile-time* pass that runs BEFORE stackcheck and codegen
and replaces every meta-word (``Macro``) call with the macro's body,
recursively to a fixpoint, so that stackcheck and both backends only ever
see a fully-expanded AST containing ZERO meta-words.

Two locked invariants (verbatim from the bead + design doc D14):

INVARIANT 1 — stackcheck and codegen only ever see a fully-expanded AST
containing ZERO meta-words; the host (REPL) and mlog backends consume the
IDENTICAL expanded AST for a given source. ``expand`` is the single
deterministic function both front-ends invoke.

INVARIANT 2 — a meta-word whose compile-time body calls a world-sink
primitive (PRINT / PRINTFLUSH / SENSOR / CONTROL-* / WAIT / GETLINK) or
reads runtime/MockWorld state is a COMPILE ERROR, not a silent miscompile.

Scope (locked this session): B1 introduces an INTERNAL meta-word
representation only — there is NO user-facing ``.fs`` macro syntax yet
(that is later beads B2/B3). These tests therefore construct + register
``Macro`` entries PROGRAMMATICALLY (build an AST + seed a Dictionary),
never from source macro syntax.

The purity check (Invariant 2) is driven OFF the dictionary tags, not a
hardcoded primitive-name list:

* a world-sink call resolves to a ``BuiltinWord`` whose ``tag`` is
  ``"mindustry"`` or ``"mindustry-control"``;
* a runtime-state read is a term resolving to a ``UserVariable`` (tag
  ``"var"``) being fetched.
"""

from __future__ import annotations

import pytest

from mforth.dictionary import (
    BuiltinWord,
    StackEffect,
    UserVariable,
    resolve,
    standard_dictionary,
)
from mforth.parse import (
    Begin,
    DoLoop,
    IfThen,
    LitInt,
    Program,
    SrcLoc,
    WordCall,
    parse,
)

# The module under test does not exist yet — this import is the headline
# RED failure (ModuleNotFoundError: No module named 'mforth.expand') until
# the implementer creates src/mforth/expand.py.
from mforth.expand import ExpandError, PurityError, expand


# ---------------------------------------------------------------------------
# Macro representation under test (lives in mforth.dictionary alongside
# BuiltinWord / UserVariable). The implementer adds a frozen
# ``Macro(name: str, body: list[Term])`` dataclass and includes it in the
# ``DictEntry`` union; ``resolve()`` must tolerate a seeded ``Macro``.
# ---------------------------------------------------------------------------
from mforth.dictionary import Macro  # noqa: E402  (deliberately after expand import)


_LOC = SrcLoc("<test>", 1, 1)


def _w(name: str) -> WordCall:
    return WordCall(name=name, src_loc=_LOC)


def _lit(value: int) -> LitInt:
    return LitInt(value=value, src_loc=_LOC)


def _seed(*macros: Macro):
    """Return a fresh standard dictionary with ``macros`` registered.

    Macros are registered programmatically (no ``.fs`` macro syntax exists
    in B1). ``resolve()`` must tolerate these already-present ``Macro``
    entries.
    """
    d = standard_dictionary()
    for m in macros:
        # The dictionary exposes per-kind adders (add_builtin / add_variable
        # / add_definition); a Macro is registered by name the same way.
        d._entries[m.name.lower()] = m  # noqa: SLF001
    return d


# A program whose ``main`` is exactly ``terms`` (no definitions).
def _prog(*terms) -> Program:
    return Program(definitions=[], main=list(terms))


# ---------------------------------------------------------------------------
# Helpers that assert the post-condition: ZERO meta-words survive expansion.
# ---------------------------------------------------------------------------


def _iter_terms(terms: list):
    """Yield every Term recursively (descending into control-flow bodies)."""
    for t in terms:
        yield t
        if isinstance(t, IfThen):
            yield from _iter_terms(t.then_body)
            yield from _iter_terms(t.else_body)
        elif isinstance(t, Begin):
            yield from _iter_terms(t.body)
            yield from _iter_terms(t.cond_body)
        elif isinstance(t, DoLoop):
            yield from _iter_terms(t.body)


def _assert_no_macros(program: Program, dictionary) -> None:
    """No WordCall anywhere in the expanded program resolves to a Macro."""
    all_terms = list(_iter_terms(program.main))
    for defn in program.definitions:
        all_terms.extend(_iter_terms(defn.body))
    for t in all_terms:
        if isinstance(t, WordCall):
            entry = dictionary.lookup(t.name)
            assert not isinstance(entry, Macro), (
                f"meta-word {t.name!r} survived expansion — it must never "
                f"reach stackcheck/codegen"
            )


def _word_names(terms: list) -> list[str]:
    return [t.name for t in _iter_terms(terms) if isinstance(t, WordCall)]


def _int_values(terms: list) -> list[int]:
    return [t.value for t in _iter_terms(terms) if isinstance(t, LitInt)]


# ===========================================================================
# 1. expand inlines a Macro's body → ZERO meta-words remain
# ===========================================================================


def test_expand_inlines_macro_body():
    # macro inc5 == ( n -- n+1 ... ) body: 5 +
    inc5 = Macro(name="inc5", body=[_lit(5), _w("+")])
    d = _seed(inc5)
    prog = _prog(_lit(10), _w("inc5"), _w("PRINT"))

    expanded = expand(prog, d)

    # No meta-word survives.
    _assert_no_macros(expanded, d)
    # The macro body's terms are spliced in place of the call: 10 5 + PRINT
    assert _int_values(expanded.main) == [10, 5]
    assert _word_names(expanded.main) == ["+", "PRINT"]


def test_expand_returns_a_program():
    inc5 = Macro(name="inc5", body=[_lit(5), _w("+")])
    d = _seed(inc5)
    expanded = expand(_prog(_lit(1), _w("inc5")), d)
    assert isinstance(expanded, Program)


def test_expand_program_with_no_macros_is_macro_free():
    # A program that uses no macros expands to a macro-free program (and the
    # observable terms are preserved).
    d = standard_dictionary()
    prog = _prog(_lit(2), _lit(3), _w("+"), _w("PRINT"))
    expanded = expand(prog, d)
    _assert_no_macros(expanded, d)
    assert _word_names(expanded.main) == ["+", "PRINT"]


def test_expand_inside_definition_body():
    # A macro called from inside a `: ... ;` definition body is expanded too.
    inc5 = Macro(name="inc5", body=[_lit(5), _w("+")])
    d = _seed(inc5)
    # Build a program whose single definition body calls the macro. We parse
    # a placeholder definition then swap its body so we don't rely on macro
    # surface syntax.
    base = parse(": bump 1 + ;", file="<test>")
    defn = base.definitions[0]
    defn.body = [_w("inc5")]
    prog = Program(definitions=[defn], main=[])
    # Register the definition so resolve/lookup is consistent.
    expanded = expand(prog, d)
    _assert_no_macros(expanded, d)
    assert _int_values(expanded.definitions[0].body) == [5]
    assert _word_names(expanded.definitions[0].body) == ["+"]


def test_expand_inside_control_flow_body():
    # A macro called inside an IF branch is expanded within the branch.
    inc5 = Macro(name="inc5", body=[_lit(5), _w("+")])
    d = _seed(inc5)
    if_node = IfThen(then_body=[_w("inc5")], else_body=[_lit(0)], src_loc=_LOC)
    prog = _prog(_lit(1), if_node)
    expanded = expand(prog, d)
    _assert_no_macros(expanded, d)
    # The inc5 inside the then-branch became `5 +`.
    the_if = [t for t in expanded.main if isinstance(t, IfThen)][0]
    assert _int_values(the_if.then_body) == [5]
    assert _word_names(the_if.then_body) == ["+"]


# ===========================================================================
# 2. FIXPOINT — a Macro whose body references another Macro expands fully
# ===========================================================================


def test_expand_nested_macro_to_fixpoint():
    # add5 body references add2 (and a literal 3): add5 == add2 3 +
    # add2 == 2 +
    add2 = Macro(name="add2", body=[_lit(2), _w("+")])
    add5 = Macro(name="add5", body=[_w("add2"), _lit(3), _w("+")])
    d = _seed(add2, add5)
    prog = _prog(_lit(10), _w("add5"), _w("PRINT"))

    expanded = expand(prog, d)

    _assert_no_macros(expanded, d)
    # 10 add5 -> 10 (add2 3 +) -> 10 (2 + 3 +)  => literals 10 2 3; words + + PRINT
    assert _int_values(expanded.main) == [10, 2, 3]
    assert _word_names(expanded.main) == ["+", "+", "PRINT"]


def test_expand_deeply_nested_chain_terminates():
    # m1 -> m2 -> m3 -> bare literal. Fixpoint must drill all the way down.
    m3 = Macro(name="m3", body=[_lit(3)])
    m2 = Macro(name="m2", body=[_w("m3")])
    m1 = Macro(name="m1", body=[_w("m2")])
    d = _seed(m1, m2, m3)
    expanded = expand(_prog(_w("m1")), d)
    _assert_no_macros(expanded, d)
    assert _int_values(expanded.main) == [3]


# ===========================================================================
# 3. Cyclic / non-terminating expansion → ExpandError
# ===========================================================================


def test_expand_direct_self_cycle_raises():
    # A macro whose body calls itself never converges.
    loop = Macro(name="loopy", body=[_lit(1), _w("loopy")])
    d = _seed(loop)
    with pytest.raises(ExpandError):
        expand(_prog(_w("loopy")), d)


def test_expand_mutual_cycle_raises():
    # A -> B -> A: mutual recursion, also non-terminating.
    a = Macro(name="ma", body=[_w("mb")])
    b = Macro(name="mb", body=[_w("ma")])
    d = _seed(a, b)
    with pytest.raises(ExpandError):
        expand(_prog(_w("ma")), d)


def test_expand_cycle_through_control_flow_raises():
    # A cycle reachable only through a control-flow body still aborts.
    a = Macro(name="ca", body=[IfThen(then_body=[_w("cb")], else_body=[], src_loc=_LOC)])
    b = Macro(name="cb", body=[_w("ca")])
    d = _seed(a, b)
    with pytest.raises(ExpandError):
        expand(_prog(_w("ca")), d)


# ===========================================================================
# 4. Invariant 2 — world-sink primitive in a macro body → PurityError
#    naming the offending primitive.
# ===========================================================================


def test_macro_calling_print_is_impure():
    # PRINT is a world-sink (tag "mindustry") — a macro body cannot perform
    # a runtime world effect at compile time.
    bad = Macro(name="say", body=[_lit(1), _w("PRINT")])
    d = _seed(bad)
    with pytest.raises(PurityError) as exc:
        expand(_prog(_w("say")), d)
    # The message names the offending primitive.
    assert "PRINT" in str(exc.value).upper()


def test_macro_calling_wait_is_impure():
    bad = Macro(name="pause", body=[_lit(1), _w("WAIT")])
    d = _seed(bad)
    with pytest.raises(PurityError) as exc:
        expand(_prog(_w("pause")), d)
    assert "WAIT" in str(exc.value).upper()


def test_macro_calling_printflush_is_impure():
    bad = Macro(name="flush", body=[_w("PRINTFLUSH")])
    d = _seed(bad)
    with pytest.raises(PurityError) as exc:
        expand(_prog(_w("flush")), d)
    assert "PRINTFLUSH" in str(exc.value).upper()


def test_macro_calling_sensor_is_impure():
    bad = Macro(name="probe", body=[_w("SENSOR")])
    d = _seed(bad)
    with pytest.raises(PurityError) as exc:
        expand(_prog(_w("probe")), d)
    assert "SENSOR" in str(exc.value).upper()


def test_macro_calling_getlink_is_impure():
    bad = Macro(name="grab", body=[_lit(0), _w("GETLINK")])
    d = _seed(bad)
    with pytest.raises(PurityError) as exc:
        expand(_prog(_w("grab")), d)
    assert "GETLINK" in str(exc.value).upper()


def test_macro_calling_control_is_impure():
    # CONTROL-* primitives carry tag "mindustry-control" — also world sinks.
    bad = Macro(name="toggle", body=[_w("CONTROL-ENABLED")])
    d = _seed(bad)
    with pytest.raises(PurityError) as exc:
        expand(_prog(_w("toggle")), d)
    assert "CONTROL-ENABLED" in str(exc.value).upper()


def test_purity_check_driven_by_tag_not_name_list():
    # The purity check must key off the dictionary TAG, not a hardcoded
    # name list. Register a fresh world-sink builtin under a novel name and
    # tag "mindustry"; a macro that calls it is impure.
    d = standard_dictionary()
    d.add_builtin(
        BuiltinWord("BEEP", StackEffect(0, 0), "made-up world sink", "mindustry")
    )
    bad = Macro(name="alarm", body=[_w("BEEP")])
    d._entries["alarm"] = bad  # noqa: SLF001
    with pytest.raises(PurityError) as exc:
        expand(_prog(_w("alarm")), d)
    assert "BEEP" in str(exc.value).upper()


def test_impure_macro_through_nested_macro_still_caught():
    # The impurity is hidden one level down (outer macro -> inner macro that
    # prints). Fixpoint expansion must still flag it.
    inner = Macro(name="inner_print", body=[_lit(1), _w("PRINT")])
    outer = Macro(name="outer", body=[_w("inner_print")])
    d = _seed(inner, outer)
    with pytest.raises(PurityError):
        expand(_prog(_w("outer")), d)


# ===========================================================================
# 5. Invariant 2 — a macro body that READS runtime state → PurityError
# ===========================================================================


def test_macro_fetching_user_variable_is_impure():
    # `x @` reads runtime/MockWorld state: x resolves to a UserVariable
    # (tag "var") and is fetched with `@`. A compile-time macro may not
    # read runtime state.
    d = standard_dictionary()
    d.add_variable(UserVariable(name="x", src_loc=_LOC))
    bad = Macro(name="peek", body=[_w("x"), _w("@")])
    d._entries["peek"] = bad  # noqa: SLF001
    with pytest.raises(PurityError):
        expand(_prog(_w("peek")), d)


# ===========================================================================
# 6. A PURE macro body (arithmetic / stack words only) → expands clean
# ===========================================================================


def test_pure_arithmetic_macro_expands_clean():
    # Only arith/stack words — no world sink, no runtime read.
    pure = Macro(name="square", body=[_w("DUP"), _w("*")])
    d = _seed(pure)
    expanded = expand(_prog(_lit(7), _w("square")), d)
    _assert_no_macros(expanded, d)
    assert _word_names(expanded.main) == ["DUP", "*"]
    assert _int_values(expanded.main) == [7]


def test_pure_macro_with_stack_and_compare_ops_expands_clean():
    pure = Macro(name="maxish", body=[_w("OVER"), _w("OVER"), _w("<")])
    d = _seed(pure)
    expanded = expand(_prog(_lit(1), _lit(2), _w("maxish")), d)
    _assert_no_macros(expanded, d)
    assert _word_names(expanded.main) == ["OVER", "OVER", "<"]


# ===========================================================================
# 7. Invariant 1 (cross-backend) — both backends consume the IDENTICAL
#    expanded AST; expand is the single deterministic function they invoke.
# ===========================================================================


def test_expand_is_deterministic_single_function():
    # Same (program, dictionary) → identical expansion every call. This is
    # what lets the host and mlog front-ends share one expand pass.
    pure = Macro(name="inc5", body=[_lit(5), _w("+")])

    d1 = _seed(pure)
    d2 = _seed(pure)
    out1 = expand(_prog(_lit(10), _w("inc5"), _w("PRINT")), d1)
    out2 = expand(_prog(_lit(10), _w("inc5"), _w("PRINT")), d2)

    assert _int_values(out1.main) == _int_values(out2.main)
    assert _word_names(out1.main) == _word_names(out2.main)


def test_expanded_program_resolves_and_stackchecks_clean():
    # The expanded AST contains zero meta-words, so the *existing*
    # resolve + stackcheck pipeline (which knows nothing about Macros)
    # accepts it. This is the gate that lets codegen + the host backend
    # consume the expanded AST unchanged.
    from mforth.stackcheck import stackcheck

    inc5 = Macro(name="inc5", body=[_lit(5), _w("+")])
    d = _seed(inc5)
    expanded = expand(_prog(_lit(10), _w("inc5")), d)

    # Re-resolve the EXPANDED program against a clean standard dictionary
    # (no macro seeded): every surviving WordCall must be a real builtin —
    # proving no meta-word leaked through.
    clean = standard_dictionary()
    resolve(expanded, dictionary=clean)
    result = stackcheck(expanded, dictionary=clean)
    assert result is not None


def test_cross_backend_expanded_ast_yields_identical_sink_events():
    """Invariant 1 in observable form.

    A macro-using program, once expanded, is the SAME term sequence both
    backends consume. We pin that by: (a) expanding a macro-using program
    and showing it is byte-equivalent (term-for-term) to the plain inlined
    source's AST, then (b) running that inlined source through BOTH the
    mlog interpreter path and the host runner path and asserting the SINK
    (PRINT) event streams are identical — i.e. the expanded AST does the
    same observable thing on either backend.
    """
    from mforth.backend.host import Executor
    from mforth.backend.primitives import register_all
    from mforth.backend.sidecar import WorldConfig
    from mforth.backend.world import MockWorld, MessagePrintEvent
    from mforth.mlog_interp import MlogInterpreter
    from mforth.optimize import OptLevel, compile_text
    from mforth.stackcheck import stackcheck

    inc5 = Macro(name="inc5", body=[_lit(5), _w("+")])
    d = _seed(inc5)
    macro_prog = _prog(_lit(10), _w("inc5"), _w("PRINT"))

    # (a) expand(macro_prog) is term-for-term the inlined source `10 5 + PRINT`.
    expanded = expand(macro_prog, d)
    inlined_src = "10 5 + PRINT"
    inlined_ast = parse(inlined_src, file="<test>")
    assert _int_values(expanded.main) == _int_values(inlined_ast.main)
    assert _word_names(expanded.main) == _word_names(inlined_ast.main)

    # (b) the inlined source runs identically on both backends (SINK == PRINT).
    # mlog path:
    mlog_text = compile_text(
        inlined_src, opt_level=OptLevel.O0, source_path="<test>"
    )
    mlog_world = MockWorld()
    MlogInterpreter(
        world=mlog_world, text=mlog_text, user_variables=set()
    ).run(iterations=1)
    mlog_prints = [
        e for e in mlog_world.events if isinstance(e, MessagePrintEvent)
    ]

    # host path:
    host_world = MockWorld()
    host_dict = resolve(inlined_ast)
    host_result = stackcheck(inlined_ast, dictionary=host_dict)
    host_exec = Executor(world=host_world, dictionary=host_dict)
    register_all(host_exec)
    host_exec.execute(host_result)
    host_prints = [
        e for e in host_world.events if isinstance(e, MessagePrintEvent)
    ]

    assert [e.text for e in mlog_prints] == [e.text for e in host_prints]
    assert [e.text for e in host_prints] == ["15"]


# ===========================================================================
# 8. PIPELINE WIRING (Invariant 1, in the REAL front-ends) — expand() must be
#    invoked BETWEEN resolve and stackcheck inside BOTH front-end entry
#    points, not merely exist as a standalone function.
#
#    Mechanism that makes these robust: a seeded ``Macro`` has NO
#    ``StackEffect``. If a macro-using program is pushed through the REAL
#    pipeline with that seeded dictionary:
#      * WITHOUT the wiring, stackcheck reaches the unexpanded ``Macro``
#        WordCall and crashes/errors (no StackEffect) → the test fails (RED).
#      * WITH the wiring, expand() removes the macro before stackcheck → the
#        program compiles/runs and the observable result is the EXPANDED one.
#    So these tests fail until expand is actually wired in.
# ===========================================================================


def test_compile_text_wires_expand_pass():
    """The MLOG front-end (``mforth.optimize.compile_text``) runs ``expand``
    between resolve and stackcheck.

    A macro ``inc5 == 5 +`` is seeded into the dictionary handed to
    ``compile_text``. The source ``10 inc5 PRINT`` can only compile if
    ``compile_text`` expands the macro before stackcheck (a ``Macro`` has no
    ``StackEffect``, so an unexpanded pipeline errors at stackcheck). We then
    PROVE the expansion happened by RUNNING the emitted mlog and asserting the
    observable PRINT is ``"15"`` (10 + 5), i.e. the macro body really was
    spliced in — not merely tolerated.
    """
    from mforth.mlog_interp import MlogInterpreter
    from mforth.optimize import OptLevel, compile_text
    from mforth.backend.world import MessagePrintEvent, MockWorld

    inc5 = Macro(name="inc5", body=[_lit(5), _w("+")])
    seed = _seed(inc5)

    mlog_text = compile_text(
        "10 inc5 PRINT",
        opt_level=OptLevel.O0,
        dictionary=seed,
        source_path="<test>",
    )

    world = MockWorld()
    MlogInterpreter(world=world, text=mlog_text, user_variables=set()).run(
        iterations=1
    )
    prints = [e for e in world.events if isinstance(e, MessagePrintEvent)]
    assert [e.text for e in prints] == ["15"]


def test_compile_text_rejects_impure_macro():
    """Purity (Invariant 2) holds through the REAL mlog pipeline, not just the
    bare ``expand`` function.

    An impure macro (its body calls the world-sink ``PRINT``) pushed through
    ``compile_text`` must raise ``PurityError`` — proving the wired ``expand``
    enforces purity inside the front-end, before stackcheck/codegen.
    """
    from mforth.optimize import OptLevel, compile_text

    bad = Macro(name="say", body=[_lit(1), _w("PRINT")])
    seed = _seed(bad)

    with pytest.raises(PurityError):
        compile_text(
            "say",
            opt_level=OptLevel.O0,
            dictionary=seed,
            source_path="<test>",
        )


def test_host_runner_wires_expand_pass(tmp_path):
    """The HOST/REPL front-end (``mforth.backend.runner.Runner``) runs
    ``expand`` between resolve and stackcheck.

    We drive the program through the runner's OWN public code path (NOT a
    hand-assembled resolve→stackcheck→Executor pipeline) so the test pins that
    ``runner.py`` ITSELF wires expand. A macro ``inc5 == 5 +`` is seeded into
    the dictionary the runner uses; the source ``10 inc5 PRINT`` can only run
    if the runner expands the macro before stackcheck (a ``Macro`` has no
    ``StackEffect``). We assert the host PRINT event is ``"15"`` — the
    expanded result, identical to the mlog backend (Invariant 1).

    SEAM (flagged for the implementer): ``Runner.from_path`` currently builds
    its OWN ``standard_dictionary()`` internally (runner.py ~line 256) and
    exposes NO way to inject a pre-seeded dictionary, AND B1 has no ``.fs``
    macro surface syntax — so today a seeded ``Macro`` cannot reach the runner
    through any public entry point. This test pins the minimal seam the
    implementer must add: ``Runner.from_path`` must accept an optional
    ``dictionary=`` parameter (a pre-seeded dictionary, threaded into the
    runner's resolve step exactly the way ``compile_text(dictionary=...)``
    already does), AND ``Runner.from_path`` must call ``expand`` between
    resolve and stackcheck. With that seam in place + the wiring, this test
    goes GREEN; without either it stays RED.
    """
    from mforth.backend.runner import Runner
    from mforth.backend.world import MessagePrintEvent

    inc5 = Macro(name="inc5", body=[_lit(5), _w("+")])
    seed = _seed(inc5)

    src = tmp_path / "macro_inc.fs"
    src.write_text("10 inc5 PRINT\n")

    # Drive the runner's real public path with the seeded dictionary. The
    # `dictionary=` keyword is the seam the implementer must add (see
    # docstring); the runner must thread it into resolve and then run expand
    # before stackcheck.
    runner = Runner.from_path(src, dictionary=seed)
    runner.run_once()

    prints = [
        e
        for e in runner.executor.world.events
        if isinstance(e, MessagePrintEvent)
    ]
    assert [e.text for e in prints] == ["15"]
