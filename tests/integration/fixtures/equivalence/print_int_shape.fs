\ mforth-05h equivalence fixture — PRINT of an integer-valued numeric
\ value must render WITHOUT a trailing `.0` on both backends, matching
\ the in-game `print` instruction's stringification rule.
\
\ Before the mforth-05h fix, the REPL's PRINT primitive str()'d the
\ value verbatim — Python str(1.0) → "1.0" — while the mlog interpreter
\ rendered integer-valued floats as "1". After: both sides render "1",
\ "2", "3".
\
\ Mixed with a true float to confirm the non-integer case still renders
\ as a decimal (2.5 stays "2.5").
1 PRINT
2 PRINT
3 PRINT
5 2 / PRINT
display PRINTFLUSH
