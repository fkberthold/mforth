"""Unit tests for the mforth parser.

Bead mforth-10t.5. TDD-first.

Pragmatic call (also noted in src/mforth/parse.py): the parser is purely
syntactic. It does not synthesise `VarRef` from `VARIABLE`/`@`/`!`; that
is the resolver's job (bead mforth-10t.6). `VARIABLE`/`@`/`!` appear here
as plain WordCall instances.
"""

from __future__ import annotations

import pytest

from mforth.parse import (
    Begin,
    Definition,
    DoLoop,
    IfThen,
    LitFloat,
    LitInt,
    LitStr,
    ParseError,
    Program,
    WordCall,
    parse,
)


def p(src: str, file: str = "<test>") -> Program:
    return parse(src, file=file)


# ---------------------------------------------------------------------------
# Empty / trivial
# ---------------------------------------------------------------------------


def test_empty_source_produces_empty_program():
    prog = p("")
    assert prog.definitions == []
    assert prog.main == []


def test_whitespace_only_source_produces_empty_program():
    prog = p("   \n\t  ")
    assert prog.definitions == []
    assert prog.main == []


# ---------------------------------------------------------------------------
# Literals + simple word calls in main
# ---------------------------------------------------------------------------


def test_single_integer_in_main():
    prog = p("42")
    assert prog.definitions == []
    assert len(prog.main) == 1
    assert isinstance(prog.main[0], LitInt)
    assert prog.main[0].value == 42


def test_integer_pair_with_word_call():
    prog = p("1 2 +")
    assert prog.definitions == []
    assert [type(t).__name__ for t in prog.main] == ["LitInt", "LitInt", "WordCall"]
    assert prog.main[0].value == 1
    assert prog.main[1].value == 2
    assert prog.main[2].name == "+"


def test_dot_quote_string_becomes_litstr():
    prog = p('." hello"')
    assert len(prog.main) == 1
    assert isinstance(prog.main[0], LitStr)
    assert prog.main[0].value == "hello"


def test_s_quote_string_becomes_litstr():
    prog = p('S" world"')
    assert isinstance(prog.main[0], LitStr)
    assert prog.main[0].value == "world"


# ---------------------------------------------------------------------------
# Float literals (bead mforth-xk7)
# ---------------------------------------------------------------------------


def test_single_float_in_main():
    prog = p("3.14")
    assert prog.definitions == []
    assert len(prog.main) == 1
    assert isinstance(prog.main[0], LitFloat)
    assert prog.main[0].value == 3.14
    assert isinstance(prog.main[0].value, float)


def test_float_literal_carries_src_loc():
    prog = p("  0.95")
    assert isinstance(prog.main[0], LitFloat)
    assert prog.main[0].src_loc.line == 1
    assert prog.main[0].src_loc.col == 3


def test_float_and_int_mixed():
    prog = p("1 2.5 +")
    assert [type(t).__name__ for t in prog.main] == ["LitInt", "LitFloat", "WordCall"]
    assert prog.main[0].value == 1
    assert prog.main[1].value == 2.5
    assert prog.main[2].name == "+"


def test_float_in_definition_body():
    prog = p(": scale 0.95 * ;")
    d = prog.definitions[0]
    assert d.name == "scale"
    assert isinstance(d.body[0], LitFloat) and d.body[0].value == 0.95
    assert isinstance(d.body[1], WordCall) and d.body[1].name == "*"


def test_negative_float_in_main():
    prog = p("-1.5")
    assert isinstance(prog.main[0], LitFloat)
    assert prog.main[0].value == -1.5


def test_scientific_float_in_main():
    prog = p("1.0e-3")
    assert isinstance(prog.main[0], LitFloat)
    assert prog.main[0].value == 0.001


def test_three_token_dot_decomposition_stays_three_terms():
    # Negative case: `3 . 14` (whitespace-separated) must NOT collapse
    # into a single LitFloat. The lex layer keeps them as three tokens;
    # the parser must produce LitInt, WordCall("."), LitInt — not one
    # LitFloat(3.14).
    prog = p("3 . 14")
    assert [type(t).__name__ for t in prog.main] == ["LitInt", "WordCall", "LitInt"]
    assert prog.main[0].value == 3
    assert prog.main[1].name == "."
    assert prog.main[2].value == 14


def test_trailing_dot_falls_through_to_word():
    # Negative case: `3.` is not a float by the regex (no fractional
    # digits). It lexes as WORD and the parser produces a WordCall.
    # Dictionary resolution downstream will fail it as unknown — which
    # is the "error" path called out in the bead's acceptance bullet.
    prog = p("3.")
    assert len(prog.main) == 1
    assert isinstance(prog.main[0], WordCall)
    assert prog.main[0].name == "3."


def test_word_call_preserves_name_and_loc():
    prog = p("foo")
    assert isinstance(prog.main[0], WordCall)
    assert prog.main[0].name == "foo"
    assert prog.main[0].src_loc.line == 1
    assert prog.main[0].src_loc.col == 1


# ---------------------------------------------------------------------------
# Definitions (: name body ;)
# ---------------------------------------------------------------------------


def test_simple_colon_definition():
    prog = p(": square dup * ;")
    assert prog.main == []
    assert len(prog.definitions) == 1
    d = prog.definitions[0]
    assert d.name == "square"
    assert [type(t).__name__ for t in d.body] == ["WordCall", "WordCall"]
    assert [t.name for t in d.body] == ["dup", "*"]


def test_empty_definition_body():
    prog = p(": noop ;")
    assert prog.definitions[0].name == "noop"
    assert prog.definitions[0].body == []


def test_definition_with_literal_in_body():
    prog = p(": add5 5 + ;")
    d = prog.definitions[0]
    assert isinstance(d.body[0], LitInt) and d.body[0].value == 5
    assert isinstance(d.body[1], WordCall) and d.body[1].name == "+"


def test_two_definitions_then_main_call():
    prog = p(": a 1 ; : b 2 ; a b +")
    names = [d.name for d in prog.definitions]
    assert names == ["a", "b"]
    assert [type(t).__name__ for t in prog.main] == ["WordCall", "WordCall", "WordCall"]


def test_unclosed_definition_raises():
    with pytest.raises(ParseError) as exc:
        p(": square dup *")
    assert "definition" in str(exc.value).lower() or "expected" in str(exc.value).lower()


def test_definition_name_missing_raises():
    with pytest.raises(ParseError):
        p(": ;")


def test_nested_definition_raises():
    # Forth does not allow ': inside an open : ... ; definition.
    with pytest.raises(ParseError):
        p(": outer : inner ; ;")


def test_semicolon_at_top_level_raises():
    with pytest.raises(ParseError):
        p("1 ;")


# ---------------------------------------------------------------------------
# IF / ELSE / THEN
# ---------------------------------------------------------------------------


def test_if_then_no_else():
    prog = p("flag IF body THEN")
    assert isinstance(prog.main[0], WordCall) and prog.main[0].name == "flag"
    if_node = prog.main[1]
    assert isinstance(if_node, IfThen)
    assert [t.name for t in if_node.then_body] == ["body"]
    assert if_node.else_body == []


def test_if_else_then():
    prog = p("flag IF a ELSE b THEN")
    if_node = prog.main[1]
    assert isinstance(if_node, IfThen)
    assert [t.name for t in if_node.then_body] == ["a"]
    assert [t.name for t in if_node.else_body] == ["b"]


def test_if_then_lowercase_aliases():
    prog = p("flag if a else b then")
    assert isinstance(prog.main[1], IfThen)


def test_nested_if_then():
    prog = p("flag IF a IF b THEN c THEN")
    outer = prog.main[1]
    assert isinstance(outer, IfThen)
    assert [type(t).__name__ for t in outer.then_body] == ["WordCall", "IfThen", "WordCall"]


def test_unclosed_if_raises():
    with pytest.raises(ParseError):
        p("flag IF body")


def test_else_without_if_raises():
    with pytest.raises(ParseError):
        p("ELSE")


def test_then_without_if_raises():
    with pytest.raises(ParseError):
        p("THEN")


# ---------------------------------------------------------------------------
# BEGIN / UNTIL  and  BEGIN / WHILE / REPEAT
# ---------------------------------------------------------------------------


def test_begin_until():
    prog = p("BEGIN body cond UNTIL")
    node = prog.main[0]
    assert isinstance(node, Begin)
    assert node.kind == "until"
    assert [t.name for t in node.body] == ["body", "cond"]
    assert node.cond_body == []


def test_begin_while_repeat():
    prog = p("BEGIN cond WHILE body REPEAT")
    node = prog.main[0]
    assert isinstance(node, Begin)
    assert node.kind == "while-repeat"
    assert [t.name for t in node.body] == ["cond"]
    assert [t.name for t in node.cond_body] == ["body"]


def test_unclosed_begin_raises():
    with pytest.raises(ParseError):
        p("BEGIN body")


def test_while_without_begin_raises():
    with pytest.raises(ParseError):
        p("WHILE")


def test_repeat_without_begin_raises():
    with pytest.raises(ParseError):
        p("REPEAT")


def test_until_without_begin_raises():
    with pytest.raises(ParseError):
        p("UNTIL")


# ---------------------------------------------------------------------------
# DO / LOOP
# ---------------------------------------------------------------------------


def test_do_loop_basic():
    prog = p("10 0 DO i . LOOP")
    do_node = prog.main[2]
    assert isinstance(do_node, DoLoop)
    assert [type(t).__name__ for t in do_node.body] == ["WordCall", "WordCall"]
    assert [t.name for t in do_node.body] == ["i", "."]


def test_unclosed_do_raises():
    with pytest.raises(ParseError):
        p("10 0 DO body")


def test_loop_without_do_raises():
    with pytest.raises(ParseError):
        p("LOOP")


# ---------------------------------------------------------------------------
# VARIABLE / @ / !  (parser treats these as plain word calls — resolution is
# bead mforth-10t.6's responsibility)
# ---------------------------------------------------------------------------


def test_variable_declaration_is_plain_wordcalls():
    prog = p("VARIABLE n")
    assert [type(t).__name__ for t in prog.main] == ["WordCall", "WordCall"]
    assert [t.name for t in prog.main] == ["VARIABLE", "n"]


def test_fetch_and_store_are_plain_wordcalls():
    prog = p("n @ 1 + n !")
    assert [type(t).__name__ for t in prog.main] == [
        "WordCall", "WordCall", "LitInt", "WordCall", "WordCall", "WordCall",
    ]
    assert prog.main[1].name == "@"
    assert prog.main[5].name == "!"


# ---------------------------------------------------------------------------
# Control flow inside a definition
# ---------------------------------------------------------------------------


def test_control_flow_inside_definition():
    src = ": maybe-double dup IF 2 * THEN ;"
    prog = p(src)
    d = prog.definitions[0]
    assert d.name == "maybe-double"
    assert [type(t).__name__ for t in d.body] == ["WordCall", "IfThen"]
    if_node = d.body[1]
    assert [t.name for t in if_node.then_body if hasattr(t, "name")] == ["*"]


def test_nested_begin_inside_if_inside_definition():
    src = ": loop-on-flag flag IF BEGIN body cond UNTIL THEN ;"
    prog = p(src)
    d = prog.definitions[0]
    if_node = d.body[1]
    assert isinstance(if_node, IfThen)
    begin_node = if_node.then_body[0]
    assert isinstance(begin_node, Begin)
    assert begin_node.kind == "until"


# ---------------------------------------------------------------------------
# Source-location propagation
# ---------------------------------------------------------------------------


def test_src_loc_on_literal_and_wordcall():
    prog = p("42\n  foo")
    lit = prog.main[0]
    word = prog.main[1]
    assert lit.src_loc.line == 1 and lit.src_loc.col == 1
    assert word.src_loc.line == 2 and word.src_loc.col == 3


def test_src_loc_on_definition_points_at_colon():
    prog = p("\n  : foo bar ;")
    d = prog.definitions[0]
    assert d.src_loc.line == 2
    assert d.src_loc.col == 3


def test_src_loc_on_if_node_points_at_if_keyword():
    prog = p("  flag IF body THEN")
    if_node = prog.main[1]
    assert if_node.src_loc.line == 1
    assert if_node.src_loc.col == 8  # col of 'IF'


def test_src_loc_on_begin_node():
    prog = p("BEGIN body cond UNTIL")
    node = prog.main[0]
    assert node.src_loc.line == 1 and node.src_loc.col == 1


def test_src_loc_on_do_node():
    prog = p("10 0 DO i LOOP")
    do_node = prog.main[2]
    assert do_node.src_loc.line == 1
    assert do_node.src_loc.col == 6  # col of 'DO'


def test_parse_error_carries_location():
    with pytest.raises(ParseError) as exc:
        p("foo\n\n   IF body")
    err = exc.value
    assert err.file == "<test>"
    assert err.line == 3
    assert err.col == 4
    assert "<test>:3:4" in str(err)


# ---------------------------------------------------------------------------
# Lexer-error passthrough
# ---------------------------------------------------------------------------


def test_lex_error_propagates_through_parser():
    from mforth.lex import LexError

    with pytest.raises(LexError):
        p('." unterminated')


# ---------------------------------------------------------------------------
# Declared stack-effect comments on `:` definitions (mforth-6dh)
# ---------------------------------------------------------------------------
# A paren-comment containing `--` that immediately follows the `:` name
# is parsed as a declared stack effect and attached to the Definition as
# `declared_effect = (in_arity, out_arity)`. Stack-effect comments anywhere
# else (in main body, inside a `:` body, before the name) are treated as
# plain comments and discarded.


def test_definition_captures_declared_effect():
    prog = p(": square ( a -- b ) dup * ;")
    defn = prog.definitions[0]
    assert defn.declared_effect == (1, 1)


def test_definition_without_declared_effect_is_none():
    prog = p(": square dup * ;")
    defn = prog.definitions[0]
    assert defn.declared_effect is None


def test_definition_with_zero_zero_effect():
    prog = p(": noop ( -- ) ;")
    defn = prog.definitions[0]
    assert defn.declared_effect == (0, 0)


def test_effect_comment_inside_body_is_just_a_comment():
    # An effect-shaped comment INSIDE the body is not a declared effect —
    # it's discarded like any other comment, and the definition has no
    # declared_effect attached.
    prog = p(": foo dup ( a -- a a ) drop ;")
    defn = prog.definitions[0]
    assert defn.declared_effect is None


def test_effect_comment_in_main_is_ignored():
    # Stack-effect comments at top level don't attach to anything; they
    # parse cleanly and the main body is unaffected.
    prog = p("1 ( a -- b ) 2 +")
    # No definitions; main has [1, 2, +]
    assert prog.definitions == []
    assert len(prog.main) == 3
