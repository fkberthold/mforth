"""Unit tests for the mforth lexer.

Bead mforth-10t.4. TDD-first.
"""

from __future__ import annotations

import pytest

from mforth.lex import LexError, Token, TokenKind, tokenize


def lex(src: str, file: str = "<test>") -> list[Token]:
    return list(tokenize(src, file=file))


# ---------------------------------------------------------------------------
# Empty / EOF
# ---------------------------------------------------------------------------


def test_empty_source_produces_only_eof():
    toks = lex("")
    assert [t.kind for t in toks] == [TokenKind.EOF]
    assert toks[0].file == "<test>"


def test_whitespace_only_source_produces_only_eof():
    toks = lex("  \t\n   ")
    assert [t.kind for t in toks] == [TokenKind.EOF]


def test_eof_token_carries_location():
    toks = lex("foo")
    eof = toks[-1]
    assert eof.kind == TokenKind.EOF
    assert eof.file == "<test>"
    assert eof.line >= 1 and eof.col >= 1


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------


def test_single_decimal_integer():
    toks = lex("42")
    assert toks[0].kind == TokenKind.NUMBER
    assert toks[0].text == "42"
    assert toks[0].value == 42


def test_negative_integer():
    toks = lex("-7")
    assert toks[0].kind == TokenKind.NUMBER
    assert toks[0].value == -7


def test_positive_sign_integer():
    toks = lex("+13")
    assert toks[0].kind == TokenKind.NUMBER
    assert toks[0].value == 13


def test_zero():
    toks = lex("0")
    assert toks[0].kind == TokenKind.NUMBER
    assert toks[0].value == 0


def test_multiple_numbers_separated_by_whitespace():
    toks = lex("1 2 3")
    assert [t.kind for t in toks[:-1]] == [TokenKind.NUMBER] * 3
    assert [t.value for t in toks[:-1]] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------


def test_plus_is_word_not_number():
    toks = lex("+")
    assert toks[0].kind == TokenKind.WORD
    assert toks[0].text == "+"


def test_minus_alone_is_word_not_number():
    toks = lex("-")
    assert toks[0].kind == TokenKind.WORD
    assert toks[0].text == "-"


def test_alphanumeric_word_with_dash():
    toks = lex("foo-bar")
    assert toks[0].kind == TokenKind.WORD
    assert toks[0].text == "foo-bar"


def test_word_starting_with_digit_but_not_number():
    # "2dup" is a classic Forth word: it is whitespace-delimited and not parseable
    # as a pure integer, so it is a WORD.
    toks = lex("2dup")
    assert toks[0].kind == TokenKind.WORD
    assert toks[0].text == "2dup"


def test_typical_forth_words():
    toks = lex("@ ! +! 0= ?dup over swap")
    kinds = [t.kind for t in toks[:-1]]
    texts = [t.text for t in toks[:-1]]
    assert kinds == [TokenKind.WORD] * 7
    assert texts == ["@", "!", "+!", "0=", "?dup", "over", "swap"]


# ---------------------------------------------------------------------------
# COLON / SEMICOLON (whitespace-delimited only)
# ---------------------------------------------------------------------------


def test_colon_alone_is_colon_token():
    toks = lex(":")
    assert toks[0].kind == TokenKind.COLON
    assert toks[0].text == ":"


def test_semicolon_alone_is_semicolon_token():
    toks = lex(";")
    assert toks[0].kind == TokenKind.SEMICOLON
    assert toks[0].text == ";"


def test_colon_definition_full():
    toks = lex(": square dup * ;")
    assert [t.kind for t in toks] == [
        TokenKind.COLON,
        TokenKind.WORD,
        TokenKind.WORD,
        TokenKind.WORD,
        TokenKind.SEMICOLON,
        TokenKind.EOF,
    ]
    assert [t.text for t in toks[:-1]] == [":", "square", "dup", "*", ";"]


def test_colon_without_trailing_space_is_a_word():
    # ":foo" is a single whitespace-delimited token, so it's a WORD, not COLON+WORD.
    # This matches Forth's whitespace-delimited tokenization discipline.
    toks = lex(":foo")
    assert toks[0].kind == TokenKind.WORD
    assert toks[0].text == ":foo"


def test_semicolon_attached_to_word_is_a_word():
    toks = lex("foo;")
    assert toks[0].kind == TokenKind.WORD
    assert toks[0].text == "foo;"


# ---------------------------------------------------------------------------
# Paren comments (discarded, nestable, error on unterminated)
# ---------------------------------------------------------------------------


def test_paren_comment_is_discarded():
    toks = lex("1 ( this is a comment ) 2")
    kinds = [t.kind for t in toks]
    assert kinds == [TokenKind.NUMBER, TokenKind.NUMBER, TokenKind.EOF]
    assert [t.value for t in toks[:-1]] == [1, 2]


def test_paren_comment_spans_newlines():
    toks = lex("1 ( first\n  second )\n2")
    kinds = [t.kind for t in toks]
    assert kinds == [TokenKind.NUMBER, TokenKind.NUMBER, TokenKind.EOF]


def test_nested_paren_comment():
    toks = lex("1 ( outer ( inner ) more outer ) 2")
    kinds = [t.kind for t in toks]
    assert kinds == [TokenKind.NUMBER, TokenKind.NUMBER, TokenKind.EOF]


def test_empty_paren_comment():
    toks = lex("1 ( ) 2")
    kinds = [t.kind for t in toks]
    assert kinds == [TokenKind.NUMBER, TokenKind.NUMBER, TokenKind.EOF]


def test_unterminated_paren_comment_raises():
    with pytest.raises(LexError) as exc_info:
        lex("1 ( open without close")
    err = exc_info.value
    assert err.file == "<test>"
    assert err.line == 1
    assert err.col == 3  # position of the opening '('


def test_paren_at_eof_raises():
    with pytest.raises(LexError):
        lex("(")


# ---------------------------------------------------------------------------
# Line comments (\\)
# ---------------------------------------------------------------------------


def test_line_comment_discards_to_eol():
    toks = lex("1 \\ rest of line\n2")
    kinds = [t.kind for t in toks]
    assert kinds == [TokenKind.NUMBER, TokenKind.NUMBER, TokenKind.EOF]
    assert [t.value for t in toks[:-1]] == [1, 2]


def test_line_comment_at_end_of_file_without_newline():
    toks = lex("1 \\ trailing")
    kinds = [t.kind for t in toks]
    assert kinds == [TokenKind.NUMBER, TokenKind.EOF]


def test_backslash_inside_word_is_part_of_word():
    # The line-comment marker is the whitespace-delimited "\\" — not any
    # arbitrary backslash. "foo\\bar" is one WORD.
    toks = lex("foo\\bar")
    assert toks[0].kind == TokenKind.WORD
    assert toks[0].text == "foo\\bar"


# ---------------------------------------------------------------------------
# Strings: ." and S"
# ---------------------------------------------------------------------------


def test_dot_quote_string_basic():
    toks = lex('." hello"')
    assert toks[0].kind == TokenKind.STRING_DOT_QUOTE
    assert toks[0].value == "hello"


def test_dot_quote_string_with_spaces():
    toks = lex('." hello world"')
    assert toks[0].kind == TokenKind.STRING_DOT_QUOTE
    assert toks[0].value == "hello world"


def test_dot_quote_empty_string():
    toks = lex('." "')
    assert toks[0].kind == TokenKind.STRING_DOT_QUOTE
    assert toks[0].value == ""


def test_s_quote_string_basic():
    toks = lex('S" hello"')
    assert toks[0].kind == TokenKind.STRING_S_QUOTE
    assert toks[0].value == "hello"


def test_s_quote_string_with_spaces():
    toks = lex('S" hello world"')
    assert toks[0].kind == TokenKind.STRING_S_QUOTE
    assert toks[0].value == "hello world"


def test_two_strings_on_one_line():
    toks = lex('." a" S" b"')
    assert toks[0].kind == TokenKind.STRING_DOT_QUOTE
    assert toks[0].value == "a"
    assert toks[1].kind == TokenKind.STRING_S_QUOTE
    assert toks[1].value == "b"


def test_unterminated_dot_quote_raises():
    with pytest.raises(LexError) as exc_info:
        lex('." unterminated')
    err = exc_info.value
    assert err.file == "<test>"
    assert err.line == 1


def test_unterminated_s_quote_raises():
    with pytest.raises(LexError):
        lex('S" unterminated')


def test_dot_quote_attached_to_word_is_word():
    # `."hello"` (no space between `."` and `hello`) is a single whitespace-
    # delimited token → WORD. This matches Forth's parsing-word convention.
    toks = lex('."hello"')
    assert toks[0].kind == TokenKind.WORD
    assert toks[0].text == '."hello"'


# ---------------------------------------------------------------------------
# Source-location tracking
# ---------------------------------------------------------------------------


def test_source_locations_track_line_and_col():
    toks = lex("1\n  2\n42")
    assert toks[0].value == 1 and toks[0].line == 1 and toks[0].col == 1
    assert toks[1].value == 2 and toks[1].line == 2 and toks[1].col == 3
    assert toks[2].value == 42 and toks[2].line == 3 and toks[2].col == 1


def test_columns_advance_with_tabs_as_single_chars():
    # Conservative: each tab advances column by 1. Mlog and Forth aren't tab-
    # sensitive; LSP clients can adjust if they care.
    toks = lex("\t42")
    assert toks[0].value == 42
    assert toks[0].line == 1
    assert toks[0].col == 2


def test_every_token_carries_file_name():
    toks = lex("1 2 + .\" hi\" S\" bye\" : foo ;", file="example.fs")
    for t in toks:
        assert t.file == "example.fs"


def test_location_after_paren_comment():
    toks = lex("( comment )foo")
    # First (and only) non-EOF token should be WORD foo at col 12
    word = toks[0]
    assert word.kind == TokenKind.WORD
    assert word.text == "foo"
    assert word.line == 1
    assert word.col == 12


# ---------------------------------------------------------------------------
# LexError carries location
# ---------------------------------------------------------------------------


def test_lex_error_carries_full_location_on_multi_line_input():
    src = 'foo bar\n\n  ." oops'
    with pytest.raises(LexError) as exc_info:
        lex(src, file="x.fs")
    err = exc_info.value
    assert err.file == "x.fs"
    assert err.line == 3
    assert err.col == 3  # the `."` starts at col 3 of line 3
    assert "x.fs:3:3" in str(err)
