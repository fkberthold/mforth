\ mforth-dlr equivalence fixture — REPL `/` (Python float div) must
\ match mlog `op div` (float div). Two cases:
\
\   5 2 / PRINT     → both sides produce 2.5 → text "2.5"
\   10 4 / PRINT    → both sides produce 2.5 → text "2.5"
\   20 4 / PRINT    → both sides produce 5.0 → text "5" (mforth-05h)
\
\ Before the mforth-dlr fix, mlog emitted `op idiv` so 5 2 / on the
\ mlog side was 2 (text "2") while the REPL produced 2.5. After:
\ both sides agree.
5 2 / PRINT
10 4 / PRINT
20 4 / PRINT
display PRINTFLUSH
