"""mforth language server — pygls stdio server + pure analyzers.

Beads mforth-10t.23 (diagnostics) + .24 (hover) + .25 (completion). The
server reuses the same lex / parse / resolve / stackcheck pipeline as
the compiler so the LSP and compiler agree on every diagnostic by
construction.

Architecture
============

Two layers:

1. **Pure analyzers** — :func:`analyze_document` and
   :func:`analyze_sidecar` are deterministic functions
   ``(text, uri) -> list[Diagnostic]``. They never touch the network,
   the filesystem, or pygls — they just run the analyzer pipeline,
   convert any error into a single :class:`lsprotocol.types.Diagnostic`
   anchored at its ``src_loc``, and return. This is the surface the
   unit tests exercise.

2. **pygls server** — :func:`create_server` instantiates a
   :class:`pygls.lsp.server.LanguageServer`, registers handlers for
   ``textDocument/didOpen`` and ``textDocument/didChange``, and wires
   them to the appropriate analyzer (forth vs. sidecar, chosen by the
   document URI suffix). The handlers call
   ``ls.text_document_publish_diagnostics(...)`` with the analyzer
   output — so the LSP <-> analyzer contract is exact and pinned by
   ``test_did_open_handler_publishes_diagnostics_matching_analyzer``.

Error → diagnostic mapping
==========================

Every error type the pipeline raises carries a 1-based ``(file, line,
col)`` source location, either as bare attributes (``LexError``,
``ParseError``) or wrapped in a :class:`mforth.parse.SrcLoc`
(``UnresolvedWordError``, ``StackError``). LSP positions are 0-based,
so we subtract 1. The diagnostic range spans a single character;
downstream beads (.24 hover, etc.) can widen ranges per word once the
analyzer surfaces end positions.

The pipeline halts at the first error in v1 — the parser is not
error-recovering. Multi-error documents will surface one diagnostic at
a time until the underlying parser learns error recovery. Tracked as a
followup, not a v1 bug.

Sidecar handling
================

Documents with a ``.world.toml`` suffix are dispatched to
:func:`analyze_sidecar`, which delegates to
:func:`mforth.backend.sidecar.parse_sidecar`. ``SidecarError`` is
mapped to a diagnostic at line 0, col 0 of the sidecar URI — TOML
parse failures do not carry per-token positions in the standard
``tomllib`` exception surface, so v1 anchors at file head. Improving
this (parsing the ``TOMLDecodeError.lineno``/``.colno`` attributes)
is filed as a followup.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from typing import List

from lsprotocol import types as lsp
from pygls.lsp.server import LanguageServer

from mforth.backend.sidecar import SidecarError, parse_sidecar
from mforth.dictionary import (
    BuiltinWord,
    Definition,
    Macro,
    UnresolvedWordError,
    UserVariable,
    resolve,
    standard_dictionary,
)
from mforth.expand import (
    CellBoundaryError,
    ExpandError,
    PurityError,
    expand,
)
from mforth.lex import LexError
from mforth.parse import (
    Begin,
    DoLoop,
    IfThen,
    LitFloat,
    LitInt,
    LitStr,
    ParseError,
    Program,
    SrcLoc,
    VarRef,
    WordCall,
    parse,
)
from mforth.stackcheck import StackError, stackcheck


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


SERVER_NAME = "mforth-lsp"
DIAGNOSTIC_SOURCE = "mforth"
SIDECAR_SUFFIX = ".world.toml"


# ---------------------------------------------------------------------------
# Diagnostic helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PosOneBased:
    """Internal carrier: 1-based source location before LSP conversion."""

    line: int
    col: int


def _lsp_position(line_1based: int, col_1based: int) -> lsp.Position:
    """Convert 1-based (line, col) from the compiler error surface to
    0-based LSP positions. Clamps at zero defensively — a malformed
    error with line=0 would otherwise produce a negative position."""
    return lsp.Position(
        line=max(0, line_1based - 1),
        character=max(0, col_1based - 1),
    )


def _diag(line_1based: int, col_1based: int, message: str) -> lsp.Diagnostic:
    start = _lsp_position(line_1based, col_1based)
    end = lsp.Position(line=start.line, character=start.character + 1)
    return lsp.Diagnostic(
        range=lsp.Range(start=start, end=end),
        message=message,
        severity=lsp.DiagnosticSeverity.Error,
        source=DIAGNOSTIC_SOURCE,
    )


# ---------------------------------------------------------------------------
# Pure analyzers
# ---------------------------------------------------------------------------


def analyze_document(text: str, *, uri: str) -> List[lsp.Diagnostic]:
    """Run lex / parse / resolve / stackcheck on `text` and return any
    diagnostics. Always returns a list (possibly empty).

    Pre-seeds the dictionary with sidecar-declared link names so
    references like ``display PRINTFLUSH`` resolve cleanly when a
    sibling ``<stem>.world.toml`` declares ``[links.display]``. Mirrors
    the pre-seed pattern in ``backend/runner.py`` (.14 ship). Without
    this, every sidecar-bound mforth-name surfaces as
    ``unresolved word`` even though CLI compile + LSP completion both
    know about it (regression: mforth-pr8).
    """
    file = _file_from_uri(uri)
    try:
        program = parse(text, file=file)
    except LexError as e:
        return [_diag(e.line, e.col, e.message)]
    except ParseError as e:
        return [_diag(e.line, e.col, e.message)]

    # Pre-seed sidecar link names. Silent-degrades on missing /
    # malformed sidecar (same convention as .25's
    # _sidecar_link_candidates — sidecar validation errors surface via
    # the separate analyze_sidecar diagnostics path).
    dictionary = standard_dictionary()
    sidecar_src_loc = SrcLoc(file=file, line=1, col=1)
    for link_name, _link_type in _sidecar_link_candidates(uri):
        if link_name not in dictionary:
            dictionary.add_variable(
                UserVariable(name=link_name, src_loc=sidecar_src_loc)
            )

    try:
        dictionary = resolve(program, dictionary=dictionary)
    except UnresolvedWordError as e:
        return [_diag(e.src_loc.line, e.src_loc.col, str(e.args[0]).split(": ", 2)[-1])]

    # Phase-0 expand (bead mforth-7h1.4, design D13). Slotted BETWEEN resolve
    # and stackcheck so the LSP analyzes the post-expansion AST and agrees with
    # ``mforth compile`` (which orders the pipeline identically — see
    # ``optimize.compile_text``). Without this, a defining-word / macro document
    # diverges: stackcheck would see un-stripped meta-words and either report a
    # phantom ``unresolved word 'CREATE'`` or crash on a surviving ``Macro``
    # entry. The meta-errors (``CellBoundaryError`` / ``PurityError`` /
    # ``ExpandError``) carry no per-token src_loc in v1, so they anchor at the
    # file head (line 1, col 1) — mirrors the ``analyze_sidecar`` convention.
    try:
        program = expand(program, dictionary)
    except (CellBoundaryError, PurityError, ExpandError) as e:
        return [_diag(1, 1, str(e.args[0]) if e.args else str(e))]

    try:
        stackcheck(program, dictionary=dictionary)
    except UnresolvedWordError as e:
        return [_diag(e.src_loc.line, e.src_loc.col, str(e.args[0]).split(": ", 2)[-1])]
    except StackError as e:
        return [_diag(e.src_loc.line, e.src_loc.col, e.message)]

    return []


def analyze_sidecar(text: str, *, uri: str) -> List[lsp.Diagnostic]:
    """Parse a `.world.toml` sidecar and return any diagnostics."""
    source = _file_from_uri(uri)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return [_diag(1, 1, f"TOML parse error: {e}")]

    try:
        parse_sidecar(data, source=source)
    except SidecarError as e:
        return [_diag(1, 1, e.message)]

    return []


def _file_from_uri(uri: str) -> str:
    """Best-effort URI → filename for error messages. The LSP doesn't
    require this to be a real path — it's just what shows up in the
    compiler error prefix."""
    if uri.startswith("file://"):
        return uri[len("file://") :]
    return uri


# ---------------------------------------------------------------------------
# Hover (bead mforth-10t.24)
# ---------------------------------------------------------------------------
#
# `hover_for(text, *, uri, position)` mirrors the `analyze_document` seam
# from .23: a pure function the test harness can drive directly without
# spinning up pygls. The hover handler registered on the server is a
# thin shim that pulls the document text from the workspace and calls
# this function.
#
# Algorithm:
#
# 1. Re-run lex → parse → resolve → stackcheck. If any stage fails, the
#    pipeline cannot give a meaningful answer about a term at the
#    cursor, so we return None (no hover). Diagnostics surface the
#    error via the separate publishDiagnostics path; hover stays
#    silent rather than echoing the failure.
#
# 2. Walk every Term in the AST (main + every definition body, and
#    recursively into IfThen/Begin/DoLoop bodies). For each term,
#    compute its (line, start_col, end_col) extent and check whether
#    the cursor lands within that extent.
#
# 3. Classify the matched term:
#      * WordCall → look up in dictionary; format per entry kind
#        (BuiltinWord / Definition / UserVariable).
#      * LitInt / LitStr → format as a literal hover.
#    For BuiltinWord, the doc field is rendered verbatim; if it's
#    empty (none today, but defensive for future entries) the
#    fallback `(no documentation)` is used.
#
# Hover content format: plain text (`MarkupKind.PlainText`). Plain text
# avoids Markdown-escaping issues with `<`, `>`, `*`, `+` — all of
# which appear verbatim in mforth stack-effect notation and
# arithmetic primitives. Editors render `( a -- b )` and `1 -- 1`
# fine as-is. If a future bead wants Markdown bullets we'll switch
# globally.
#
# Position fidelity: token extents are derived from the source
# location (1-based line, col) and the rendered length of the term
# (`name` for WordCall, `str(value)` for LitInt, `len(value) + 2` for
# LitStr to cover surrounding quotes). The lexer does not export
# end-column today; this approximation is correct for every v1
# fixture and degrades gracefully for unusual whitespace (the
# hover-for-whitespace test pins the "no hover on a space" case).


_HOVER_FAILED = object()  # sentinel for failed analysis


def hover_for(
    text: str, *, uri: str, position: lsp.Position
) -> lsp.Hover | None:
    """Return a hover for the term under ``position``, or None if the
    cursor isn't on a recognizable term or analysis fails."""
    file = _file_from_uri(uri)

    # Hover needs the AST + the dictionary at minimum. parse/resolve
    # failures kill hover (we have nothing to point at). Stackcheck
    # failures are NON-fatal — without inferred effects we still
    # display the literal/built-in shape; user-definition effects
    # render as `( ? -- ? )`.
    try:
        program = parse(text, file=file)
    except (LexError, ParseError):
        return None

    # Positioning uses the PRE-EXPAND parse (above): a stamped child like
    # ``TROMBONES`` is inlined to a literal push by expand, so its WordCall
    # only exists in the un-expanded AST. But the EFFECT we render must come
    # from the POST-EXPAND dictionary (which carries the stamped child + user
    # macros as ``Macro`` entries). ``expand`` mutates its program + dictionary
    # in place, so build them from a SECOND parse to keep the positioning AST
    # intact. Mirrors the resolve→expand→stackcheck order of ``mforth compile``
    # (design D13). Any meta-failure (CellBoundaryError/PurityError/ExpandError)
    # leaves us without effects — hover degrades to literal/built-in shapes.
    try:
        dictionary = resolve(program)
    except UnresolvedWordError:
        return None

    sc_result = None
    try:
        eff_program = parse(text, file=file)
        eff_dictionary = resolve(eff_program)
        eff_program = expand(eff_program, eff_dictionary)
        dictionary = eff_dictionary
        sc_result = stackcheck(eff_program, dictionary=eff_dictionary)
    except (
        StackError,
        UnresolvedWordError,
        CellBoundaryError,
        PurityError,
        ExpandError,
        LexError,
        ParseError,
    ):
        sc_result = None

    term = _term_at_position(program, position)
    if term is None:
        return None

    body = _format_hover(term, dictionary, sc_result)
    if body is None:
        return None
    return lsp.Hover(
        contents=lsp.MarkupContent(kind=lsp.MarkupKind.PlainText, value=body)
    )


def _term_at_position(program: Program, position: lsp.Position):
    """Find the AST term whose source extent contains ``position``.

    Position is LSP-style (0-based line + character). Term src_loc is
    1-based. Returns the matched term or None.
    """
    target_line_1based = position.line + 1
    target_col_1based = position.character + 1

    match: object | None = None

    def _visit(term) -> None:
        nonlocal match
        if match is not None:
            return

        # Recurse into structural nodes that don't themselves have a
        # paste-able hover. (IfThen/Begin/DoLoop are AST scaffolding;
        # the if/then/begin/loop keywords don't survive parsing as
        # WordCalls so they have no hover in v1 — see negative case.)
        if isinstance(term, IfThen):
            for t in term.then_body:
                _visit(t)
            for t in term.else_body:
                _visit(t)
            return
        if isinstance(term, Begin):
            for t in term.body:
                _visit(t)
            for t in term.cond_body:
                _visit(t)
            return
        if isinstance(term, DoLoop):
            for t in term.body:
                _visit(t)
            return

        extent = _term_extent(term)
        if extent is None:
            return
        line, start, end = extent
        if line == target_line_1based and start <= target_col_1based < end:
            match = term

    for t in program.main:
        _visit(t)
    for defn in program.definitions:
        for t in defn.body:
            _visit(t)

    return match


def _term_extent(term) -> tuple[int, int, int] | None:
    """Return (line, start_col, end_col_exclusive) for a term, all
    1-based. Returns None if the term has no representable extent
    (control-flow structural nodes — but those are filtered before
    this is called)."""
    if isinstance(term, WordCall):
        return (term.src_loc.line, term.src_loc.col, term.src_loc.col + len(term.name))
    if isinstance(term, LitInt):
        return (
            term.src_loc.line,
            term.src_loc.col,
            term.src_loc.col + len(str(term.value)),
        )
    if isinstance(term, LitFloat):
        # Use ``repr`` so the rendered extent matches the source token
        # for typical magnitudes (``0.95`` → 4 chars). Scientific-notation
        # cases are best-effort — the LSP only needs the cursor to land
        # somewhere on the literal to hit it.
        return (
            term.src_loc.line,
            term.src_loc.col,
            term.src_loc.col + len(repr(term.value)),
        )
    if isinstance(term, LitStr):
        # `." hello"` — the parser puts src_loc on the `."` opener; the
        # rendered length covers the opener (2 chars), the value, and the
        # trailing quote. This is an approximation, but it's enough to
        # let the cursor land anywhere on the literal and hit it.
        return (
            term.src_loc.line,
            term.src_loc.col,
            term.src_loc.col + len(term.value) + 4,
        )
    if isinstance(term, VarRef):
        return (term.src_loc.line, term.src_loc.col, term.src_loc.col + len(term.name))
    return None


def _format_hover(term, dictionary, sc_result) -> str | None:
    """Render the hover body for a matched term. Returns None if the
    term shape isn't hover-able (e.g. an unresolved word)."""
    if isinstance(term, LitInt):
        return f"{term.value}\n( -- n )"
    if isinstance(term, LitFloat):
        return f"{term.value}\n( -- f )"
    if isinstance(term, LitStr):
        return f'"{term.value}"\n( -- str )'
    if isinstance(term, WordCall):
        entry = dictionary.lookup(term.name)
        if entry is None:
            return None
        if isinstance(entry, BuiltinWord):
            return _format_builtin(entry)
        if isinstance(entry, Definition):
            return _format_user_def(entry, sc_result)
        if isinstance(entry, UserVariable):
            return _format_user_var(entry)
        if isinstance(entry, Macro):
            return _format_macro(entry, dictionary)
    if isinstance(term, VarRef):
        entry = dictionary.lookup(term.name)
        if isinstance(entry, UserVariable):
            return _format_user_var(entry)
    return None


def _format_stack_effect(in_arity: int, out_arity: int) -> str:
    """Render a stack effect in mforth's CANONICAL depth-numbered form.

    The convention is ``( <in_arity> -- <out_arity> )`` -- explicit input
    and output DEPTHS, not Forth-traditional named placeholders like
    ``( -- n )``. Every LSP surface that renders a stack effect (built-in
    hover, alias hover, completion ``detail``, the @-identifier magic vars
    from mforth-eaz) routes through here, so the format is uniform across
    the whole surface by construction. A value-pushing @-identifier such as
    ``@copper`` or ``@tick`` therefore renders ``( 0 -- 1 )`` (zero in, one
    out). Pinned by tests in
    ``tests/integration/test_mindustry_lsp_surfaces.py`` (beads mforth-7ma,
    mforth-9lx).
    """
    return f"( {in_arity} -- {out_arity} )"


def _format_builtin(entry: BuiltinWord) -> str:
    eff = _format_stack_effect(entry.stack_effect.in_arity, entry.stack_effect.out_arity)
    doc = entry.doc if entry.doc else "(no documentation)"
    return f"{entry.name} {eff}\n{doc}"


def _format_user_def(entry: Definition, sc_result) -> str:
    eff_obj = None
    if sc_result is not None and sc_result.effects is not None:
        eff_obj = sc_result.effects.get(entry.name)
    if eff_obj is not None:
        eff = _format_stack_effect(eff_obj.in_arity, eff_obj.out_arity)
    else:
        eff = "( ? -- ? )"
    loc = entry.src_loc
    return f"{entry.name} {eff}\ndefined at {loc.file}:{loc.line}:{loc.col}"


def _format_user_var(entry: UserVariable) -> str:
    loc = entry.src_loc
    return (
        f"{entry.name} ( -- addr )\n"
        f"variable defined at {loc.file}:{loc.line}:{loc.col}"
    )


def _macro_stack_effect(entry: Macro, dictionary) -> "tuple[int, int] | None":
    """Compute a ``Macro``'s statically-known stack effect (bead mforth-7h1.4,
    design D7/F15).

    A ``Macro`` entry is either a B3 user macro (``MACRO: name body ;``) or a
    B2 stamped CREATE/,/DOES> child (``76 CONSTANT TROMBONES`` → a single
    literal push). Because ``expand`` has already run, the macro body is fully
    inlined (zero surviving ``Macro`` calls), so its effect is computable by
    the SAME stackcheck the compiler runs — we wrap the body in a synthetic
    one-shot ``Definition`` and read back the inferred effect. The auto-pushed
    field address that a DOES> child consumes is already baked into the stamped
    body, so a stamped child renders ``( 0 -- 1 )`` (zero in, one out).

    Returns ``(in_arity, out_arity)`` or ``None`` if the effect can't be
    inferred (a malformed / not-stack-valid body — hover then falls back to
    the ``( ? -- ? )`` placeholder)."""
    synth_name = f"__macro_hover_{entry.name}"
    synth = Definition(name=synth_name, body=list(entry.body), src_loc=SrcLoc("<macro>", 1, 1))
    synth_program = Program(definitions=[synth], main=[])
    d = dictionary.copy() if hasattr(dictionary, "copy") else dictionary
    d._entries[synth_name.lower()] = synth  # noqa: SLF001
    try:
        # The dictionary stores macro entries with their ORIGINAL (un-inlined)
        # bodies — a nested macro (``MACRO: quad dbl dbl ;``) still calls
        # ``dbl`` as a ``Macro`` WordCall. Run ``expand`` on the synthetic
        # wrapper so those nested macro calls inline to a meta-free body before
        # stackcheck (which raises ``TypeError`` on a surviving ``Macro``).
        synth_program = expand(synth_program, d)
        result = stackcheck(synth_program, dictionary=d)
    except (
        StackError,
        UnresolvedWordError,
        TypeError,
        CellBoundaryError,
        PurityError,
        ExpandError,
    ):
        return None
    eff = result.effects.get(synth_name)
    if eff is None:
        return None
    return (eff.in_arity, eff.out_arity)


def _format_macro(entry: Macro, dictionary) -> str:
    """Render a hover for a ``Macro`` entry: its name + stamped/inlined stack
    effect. Covers both B3 user macros and B2 stamped CREATE/,/DOES> children
    (which carry no source location, so the body line names the kind rather
    than a ``defined at`` anchor)."""
    eff = _macro_stack_effect(entry, dictionary)
    eff_str = _format_stack_effect(*eff) if eff is not None else "( ? -- ? )"
    return f"{entry.name} {eff_str}\nmeta-word (expanded inline)"


# ---------------------------------------------------------------------------
# Completion (bead mforth-10t.25)
# ---------------------------------------------------------------------------
#
# `completions_for(text, *, uri, position)` is the third pure-function
# seam alongside `analyze_document` and `hover_for`. The completion
# handler registered on the server is a thin shim that pulls the
# document text from the per-server URI→text cache (populated by
# did_open/did_change) and calls this function.
#
# Completion sources (per the bead's acceptance):
#
#   1. Built-ins — all 32 entries from `standard_dictionary()`. Surfaced
#      as `CompletionItemKind.Function` with the dictionary's stack
#      effect in `detail` and the doc string in `documentation`.
#   2. User-defined words — every `: name ... ;` declaration whose
#      `src_loc` is BEFORE the cursor position. Forth's source-order
#      rule: a word becomes callable only after its `;` closes.
#      `CompletionItemKind.Function`.
#   3. User variables — every `VARIABLE name` declaration whose name
#      token position is BEFORE the cursor. `CompletionItemKind.Variable`.
#   4. Sidecar link names — `[links.<name>]` entries in a sibling
#      `<stem>.world.toml` file (looked up on disk via the URI's path).
#      `CompletionItemKind.Constant`. Used as arguments to SENSOR /
#      PRINTFLUSH / GETLINK.
#
# Source-order rule (HARD): for user definitions, the definition is
# considered visible at positions STRICTLY AFTER the `;` that closes it.
# In particular, `square` does NOT appear in completions inside its own
# body — mforth v1 explicitly disallows recursion (bead .7), so the body
# is the wrong place to surface it. Pragmatic implementation: parse the
# program and, for each Definition, derive its closing `;` position by
# inspecting the last term in the body's `src_loc` plus rendered length;
# if a definition has no terms or src locations are unavailable, fall
# back to the `:` opener's line and conservatively exclude it. The end
# result: an interactive session sees `square` as soon as the cursor
# leaves the `: square ... ;` definition.
#
# Variables: surfaced from the resolved dictionary's `UserVariable`
# entries. Each `UserVariable.src_loc` points at the NAME token (see
# `_collect_variable_declarations` in `mforth.dictionary`), so the
# source-order check uses the name's loc directly.
#
# String / comment context detection: we walk the text from start of
# document up to the cursor, tracking three states — IN_PAREN_COMMENT,
# IN_LINE_COMMENT, IN_STRING (`."` or `S"`). If the cursor lands inside
# any of those, return an empty completion list. The walk mirrors the
# lexer's rules (a `(` only opens a comment when surrounded by whitespace,
# a `."`/`S"` only opens a string when the same condition holds, a `\\`
# starts a line comment under the same rule). Line comments end at the
# next `\n`; paren comments end at the matching `)`; strings end at the
# next `"`.
#
# Graceful degradation: if the document fails to parse, completion still
# returns the built-ins + any sidecar links — completion is a TEACHING
# surface and the user is, by definition, typing something incomplete.
# Mirrors `.24`'s NON-fatal-stackcheck decision.


_SIDECAR_LINK_DETAIL_PREFIX = "link:"


def completions_for(
    text: str, *, uri: str, position: lsp.Position
) -> List[lsp.CompletionItem]:
    """Return all completion candidates relevant to the cursor.

    The returned list is the union of all sources (built-ins, user
    words/variables in scope, sidecar link names). The LSP client
    filters by the partial word under the cursor. If the cursor is
    inside a string literal or comment, returns an empty list.
    """
    # Context filter: bail early inside strings/comments.
    if _cursor_in_string_or_comment(text, position):
        return []

    items: List[lsp.CompletionItem] = []

    # 1. Built-ins — always available, even when the document is broken.
    # Iterate by dict key (lowercased name) so aliases produce their own
    # completion items. When the key equals entry.name.lower(), it's the
    # canonical entry; otherwise it's an alias (e.g., @ticks → @tick from
    # mforth-eaz's _MINDUSTRY_ALIASES).
    builtins_dict = standard_dictionary()
    for name_lc, entry in builtins_dict._entries.items():  # noqa: SLF001
        if isinstance(entry, BuiltinWord):
            if name_lc == entry.name.lower():
                items.append(_builtin_completion(entry))
            else:
                items.append(_builtin_completion(entry, alias_label=name_lc))

    # 2 + 3. User words + variables — only those declared before the cursor.
    # Re-run lex/parse/resolve. parse failure → skip user-defined sources;
    # we still get built-ins + sidecar links.
    file = _file_from_uri(uri)
    program = None
    dictionary = None
    try:
        program = parse(text, file=file)
    except (LexError, ParseError):
        program = None

    if program is not None:
        try:
            dictionary = resolve(program)
        except UnresolvedWordError:
            # An unresolved word doesn't prevent us from surfacing the
            # in-scope definitions/variables; rebuild the dictionary
            # by hand from the parsed program.
            dictionary = None

        in_scope_defs = _user_defs_in_scope(program, position)
        in_scope_vars = _user_vars_in_scope(program, position)

        for defn in in_scope_defs:
            items.append(_user_def_completion(defn))
        for var in in_scope_vars:
            items.append(_user_var_completion(var))

        # Meta-words (bead mforth-7h1.4, design D13): user macros
        # (``MACRO: name ... ;``) and stamped CREATE/,/DOES> children
        # (``76 CONSTANT TROMBONES``) become ``Macro`` entries only AFTER the
        # phase-0 expand pass runs. Build the post-expand dictionary from a
        # SECOND parse (expand mutates its program + dictionary in place) and
        # surface every in-scope ``Macro`` as a completion candidate.
        for name, eff in _meta_words_in_scope(text, file, position):
            items.append(_macro_completion(name, eff))

    # 4. Sidecar link names — load from the on-disk sibling `.world.toml`
    # if present. Failures (file missing, TOML malformed, schema error)
    # all degrade to "no link completions" rather than crashing.
    for link_name, link_type in _sidecar_link_candidates(uri):
        items.append(_sidecar_link_completion(link_name, link_type))

    return items


def _builtin_completion(
    entry: BuiltinWord, *, alias_label: str | None = None
) -> lsp.CompletionItem:
    label = alias_label if alias_label is not None else entry.name
    doc_value = entry.doc or "(no documentation)"
    if alias_label is not None:
        doc_value = f"alias of {entry.name}\n{doc_value}"
    detail = _format_stack_effect(entry.stack_effect.in_arity, entry.stack_effect.out_arity)
    return lsp.CompletionItem(
        label=label,
        kind=lsp.CompletionItemKind.Function,
        detail=detail,
        documentation=lsp.MarkupContent(
            kind=lsp.MarkupKind.PlainText,
            value=doc_value,
        ),
    )


def _user_def_completion(defn: Definition) -> lsp.CompletionItem:
    loc = defn.src_loc
    return lsp.CompletionItem(
        label=defn.name,
        kind=lsp.CompletionItemKind.Function,
        detail="( ? -- ? )",
        documentation=lsp.MarkupContent(
            kind=lsp.MarkupKind.PlainText,
            value=f"defined at {loc.file}:{loc.line}:{loc.col}",
        ),
    )


def _user_var_completion(var: UserVariable) -> lsp.CompletionItem:
    loc = var.src_loc
    return lsp.CompletionItem(
        label=var.name,
        kind=lsp.CompletionItemKind.Variable,
        detail="( -- addr )",
        documentation=lsp.MarkupContent(
            kind=lsp.MarkupKind.PlainText,
            value=f"variable defined at {loc.file}:{loc.line}:{loc.col}",
        ),
    )


def _macro_completion(
    name: str, eff: "tuple[int, int] | None"
) -> lsp.CompletionItem:
    """Completion item for a meta-word (user macro or stamped CREATE/,/DOES>
    child). Both expand inline; the ``detail`` shows the inferred stack effect
    so a stamped CONSTANT child reads ``( 0 -- 1 )``."""
    detail = _format_stack_effect(*eff) if eff is not None else "( ? -- ? )"
    return lsp.CompletionItem(
        label=name,
        kind=lsp.CompletionItemKind.Function,
        detail=detail,
        documentation=lsp.MarkupContent(
            kind=lsp.MarkupKind.PlainText,
            value="meta-word (expanded inline)",
        ),
    )


def _sidecar_link_completion(name: str, link_type: str) -> lsp.CompletionItem:
    return lsp.CompletionItem(
        label=name,
        kind=lsp.CompletionItemKind.Constant,
        detail=f"{_SIDECAR_LINK_DETAIL_PREFIX} {link_type}",
        documentation=lsp.MarkupContent(
            kind=lsp.MarkupKind.PlainText,
            value=f"sidecar link '{name}' ({link_type})",
        ),
    )


# ---------------------------------------------------------------------------
# Source-order scope helpers
# ---------------------------------------------------------------------------


def _position_strictly_before(
    loc_line_1based: int,
    loc_col_1based: int,
    cursor: lsp.Position,
) -> bool:
    """True when (loc_line, loc_col) is strictly earlier than the cursor.

    Both inputs are 1-based; cursor is 0-based LSP. Strict because a
    declaration AT the cursor position is still being typed.
    """
    cursor_line_1based = cursor.line + 1
    cursor_col_1based = cursor.character + 1
    if loc_line_1based < cursor_line_1based:
        return True
    if loc_line_1based == cursor_line_1based and loc_col_1based < cursor_col_1based:
        return True
    return False


def _definition_end_position(defn: Definition) -> tuple[int, int] | None:
    """Best-effort: where does the `;` that closes this definition sit?

    The parser records `src_loc` on the `:` opener but not on the `;`.
    Approximate: use the last body term's location + rendered length as
    an upper bound for the body end. The `;` is at-or-after that point.
    Returns (line, col) 1-based, or None if no body terms are available.
    """
    if not defn.body:
        return None
    last = defn.body[-1]
    extent = _term_extent(last)
    if extent is None:
        # Walk into the structural last term if needed
        return (getattr(last, "src_loc", defn.src_loc).line, 0)
    line, _start, end = extent
    return (line, end)


def _user_defs_in_scope(program: Program, cursor: lsp.Position) -> list[Definition]:
    """Return user definitions whose `;` closes strictly before the cursor.

    Source-order rule: `square` becomes completable only after `: square
    ... ;` finishes. Crucially, this excludes the definition's own body
    (which is correct — mforth v1 disallows recursion).
    """
    result: list[Definition] = []
    for defn in program.definitions:
        end = _definition_end_position(defn)
        if end is None:
            # No body — treat as visible after the `:` line (best-effort).
            if _position_strictly_before(defn.src_loc.line, defn.src_loc.col, cursor):
                result.append(defn)
            continue
        end_line, end_col = end
        if _position_strictly_before(end_line, end_col, cursor):
            result.append(defn)
    return result


def _macro_decl_line(entry: Macro, fallback_program_macro) -> "int | None":
    """Best-effort 1-based declaration line for a ``Macro`` entry.

    A user macro (``MACRO: name body ;``) carries no ``src_loc`` on the parsed
    ``Macro``, so we read the line off its body's first term (the macro body is
    written on the declaration line). A stamped CREATE/,/DOES> child's body is
    the literal push synthesized at the invocation site, so the same first-term
    read yields the invocation line. Returns ``None`` for an empty body
    (treated as always-visible by the caller)."""
    src = fallback_program_macro.body if fallback_program_macro is not None else entry.body
    for term in src:
        loc = getattr(term, "src_loc", None)
        if loc is not None:
            return loc.line
    return None


def _meta_words_in_scope(
    text: str, file: str, cursor: lsp.Position
) -> "list[tuple[str, tuple[int, int] | None]]":
    """Return ``[(macro_name, stack_effect), ...]`` for every meta-word
    (user macro + stamped CREATE/,/DOES> child) declared strictly before the
    cursor line (bead mforth-7h1.4, design D13).

    Meta-words only become ``Macro`` entries AFTER the phase-0 ``expand`` pass.
    ``expand`` mutates its program + dictionary in place, so we run it on a
    SECOND parse to avoid disturbing the caller's positioning AST. The
    source-order rule is line-granular (a meta-word is usable on the line after
    its declaration completes) — finer than ``;``-column tracking, but a macro /
    defining word is always written single-line in v1 fixtures and completion is
    a teaching aid, not a validator. Any meta-failure → no meta completions."""
    try:
        program = parse(text, file=file)
        dictionary = resolve(program)
        # `program.macros` (pre-expand) carries the user-macro bodies with src
        # locs; capture them BEFORE expand mutates the dictionary so we can read
        # declaration lines off the parsed bodies.
        program_macros = {
            m.name.lower(): m for m in getattr(program, "macros", [])
        }
        expand(program, dictionary)
    except (
        LexError,
        ParseError,
        UnresolvedWordError,
        CellBoundaryError,
        PurityError,
        ExpandError,
    ):
        return []

    standard = standard_dictionary()
    result: list[tuple[str, tuple[int, int] | None]] = []
    cursor_line_1based = cursor.line + 1
    for name_lc, entry in dictionary._entries.items():  # noqa: SLF001
        if not isinstance(entry, Macro):
            continue
        if name_lc in standard:
            continue  # a builtin shadowed by a Macro is not a user meta-word
        decl_line = _macro_decl_line(entry, program_macros.get(name_lc))
        if decl_line is not None and decl_line >= cursor_line_1based:
            continue  # declared at-or-after the cursor line → not yet in scope
        eff = _macro_stack_effect(entry, dictionary)
        result.append((entry.name, eff))
    return result


def _user_vars_in_scope(program: Program, cursor: lsp.Position) -> list[UserVariable]:
    """Return user variables whose name-token position is strictly before
    the cursor. Walks the program directly rather than the dictionary so
    the result tracks source order rather than the dictionary's
    case-insensitive map order."""
    # Reuse the dictionary's pre-scanner — it produces UserVariable entries
    # with src_loc on the name token, exactly what we need.
    from mforth.dictionary import _collect_variable_declarations

    result: list[UserVariable] = []
    for var in _collect_variable_declarations(program):
        if _position_strictly_before(var.src_loc.line, var.src_loc.col, cursor):
            result.append(var)
    return result


# ---------------------------------------------------------------------------
# Sidecar lookup
# ---------------------------------------------------------------------------


def _sidecar_link_candidates(uri: str) -> list[tuple[str, str]]:
    """Look up `<stem>.world.toml` next to the Forth file referenced by
    `uri` and return ``[(link_name, link_type), ...]``.

    Returns empty list when the URI isn't a `file://` URI, when the
    sibling sidecar doesn't exist, or when parsing fails (malformed
    TOML / schema violation). Failures degrade silently — completion
    is a typing assistant, not a validator. Sidecar validation errors
    surface through the separate `analyze_sidecar` diagnostics path.
    """
    import os
    from urllib.parse import urlparse

    parsed = urlparse(uri)
    if parsed.scheme not in ("file", ""):
        return []
    fs_path = parsed.path or uri
    if not fs_path:
        return []
    # Derive `<stem>.world.toml`: drop the final extension if any.
    base, ext = os.path.splitext(fs_path)
    if ext == ".toml":
        # The cursor is already on a sidecar — completion of links from
        # within a sidecar isn't a v1 use case.
        return []
    sidecar_path = base + ".world.toml"
    if not os.path.isfile(sidecar_path):
        return []
    try:
        with open(sidecar_path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    try:
        world = parse_sidecar(data, source=sidecar_path)
    except SidecarError:
        return []
    return [(link.mforth_name, link.type) for link in world.links]


def _sidecar_path_for(uri: str) -> str | None:
    """Return the filesystem path of the sibling ``<stem>.world.toml`` for
    a Forth ``uri``, or None when the URI isn't a file URI / has no sibling
    sidecar on disk. Used by go-to-definition to navigate a sidecar-bound
    name to its declaring sidecar file."""
    import os
    from urllib.parse import urlparse

    parsed = urlparse(uri)
    if parsed.scheme not in ("file", ""):
        return None
    fs_path = parsed.path or uri
    if not fs_path:
        return None
    base, ext = os.path.splitext(fs_path)
    if ext == ".toml":
        return None
    sidecar_path = base + ".world.toml"
    if not os.path.isfile(sidecar_path):
        return None
    return sidecar_path


# ---------------------------------------------------------------------------
# Go-to-definition (bead mforth-10t.26, part 1)
# ---------------------------------------------------------------------------
#
# `definition_for(text, *, uri, position)` is the fourth pure-function seam
# alongside analyze_document / hover_for / completions_for. It answers
# `textDocument/definition`: the cursor sits on a WordCall; we resolve the
# name and point at the source location of its declaration.
#
# Resolution targets, in priority order:
#
#   1. Sidecar link name — `[links.<name>]` in the sibling `.world.toml`.
#      Navigate to the sidecar file (head, since tomllib doesn't surface
#      per-key positions in v1). Checked FIRST so a sidecar-pre-seeded
#      UserVariable (synthetic src_loc at the .fs head) never shadows the
#      real sidecar target.
#   2. User Definition — `: name ... ;`. Navigate to the `:` opener
#      (the Definition.src_loc, same anchor hover uses).
#   3. User VARIABLE — navigate to the declaration's NAME token
#      (UserVariable.src_loc).
#
# Built-ins, literals, control-flow keywords, unresolved words, and a
# document that fails to parse all yield None (nothing to navigate to).
# Mirrors hover's parse/resolve-failure → None policy.


def definition_for(
    text: str, *, uri: str, position: lsp.Position
) -> lsp.Location | None:
    """Return the source `Location` that defines the WordCall under the
    cursor, or None when there's nothing navigable there."""
    file = _file_from_uri(uri)

    try:
        program = parse(text, file=file)
    except (LexError, ParseError):
        return None

    term = _term_at_position(program, position)
    if not isinstance(term, (WordCall, VarRef)):
        return None
    name = term.name

    # 1. Sidecar link names win — they're the real declaration site.
    sidecar_names = {ln for ln, _t in _sidecar_link_candidates(uri)}
    if name in sidecar_names:
        sidecar_path = _sidecar_path_for(uri)
        if sidecar_path is not None:
            return _location(_path_to_uri(sidecar_path), 1, 1)
        return None

    # 2 + 3. Resolve and look the name up. Pre-seed sidecar links so a
    # resolve over a sidecar-bound document doesn't raise before we reach
    # the user-symbol lookup (the link branch above already handled those).
    dictionary = standard_dictionary()
    sidecar_src_loc = SrcLoc(file=file, line=1, col=1)
    for link_name, _link_type in _sidecar_link_candidates(uri):
        if link_name not in dictionary:
            dictionary.add_variable(
                UserVariable(name=link_name, src_loc=sidecar_src_loc)
            )
    try:
        dictionary = resolve(program, dictionary=dictionary)
    except UnresolvedWordError:
        return None

    entry = dictionary.lookup(name)
    if isinstance(entry, Definition):
        loc = entry.src_loc
        return _location(uri, loc.line, loc.col)
    if isinstance(entry, UserVariable):
        loc = entry.src_loc
        return _location(uri, loc.line, loc.col)
    # BuiltinWord (or None) — no navigable source location.
    return None


def _location(uri: str, line_1based: int, col_1based: int) -> lsp.Location:
    """Build a single-character LSP Location at a 1-based source position."""
    start = _lsp_position(line_1based, col_1based)
    end = lsp.Position(line=start.line, character=start.character + 1)
    return lsp.Location(uri=uri, range=lsp.Range(start=start, end=end))


def _path_to_uri(path: str) -> str:
    """Convert a filesystem path to a ``file://`` URI."""
    from pathlib import Path

    return Path(path).as_uri()


# ---------------------------------------------------------------------------
# Semantic tokens (bead mforth-10t.26, part 2)
# ---------------------------------------------------------------------------
#
# `semantic_token_spans(text)` classifies every lexeme into one standard
# LSP semantic-token TYPE so the editor can colorize Forth source. It does
# its own single-pass scan (NOT the lexer) because the lexer discards plain
# comments — and comments are one of the classes we must surface. The scan
# mirrors the lexer's whitespace-delimited, standalone-token discipline:
#
#   * `( ... )` paren comment        → comment   (whole span incl. parens)
#   * `\ ...`  line comment          → comment   (to end of line)
#   * `."` / `S"` string             → string    (opener through closing ")
#   * integer / float literal        → number
#   * `:` / `;` + control keywords   → keyword
#   * `@`-identifier (magic var)     → macro
#   * sidecar link name (if known)   → macro
#   * anything else (a WORD)         → function for word-calls, variable
#                                      for VARIABLE references
#
# Word-vs-variable classification: a WORD is a `variable` iff it names a
# source-declared VARIABLE; otherwise `function` (built-in or user word).
# We compute the VARIABLE name set by a best-effort parse; if the document
# doesn't parse, every WORD falls back to `function` (the teaching surface
# still colorizes reasonably). The classification never raises.
#
# `semantic_tokens_for(text, *, uri)` wraps the spans in the LSP
# delta-encoded `data` array using SEMANTIC_TOKEN_LEGEND.

SEMANTIC_TOKEN_LEGEND = lsp.SemanticTokensLegend(
    token_types=[
        lsp.SemanticTokenTypes.Keyword.value,
        lsp.SemanticTokenTypes.Function.value,
        lsp.SemanticTokenTypes.Variable.value,
        lsp.SemanticTokenTypes.Number.value,
        lsp.SemanticTokenTypes.String.value,
        lsp.SemanticTokenTypes.Comment.value,
        lsp.SemanticTokenTypes.Macro.value,
    ],
    token_modifiers=[],
)

_TOKEN_TYPE_INDEX = {name: i for i, name in enumerate(SEMANTIC_TOKEN_LEGEND.token_types)}


def _variable_names(text: str, file: str) -> set[str]:
    """Best-effort set of source-declared VARIABLE names (lowercased) so
    semantic tokens can color references as `variable`. Returns an empty
    set on any parse failure."""
    from mforth.dictionary import _collect_variable_declarations

    try:
        program = parse(text, file=file)
    except (LexError, ParseError):
        return set()
    return {v.name.lower() for v in _collect_variable_declarations(program)}


def semantic_token_spans(text: str) -> list[tuple[int, int, int, str]]:
    """Classify `text` into absolute semantic-token spans.

    Returns a list of ``(line, col, length, type_name)`` tuples, 0-based
    line + col, in document order (sorted by position, non-overlapping).
    Pure + total: never raises, never touches the filesystem.
    """
    variable_names = _variable_names(text, "<semantic>")
    spans: list[tuple[int, int, int, str]] = []

    n = len(text)
    i = 0
    line = 0
    col = 0

    def emit(start_line: int, start_col: int, length: int, type_name: str) -> None:
        if length > 0:
            spans.append((start_line, start_col, length, type_name))

    def at_edge(idx: int) -> bool:
        if idx < 0 or idx >= n:
            return True
        return text[idx] in " \t\n\r"

    while i < n:
        ch = text[i]

        # Whitespace — advance line/col bookkeeping.
        if ch == "\n":
            line += 1
            col = 0
            i += 1
            continue
        if ch in " \t\r":
            col += 1
            i += 1
            continue

        start_line, start_col, start_i = line, col, i

        # Line comment `\ ...` (standalone backslash) → to end of line.
        if ch == "\\" and at_edge(i - 1) and at_edge(i + 1):
            j = i
            while j < n and text[j] != "\n":
                j += 1
            emit(start_line, start_col, j - i, "comment")
            col += j - i
            i = j
            continue

        # Paren comment `( ... )` (standalone open paren).
        if ch == "(" and at_edge(i - 1) and at_edge(i + 1):
            j = i + 1
            run_col = start_col + 1
            run_line = start_line
            while j < n and text[j] != ")":
                if text[j] == "\n":
                    # Multi-line comment: emit the current line's run, reset.
                    emit(run_line, run_col if j == i + 1 else 0, 0, "comment")
                    run_line += 1
                j += 1
            if j < n:
                j += 1  # consume the `)`
            # Emit comment span(s). For simplicity (and because v1 comments
            # are single-line in practice) emit one span per physical line.
            _emit_multiline(emit, text, start_i, j, start_line, start_col, "comment")
            # Advance i/line/col by walking the consumed region.
            i, line, col = _advance(text, start_i, j, start_line, start_col)
            continue

        # String `."` / `S"` (standalone opener) → through closing quote.
        if (
            i + 1 < n
            and text[i + 1] == '"'
            and ch in (".", "S")
            and at_edge(i - 1)
            and at_edge(i + 2)
        ):
            j = i + 2
            while j < n and text[j] != '"' and text[j] != "\n":
                j += 1
            if j < n and text[j] == '"':
                j += 1  # consume closing quote
            emit(start_line, start_col, j - i, "string")
            col += j - i
            i = j
            continue

        # General lexeme: read a whitespace-delimited run.
        j = i
        while j < n and text[j] not in " \t\r\n":
            j += 1
        lexeme = text[i:j]
        length = j - i

        type_name = _classify_lexeme(lexeme, variable_names)
        emit(start_line, start_col, length, type_name)
        col += length
        i = j

    spans.sort(key=lambda s: (s[0], s[1]))
    return spans


def _emit_multiline(emit, text, start_i, end_i, start_line, start_col, type_name):
    """Emit one comment span per physical line covering text[start_i:end_i]."""
    seg_line = start_line
    seg_col = start_col
    seg_start = start_i
    k = start_i
    while k < end_i:
        if text[k] == "\n":
            emit(seg_line, seg_col, k - seg_start, type_name)
            seg_line += 1
            seg_col = 0
            seg_start = k + 1
        k += 1
    emit(seg_line, seg_col, end_i - seg_start, type_name)


def _advance(text, start_i, end_i, start_line, start_col):
    """Return (new_i, new_line, new_col) after consuming text[start_i:end_i]."""
    line = start_line
    col = start_col
    k = start_i
    while k < end_i:
        if text[k] == "\n":
            line += 1
            col = 0
        else:
            col += 1
        k += 1
    return end_i, line, col


def _classify_lexeme(lexeme: str, variable_names: set[str]) -> str:
    """Classify a whitespace-delimited lexeme into a semantic-token type."""
    if lexeme in (":", ";"):
        return "keyword"
    low = lexeme.lower()
    if low in _SEMANTIC_KEYWORDS:
        return "keyword"
    if _is_number_lexeme(lexeme):
        return "number"
    if lexeme.startswith("@") and len(lexeme) > 1:
        return "macro"
    if low in variable_names:
        return "variable"
    return "function"


_SEMANTIC_KEYWORDS = {
    "if", "else", "then",
    "begin", "until", "while", "repeat",
    "do", "loop",
}


def _is_number_lexeme(lexeme: str) -> bool:
    """True iff `lexeme` is an integer or float literal (mirrors the lexer's
    int / float recognizers)."""
    from mforth.lex import _parse_float_or_none, _parse_int_or_none

    return (
        _parse_int_or_none(lexeme) is not None
        or _parse_float_or_none(lexeme) is not None
    )


def semantic_tokens_for(text: str, *, uri: str) -> lsp.SemanticTokens:
    """Return LSP delta-encoded semantic tokens for `text`. `uri` is
    accepted for symmetry with the other seams (semantic classification is
    URI-independent in v1 — no cross-file resolution)."""
    spans = semantic_token_spans(text)
    data: list[int] = []
    prev_line = 0
    prev_col = 0
    for s_line, s_col, length, type_name in spans:
        type_idx = _TOKEN_TYPE_INDEX[type_name]
        if s_line == prev_line:
            delta_start = s_col - prev_col
        else:
            delta_start = s_col
        data.extend([s_line - prev_line, delta_start, length, type_idx, 0])
        prev_line = s_line
        prev_col = s_col
    return lsp.SemanticTokens(data=data)


# ---------------------------------------------------------------------------
# String / comment context detection
# ---------------------------------------------------------------------------


def _cursor_in_string_or_comment(text: str, position: lsp.Position) -> bool:
    """Walk the document up to ``position`` and return True iff the
    cursor falls inside a string literal or comment.

    Mirrors the lexer's tokenizer state machine just enough to classify
    the cursor location. Handles:

    * ``\\ `` line comments — terminate at the next newline.
    * ``( ... )`` paren comments — nestable, terminate at the matching
      ``)``. The `(` opener must be a standalone token (surrounded by
      whitespace) to count as a comment opener.
    * ``."`` / ``S"`` string literals — terminate at the next ``"``.
      Standalone-token rule applies to the opener.

    The cursor is considered "inside" if a comment / string is open at
    the cursor position OR closes exactly at the cursor position (the
    cursor stays inside the lexeme until the closing character has been
    crossed).
    """
    # Convert (line, character) to absolute char index.
    target = _position_to_offset(text, position)

    # State machine.
    STATE_CODE = 0
    STATE_LINE_COMMENT = 1
    STATE_PAREN_COMMENT = 2  # depth carried separately
    STATE_STRING = 3

    state = STATE_CODE
    paren_depth = 0
    i = 0
    n = len(text)

    def is_ws_or_edge(idx: int) -> bool:
        if idx < 0 or idx >= n:
            return True
        c = text[idx]
        return c in " \t\n\r"

    while i < target and i < n:
        ch = text[i]

        if state == STATE_LINE_COMMENT:
            if ch == "\n":
                state = STATE_CODE
                i += 1
            else:
                i += 1
            continue

        if state == STATE_PAREN_COMMENT:
            # Nest on standalone `(`, close on `)`. The lexer requires
            # the `(` opener to be whitespace-surrounded but the closer
            # is any `)`. We mirror that.
            if ch == "(" and is_ws_or_edge(i - 1):
                paren_depth += 1
                i += 1
            elif ch == ")":
                paren_depth -= 1
                i += 1
                if paren_depth <= 0:
                    state = STATE_CODE
                    paren_depth = 0
            else:
                i += 1
            continue

        if state == STATE_STRING:
            if ch == '"':
                state = STATE_CODE
                i += 1
            else:
                i += 1
            continue

        # STATE_CODE — look for openers.
        # Line comment: `\` as a standalone token.
        if ch == "\\" and is_ws_or_edge(i - 1) and is_ws_or_edge(i + 1):
            state = STATE_LINE_COMMENT
            i += 1
            continue

        # Paren comment: `(` as a standalone token.
        if ch == "(" and is_ws_or_edge(i - 1) and is_ws_or_edge(i + 1):
            state = STATE_PAREN_COMMENT
            paren_depth = 1
            i += 1
            continue

        # Dot-quote string: `."` as a standalone token (next-next is ws/edge).
        if (
            ch == "."
            and i + 1 < n
            and text[i + 1] == '"'
            and is_ws_or_edge(i - 1)
            and is_ws_or_edge(i + 2)
        ):
            state = STATE_STRING
            i += 2
            continue

        # S-quote string: `S"` as a standalone token.
        if (
            ch == "S"
            and i + 1 < n
            and text[i + 1] == '"'
            and is_ws_or_edge(i - 1)
            and is_ws_or_edge(i + 2)
        ):
            state = STATE_STRING
            i += 2
            continue

        i += 1

    return state != STATE_CODE


def _position_to_offset(text: str, position: lsp.Position) -> int:
    """Convert an LSP (line, character) 0-based position to an absolute
    character offset in `text`. Clamps past EOF to len(text)."""
    line = position.line
    character = position.character
    if line < 0 or character < 0:
        return 0
    offset = 0
    current_line = 0
    while current_line < line and offset < len(text):
        if text[offset] == "\n":
            current_line += 1
        offset += 1
    # `offset` is now at the start of `line`. Advance by `character`,
    # but stop at the next newline (a cursor past line-end still maps
    # to before the newline).
    end = offset + character
    if end > len(text):
        return len(text)
    # If a newline falls within [offset, end), clamp to it.
    nl = text.find("\n", offset, end)
    if nl != -1:
        return nl
    return end


# ---------------------------------------------------------------------------
# pygls server factory
# ---------------------------------------------------------------------------


def _select_analyzer(uri: str):
    """Choose the right analyzer based on the document URI."""
    if uri.endswith(SIDECAR_SUFFIX):
        return analyze_sidecar
    return analyze_document


def create_server() -> LanguageServer:
    """Build a fresh `LanguageServer` instance with did_open /
    did_change handlers wired to the analyzer."""
    server = LanguageServer(name=SERVER_NAME, version=_get_server_version())

    # Per-server URI → latest text cache. Populated by did_open and
    # did_change. The hover handler reads from this rather than from
    # `ls.workspace.get_text_document(uri)` because the workspace API
    # requires an initialized server (i.e. a live transport), which
    # the test harness intentionally doesn't provide. Keeping our own
    # cache also means hover behavior is deterministic and decoupled
    # from pygls' internal sync-kind state machine.
    document_cache: dict[str, str] = {}
    server._mforth_document_cache = document_cache  # type: ignore[attr-defined]

    @server.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
    def _on_did_open(
        ls: LanguageServer, params: lsp.DidOpenTextDocumentParams
    ) -> None:
        uri = params.text_document.uri
        text = params.text_document.text
        document_cache[uri] = text
        diags = _select_analyzer(uri)(text, uri=uri)
        ls.text_document_publish_diagnostics(
            lsp.PublishDiagnosticsParams(uri=uri, diagnostics=diags)
        )

    @server.feature(lsp.TEXT_DOCUMENT_DID_CHANGE)
    def _on_did_change(
        ls: LanguageServer, params: lsp.DidChangeTextDocumentParams
    ) -> None:
        uri = params.text_document.uri
        # Full-document sync: pygls accumulates the new text in
        # workspace, but the simplest path that works for both the
        # test harness and the live LSP is to read the last
        # WholeDocument change directly.
        text = _extract_change_text(ls, params)
        if text is None:
            return
        document_cache[uri] = text
        diags = _select_analyzer(uri)(text, uri=uri)
        ls.text_document_publish_diagnostics(
            lsp.PublishDiagnosticsParams(uri=uri, diagnostics=diags)
        )

    @server.feature(lsp.TEXT_DOCUMENT_HOVER)
    def _on_hover(
        ls: LanguageServer, params: lsp.HoverParams
    ) -> lsp.Hover | None:
        uri = params.text_document.uri
        # Sidecar TOML documents don't get hover in v1 — only Forth.
        if uri.endswith(SIDECAR_SUFFIX):
            return None
        text = document_cache.get(uri)
        if text is None:
            return None
        return hover_for(text, uri=uri, position=params.position)

    @server.feature(lsp.TEXT_DOCUMENT_COMPLETION)
    def _on_completion(
        ls: LanguageServer, params: lsp.CompletionParams
    ) -> lsp.CompletionList | None:
        uri = params.text_document.uri
        # Sidecar TOML documents don't get completion in v1.
        if uri.endswith(SIDECAR_SUFFIX):
            return None
        text = document_cache.get(uri)
        if text is None:
            return None
        items = completions_for(text, uri=uri, position=params.position)
        return lsp.CompletionList(is_incomplete=False, items=items)

    @server.feature(lsp.TEXT_DOCUMENT_DEFINITION)
    def _on_definition(
        ls: LanguageServer, params: lsp.DefinitionParams
    ) -> lsp.Location | None:
        uri = params.text_document.uri
        # Sidecar TOML documents don't get go-to-definition in v1.
        if uri.endswith(SIDECAR_SUFFIX):
            return None
        text = document_cache.get(uri)
        if text is None:
            return None
        return definition_for(text, uri=uri, position=params.position)

    @server.feature(
        lsp.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL,
        lsp.SemanticTokensRegistrationOptions(
            legend=SEMANTIC_TOKEN_LEGEND, full=True
        ),
    )
    def _on_semantic_tokens(
        ls: LanguageServer, params: lsp.SemanticTokensParams
    ) -> lsp.SemanticTokens:
        uri = params.text_document.uri
        # Sidecar TOML documents don't get semantic tokens in v1.
        if uri.endswith(SIDECAR_SUFFIX):
            return lsp.SemanticTokens(data=[])
        text = document_cache.get(uri)
        if text is None:
            return lsp.SemanticTokens(data=[])
        return semantic_tokens_for(text, uri=uri)

    @server.feature(lsp.WORKSPACE_DID_CHANGE_WATCHED_FILES)
    def _on_did_change_watched_files(
        ls: LanguageServer, params: lsp.DidChangeWatchedFilesParams
    ) -> None:
        # When a `<stem>.world.toml` sidecar changes, re-validate every
        # open `<stem>.fs` Forth document whose link resolution depends on
        # it and republish its diagnostics. Editing the sidecar can flip a
        # name between resolved and unresolved.
        for change in params.changes:
            changed_uri = change.uri
            if not changed_uri.endswith(SIDECAR_SUFFIX):
                continue
            for fs_uri in _forth_docs_for_sidecar(changed_uri, document_cache):
                text = document_cache.get(fs_uri)
                if text is None:
                    continue
                diags = analyze_document(text, uri=fs_uri)
                ls.text_document_publish_diagnostics(
                    lsp.PublishDiagnosticsParams(uri=fs_uri, diagnostics=diags)
                )

    return server


def _forth_docs_for_sidecar(
    sidecar_uri: str, document_cache: dict[str, str]
) -> list[str]:
    """Return the URIs of open `.fs` documents that share the sidecar's
    stem. ``blink.world.toml`` matches an open ``blink.fs``.

    Matching is done on the filesystem-path stem so it's robust to URI
    spelling differences. Returns only documents currently in the cache
    (an edit to a sidecar with no open sibling is a no-op)."""
    import os
    from urllib.parse import urlparse

    parsed = urlparse(sidecar_uri)
    sidecar_path = parsed.path or sidecar_uri
    if not sidecar_path.endswith(SIDECAR_SUFFIX):
        return []
    stem = sidecar_path[: -len(SIDECAR_SUFFIX)]  # drop ".world.toml"

    matches: list[str] = []
    for fs_uri in document_cache:
        if fs_uri.endswith(SIDECAR_SUFFIX):
            continue
        fs_parsed = urlparse(fs_uri)
        fs_path = fs_parsed.path or fs_uri
        fs_base, _ext = os.path.splitext(fs_path)
        if fs_base == stem:
            matches.append(fs_uri)
    return matches


def _extract_change_text(
    ls: LanguageServer, params: lsp.DidChangeTextDocumentParams
) -> str | None:
    """Pull the new full text from a didChange params.

    Prefer the pygls workspace's assembled document state — it tracks
    edits under BOTH ``TextDocumentSyncKind.Full`` AND ``Incremental``.

    The earlier shape (read ``content_changes[-1].text`` first) was
    wrong under Incremental sync: the change payload's ``text`` is the
    REPLACEMENT for the change range, not the full document, so a
    single-character keystroke from Helix etc. would arrive as
    ``text="h"`` and the analyzer would see a 1-character document.
    Regression test: ``test_did_change_handler_incremental_sync_uses_full_document``.
    Bug bead: ``mforth-mig``.
    """
    try:
        doc = ls.workspace.get_text_document(params.text_document.uri)
        if doc.source is not None:
            return doc.source
    except Exception:
        pass
    # Fallback for the teardown-race / no-workspace case: scrape the
    # last content_change. Only correct under Full sync.
    if params.content_changes:
        last = params.content_changes[-1]
        text = getattr(last, "text", None)
        if isinstance(text, str):
            return text
    return None


def _get_server_version() -> str:
    """Return the server version string. Defers to mforth.__version__
    so the LSP reports a version that tracks the package."""
    import mforth

    return mforth.__version__


# ---------------------------------------------------------------------------
# Stdio entry point
# ---------------------------------------------------------------------------


def serve_stdio() -> int:
    """Run the server over stdio. Called from `mforth lsp` and
    `python -m mforth.lsp`."""
    server = create_server()
    server.start_io()
    return 0


__all__ = [
    "DIAGNOSTIC_SOURCE",
    "SEMANTIC_TOKEN_LEGEND",
    "SERVER_NAME",
    "SIDECAR_SUFFIX",
    "analyze_document",
    "analyze_sidecar",
    "completions_for",
    "create_server",
    "definition_for",
    "hover_for",
    "semantic_token_spans",
    "semantic_tokens_for",
    "serve_stdio",
]
