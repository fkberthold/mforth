\ mforth-xk7 equivalence fixture — float literals in source.
\
\ Pins the headline REPL ↔ mlog equivalence property for the new
\ LitFloat AST node: source-level decimals (0.95, 3.14, etc.) must
\ produce identical event sequences on both backends.
\
\ The cases exercise:
\   * a sub-1 float literal (0.95) — the canonical "Just Charge" use
\     case the tutorial Part 3 had to 100x-scale around (bead notes).
\   * a > 1 float literal (3.14) — for symmetry.
\   * a negative float literal (-2.5) — the lexer's sign-handling pin.
\   * an integer-valued float (1.0) — exercises the mforth-05h
\     PRINT integer-float rendering rule. PRINT must emit "1" (no
\     trailing ".0") on both backends.
\   * a multiplication landing on an integer (4 0.25 *) — exercises
\     the LitFloat → emit `set s<i> 0.25` path through the slot
\     allocator, then `op mul`. Result is 1.0 → printed as "1".
\
\ Before mforth-xk7: these literals failed to lex (`0.95` became a
\ WORD that dictionary resolution rejected). After: equivalence
\ property holds end-to-end.
0.95 PRINT
3.14 PRINT
-2.5 PRINT
1.0 PRINT
4 0.25 * PRINT
display PRINTFLUSH
