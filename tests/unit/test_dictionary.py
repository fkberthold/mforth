"""Unit tests for the mforth dictionary + word-resolution pass.

Bead mforth-10t.6. TDD-first.
"""

from __future__ import annotations

import pytest

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
    WordCall,
    parse,
)


# ---------------------------------------------------------------------------
# standard_dictionary builds + stack effects
# ---------------------------------------------------------------------------


def test_standard_dictionary_has_stack_ops():
    d = standard_dictionary()
    for name in ["DUP", "DROP", "SWAP", "OVER", "ROT", "NIP", "TUCK"]:
        e = d.lookup(name)
        assert isinstance(e, BuiltinWord)
        assert e.tag == "stack"


def test_standard_dictionary_has_arithmetic():
    d = standard_dictionary()
    for name in ["+", "-", "*", "/", "MOD"]:
        e = d.lookup(name)
        assert isinstance(e, BuiltinWord)
        assert e.tag == "arith"
        assert e.stack_effect == StackEffect(2, 1)


def test_standard_dictionary_has_mindustry_primitives():
    d = standard_dictionary()
    for name in ["PRINT", "PRINTFLUSH", "WAIT", "SENSOR", "GETLINK"]:
        e = d.lookup(name)
        assert isinstance(e, BuiltinWord)
        assert e.tag == "mindustry"


def test_dup_stack_effect():
    d = standard_dictionary()
    assert d.lookup("DUP").stack_effect == StackEffect(1, 2)


def test_drop_stack_effect():
    assert standard_dictionary().lookup("DROP").stack_effect == StackEffect(1, 0)


def test_sensor_stack_effect():
    # sensor: ( block prop -- value ) → (2, 1)
    assert standard_dictionary().lookup("SENSOR").stack_effect == StackEffect(2, 1)


def test_store_stack_effect_is_two_in_zero_out():
    # !: ( value addr -- )
    assert standard_dictionary().lookup("!").stack_effect == StackEffect(2, 0)


def test_fetch_stack_effect_is_one_in_one_out():
    # @: ( addr -- value )
    assert standard_dictionary().lookup("@").stack_effect == StackEffect(1, 1)


def test_variable_stack_effect_is_zero_zero():
    # VARIABLE is a compile-time word that consumes a name from source,
    # not from the stack.
    assert standard_dictionary().lookup("VARIABLE").stack_effect == StackEffect(0, 0)


def test_not_is_unary():
    assert standard_dictionary().lookup("NOT").stack_effect == StackEffect(1, 1)


def test_lookup_is_case_insensitive():
    d = standard_dictionary()
    assert d.lookup("dup") is d.lookup("DUP")
    assert d.lookup("Dup") is d.lookup("DUP")
    assert d.lookup("PrInTfLuSh") is d.lookup("PRINTFLUSH")


def test_unknown_word_returns_none():
    assert standard_dictionary().lookup("nonexistent") is None


def test_contains_uses_case_insensitive_lookup():
    d = standard_dictionary()
    assert "dup" in d
    assert "DUP" in d
    assert "nonexistent" not in d


# ---------------------------------------------------------------------------
# Dictionary as a container (manual builds)
# ---------------------------------------------------------------------------


def test_empty_dictionary():
    d = Dictionary()
    assert len(d) == 0
    assert d.lookup("anything") is None


def test_add_builtin():
    d = Dictionary()
    d.add_builtin(
        BuiltinWord(name="FOO", stack_effect=StackEffect(0, 1), doc="x", tag="stack")
    )
    assert d.lookup("foo").name == "FOO"
    assert len(d) == 1


def test_add_user_variable():
    d = Dictionary()
    loc = SrcLoc("<t>", 1, 1)
    d.add_variable(UserVariable(name="n", src_loc=loc))
    e = d.lookup("n")
    assert isinstance(e, UserVariable)
    assert e.name == "n"


# ---------------------------------------------------------------------------
# resolve() — happy paths
# ---------------------------------------------------------------------------


def test_resolve_empty_program_succeeds():
    prog = parse("")
    d = resolve(prog)
    assert isinstance(d, Dictionary)


def test_resolve_only_literals_no_words():
    prog = parse("1 2 3")
    resolve(prog)  # no error


def test_resolve_simple_arithmetic():
    prog = parse("1 2 +")
    resolve(prog)


def test_resolve_definition_then_call():
    prog = parse(": square dup * ; 5 square")
    d = resolve(prog)
    assert d.lookup("square") is prog.definitions[0]


def test_resolve_call_inside_definition_body():
    prog = parse(": foo bar ; : bar 1 ; foo")
    # Both definitions are pre-registered, so foo's body can resolve bar
    resolve(prog)


def test_resolve_redefinition_keeps_later():
    prog = parse(": foo 1 ; : foo 2 ; foo")
    d = resolve(prog)
    # The dictionary should reflect the LATER definition (Forth semantics)
    assert d.lookup("foo") is prog.definitions[-1]


def test_resolve_variable_declaration_then_use():
    prog = parse("VARIABLE n n @")
    d = resolve(prog)
    assert isinstance(d.lookup("n"), UserVariable)


def test_resolve_variable_used_inside_definition():
    prog = parse("VARIABLE n : get-n n @ ;")
    resolve(prog)


def test_resolve_inside_if_then():
    prog = parse("flag IF 1 + THEN")
    with pytest.raises(UnresolvedWordError):
        resolve(prog)  # 'flag' is unresolved


def test_resolve_inside_begin_until():
    prog = parse(": loop-on BEGIN 1 + cond UNTIL ;")
    with pytest.raises(UnresolvedWordError) as exc:
        resolve(prog)
    assert exc.value.name == "cond"


def test_resolve_inside_do_loop():
    prog = parse("10 0 DO bogus LOOP")
    with pytest.raises(UnresolvedWordError) as exc:
        resolve(prog)
    assert exc.value.name == "bogus"


def test_resolve_inside_while_repeat():
    prog = parse("BEGIN cond WHILE bogus REPEAT")
    with pytest.raises(UnresolvedWordError) as exc:
        resolve(prog)
    assert exc.value.name in ("cond", "bogus")  # whichever surfaces first


# ---------------------------------------------------------------------------
# resolve() — unresolved error carries name + src_loc
# ---------------------------------------------------------------------------


def test_unresolved_word_in_main_raises_with_loc():
    src = "1\n  unknown-word"
    prog = parse(src, file="x.fs")
    with pytest.raises(UnresolvedWordError) as exc:
        resolve(prog)
    err = exc.value
    assert err.name == "unknown-word"
    assert err.src_loc.file == "x.fs"
    assert err.src_loc.line == 2
    assert err.src_loc.col == 3
    assert "x.fs:2:3" in str(err)


def test_unresolved_word_in_definition_body_raises():
    prog = parse(": broken bogus ;")
    with pytest.raises(UnresolvedWordError) as exc:
        resolve(prog)
    assert exc.value.name == "bogus"


def test_variable_used_without_declaration_raises():
    prog = parse("n @")
    with pytest.raises(UnresolvedWordError) as exc:
        resolve(prog)
    assert exc.value.name == "n"


def test_variable_declared_only_in_one_scope_is_globally_visible():
    # `VARIABLE n` inside a definition declares n at the top-level dictionary
    # — Forth has no lexical scoping.
    prog = parse(": declare-n VARIABLE n ; declare-n n @")
    resolve(prog)


# ---------------------------------------------------------------------------
# Dictionary instance independence
# ---------------------------------------------------------------------------


def test_two_dictionary_instances_are_independent():
    a = standard_dictionary()
    b = standard_dictionary()
    b.add_builtin(BuiltinWord("UNIQUE", StackEffect(0, 0), "", "stack"))
    assert "unique" in b
    assert "unique" not in a


def test_resolve_with_explicit_dictionary_does_not_mutate_a_different_one():
    a = standard_dictionary()
    prog = parse(": foo 1 ;")
    resolve(prog, dictionary=a)
    fresh = standard_dictionary()
    assert "foo" in a
    assert "foo" not in fresh
