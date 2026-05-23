\ if_then.fs — IF/ELSE/THEN control-flow fixture, xfail until
\ mforth-10t.17 ships (mlog control-flow codegen).
\
\ Once .17 ships, drop the `if_then` entry from XFAIL_FIXTURES in
\ test_golden.py and run `pytest tests/golden --update-golden` to
\ populate the expected mlog.

: clamp_zero  ( n -- m )
  DUP 0 < IF DROP 0 THEN
;

-3 clamp_zero
