; tree-sitter highlights for mforth
;
; Token-class alignment with src/mforth/lex.py and the eventual LSP's
; semantic-token encoding. The LSP can refine `word` nodes into
; @function.builtin / @function / @variable per its dictionary; this query
; does the same classification statically via #match? predicates over the
; well-known v1 built-in names. When the LSP is running, its semanticTokens
; override these — but Helix users without the LSP still get useful color
; from tree-sitter alone, matching the design's "tree-sitter is optional"
; goal.

; ---- Trivia ----------------------------------------------------------------

(line_comment) @comment
(block_comment) @comment

; ---- Literals --------------------------------------------------------------

(number) @number
(string_literal) @string

; ---- Definitions -----------------------------------------------------------

(definition (colon) @keyword)
(definition (semicolon) @keyword)
(definition name: (definition_name) @function)

; ---- Words — classified by name -------------------------------------------
;
; Control-flow keywords. Match on case-insensitive name (Forth tradition).
; The mforth parser case-folds via .lower(); we mirror that here.

((word) @keyword.control
 (#match? @keyword.control "^(?i)(if|else|then|begin|until|while|repeat|do|loop)$"))

; Stack manipulation words.
((word) @function.builtin
 (#match? @function.builtin "^(?i)(dup|drop|swap|over|rot|nip|tuck)$"))

; Arithmetic and comparison operators (mforth dialect v1).
((word) @operator
 (#match? @operator "^(?i)(\\+|-|\\*|/|mod|<|>|=|<>|<=|>=|and|or|not)$"))

; Variable + memory words.
((word) @keyword
 (#match? @keyword "^(?i)(variable|@|!)$"))

; Mindustry primitives — these are mforth-name → mlog-instruction bindings.
; The eventual LSP will surface their stack effects on hover; here we just
; color them distinctly from generic user words.
((word) @function.builtin
 (#match? @function.builtin "^(?i)(print|printflush|wait|sensor|getlink)$"))

; Loop counters I and J — read DO/LOOP indices. (0,1) effect.
((word) @variable.builtin
 (#match? @variable.builtin "^(?i)(i|j)$"))

; Everything else is a user-defined or unknown word. The LSP can promote
; these to @function (if defined) or surface a diagnostic (if undefined);
; tree-sitter falls back to a neutral classification.
(word) @variable
