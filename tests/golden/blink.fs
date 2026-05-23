\ blink.fs — v1 demo half, xfail until mforth-10t.18 + .19 ship.
\
\ Toggles a switch on/off forever, with a half-second WAIT pacing it.
\ This is the canonical blink demo named in `mforth-v1-demo`.
\
\ Once .18 (Mindustry primitive emit) and .19 (label resolution +
\ sidecar substitution + getlink prologue) ship, drop the
\ `blink` entry from XFAIL_FIXTURES in test_golden.py and run
\ `pytest tests/golden --update-golden` to populate the expected mlog.

VARIABLE on

: tick
  on @ 0= IF 1 on ! ELSE 0 on ! THEN
  switch1 SENSOR
  drop                   \ placeholder until SENSOR-write wiring lands
  0.5 WAIT
;

tick
