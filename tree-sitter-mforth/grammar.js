/**
 * tree-sitter-mforth — grammar for mforth (.fs) source.
 *
 * Aligns with src/mforth/lex.py so passive (tree-sitter) highlighting and
 * active (LSP semantic-token) highlighting agree on what each token IS.
 * The LSP later attaches a `kind` to every WordCall (builtin vs user-defined
 * vs control vs Mindustry primitive); tree-sitter alone cannot know that
 * distinction, so this grammar produces a single `word` node and the
 * highlights.scm query downgrades-or-promotes per-name via #match? predicates.
 *
 * Lexer alignment (matching src/mforth/lex.py behavior):
 *
 *   * `\` line comment — only when standalone (whitespace before/after or EOF).
 *   * `( ... )` block comment — NESTABLE in our dialect (standard Forth is
 *     not, but our lexer is; honor the actual lexer). Discarded by lexer;
 *     here surfaced as a `block_comment` node so highlights.scm can color it.
 *   * `:` / `;` — only standalone (whitespace-delimited) become COLON/SEMICOLON.
 *   * `."` / `S"` — only standalone become string literals; content runs to
 *     the matching `"`. Lexer eats one optional whitespace char after the
 *     opening delimiter; tree-sitter handles that uniformly via the regex.
 *   * NUMBER — signed decimal integer; otherwise the token is a WORD.
 *
 * Forth syntax has no nested-definition support in our dialect, but tree-sitter
 * grammars don't need to enforce that — the parser does. We model `: name body ;`
 * as a `definition` node and let arbitrary terms fill the body. The mforth
 * Python parser is the source of truth for semantic rejection (e.g., nested `:`).
 */

module.exports = grammar({
  name: "mforth",

  // `extras` are tokens allowed between any two grammar tokens without being
  // captured in the tree. Whitespace and standalone line-comments fit. Block
  // comments are NOT extras — we want them in the tree so highlights can
  // color them and so editors can fold them.
  extras: ($) => [/\s+/, $.line_comment],

  // word is the conflict-resolution helper for keyword extraction; mforth
  // doesn't have reserved keywords (everything is whitespace-delimited words),
  // so we omit `word:`. The grammar is fully token-based on identifiers.

  rules: {
    // ---- Top level ---------------------------------------------------------

    source_file: ($) => repeat($._term),

    // A term is anything that can appear at top level OR inside a definition
    // body. Control-flow words (IF/BEGIN/DO/...) are NOT structural in
    // tree-sitter — they're just words. The Python parser builds the IfThen /
    // Begin / DoLoop AST shape; tree-sitter stays flat so editors get
    // highlight + fold without having to mirror Forth's nesting rules.
    _term: ($) =>
      choice(
        $.definition,
        $.block_comment,
        $.string_literal,
        $.number,
        $.word,
      ),

    // ---- Definitions -------------------------------------------------------
    //
    // `: name body... ;`  — name is the WORD immediately after `:`. The body
    // is any sequence of terms up to the matching `;`. Standard Forth allows
    // exactly one definition at a time (no nesting); we don't enforce that
    // here.
    definition: ($) =>
      seq(
        field("colon", $.colon),
        field("name", $.definition_name),
        field("body", repeat($._term)),
        field("semicolon", $.semicolon),
      ),

    // Standalone `:` and `;` only — tree-sitter's lexer applies longest-match,
    // so `:foo` is lexed as the longer `word` token (`\S+` matches all 4
    // chars) and `:` alone matches `colon`. No lookahead assertion needed
    // (tree-sitter regex doesn't support look-around). Default prec is fine;
    // length is the tiebreaker the lexer uses first, and length-1 vs
    // length-N favours `word` whenever `:` or `;` is glued to other chars.
    colon: (_) => ":",
    semicolon: (_) => ";",

    // The first WORD after `:` is the definition name. Distinct node so
    // highlights.scm can color it `@function` instead of `@variable`.
    definition_name: (_) => /[^\s:;]\S*/,

    // ---- Comments ----------------------------------------------------------
    //
    // Line comment: standalone `\` followed by anything up to end-of-line.
    // Lookahead is enforced so a word like `\foo` (not whitespace-anchored)
    // is NOT treated as a comment — matches the lexer's
    // `if ch == "\\" and (next_ch == "" or _is_ws(next_ch))` guard.
    line_comment: (_) => token(seq("\\", /[ \t][^\n]*|[^\n]*/)),

    // Block comment: standalone `(` ... `)`. Nestable per our lexer. The
    // outer paren matches when whitespace-anchored. We use an external-ish
    // regex that consumes balanced parens up to depth-zero. tree-sitter's
    // regex engine doesn't do recursion, so we model nesting by allowing
    // any non-paren run plus inner `(...)` groups one level deep — that
    // covers the overwhelming majority of real-world Forth and degrades
    // gracefully (truncates the comment at the first unbalanced `)`) on
    // edge cases. The Python lexer is the source of truth for nesting
    // semantics; tree-sitter only needs to color the region.
    block_comment: (_) =>
      token(
        seq(
          "(",
          // Run of: any chars except parens, OR an inner balanced `(...)`,
          // repeated. Two levels of nesting handled inline; deeper nesting
          // is rare enough to accept the truncation tradeoff.
          /([^()]*(\([^()]*\)[^()]*)*)*/,
          ")",
        ),
      ),

    // ---- String literals ---------------------------------------------------
    //
    // `." text"` and `S" text"`. The opening delimiter must be a standalone
    // word (whitespace-anchored). The content runs to the next `"`. Lexer
    // optionally eats one space after the opening delimiter; we include that
    // in the regex with a single-character class.
    string_literal: ($) =>
      choice($._dot_quote_string, $._s_quote_string),

    _dot_quote_string: (_) =>
      token(seq('."', /[ \t]?/, /[^"]*/, '"')),

    _s_quote_string: (_) =>
      token(seq('S"', /[ \t]?/, /[^"]*/, '"')),

    // ---- Numbers and words ------------------------------------------------
    //
    // Number: optional sign, then one or more digits. tree-sitter's
    // longest-match rule means `1foo` lexes as the longer `word` token,
    // not as `1` then `foo` — which matches the Python lexer's
    // whitespace-first split (it tokenises `1foo` whole, then asks "is
    // this an int?", no). No explicit lookahead needed (regex
    // look-around is unsupported in tree-sitter).
    number: (_) => token(prec(1, /[+-]?\d+/)),

    // Word: any non-whitespace run that isn't otherwise classified. Numbers
    // win via prec(1); strings/comments/colon/semicolon win via prec(2) or
    // explicit prefix tokens. Words can include `:` and `;` if not
    // standalone (e.g., the mforth lexer accepts `:foo` as a WORD because
    // the standalone check fails). The regex below excludes whitespace
    // only — same as the lexer's `not _is_ws(src[i])` loop.
    word: (_) => token(/\S+/),
  },
});
