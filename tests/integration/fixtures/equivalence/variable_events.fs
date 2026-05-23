\ mforth-0qi equivalence fixture — VARIABLE @/! must produce identical
\ VariableReadEvent / VariableWriteEvent streams on both backends.
\
\ Before the mforth-0qi fix, the REPL emitted VariableRead/Write on
\ every @/! while the mlog interpreter emitted none — so this fixture
\ would have produced 7 events on REPL (2 read + 1 write + 2 read + 1
\ write + 1 print + 1 flush after the printflush; counting roughly)
\ and only 2 on mlog. After: both sides emit the same Variable events
\ for the user-declared `counter`.
VARIABLE counter
3 counter !
counter @ 1 + counter !
counter @ PRINT
display PRINTFLUSH
