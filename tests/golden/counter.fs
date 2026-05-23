\ counter.fs — v1 demo half, xfail until mforth-10t.18 + .19 ship.
\
\ Counts up, printing the count to a message block once per second.
\ The counter VARIABLE compiles to a bare mlog variable per CLAUDE.md
\ "v1 stays cell-free" rule.
\
\ Once .18 (Mindustry primitives) and .19 (final pass) ship, drop
\ the `counter` entry from XFAIL_FIXTURES in test_golden.py and run
\ `pytest tests/golden --update-golden` to populate the expected mlog.

VARIABLE n

: tick
  n @ 1 + DUP n !       \ increment and keep the new value on stack
  PRINT
  display PRINTFLUSH
  1 WAIT
;

tick
