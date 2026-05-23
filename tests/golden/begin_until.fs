\ begin_until.fs — BEGIN/UNTIL loop fixture, xfail until
\ mforth-10t.17 ships (mlog control-flow codegen).
\
\ Once .17 ships, drop the `begin_until` entry from XFAIL_FIXTURES in
\ test_golden.py and run `pytest tests/golden --update-golden` to
\ populate the expected mlog.

: countdown  ( n -- )
  BEGIN
    1 -
    DUP 0 =
  UNTIL
  DROP
;

5 countdown
