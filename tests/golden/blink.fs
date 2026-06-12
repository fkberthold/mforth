\ blink.fs — the canonical v1 blink demo named in `mforth-v1-demo`.
\
\ Toggles a flag on/off forever, with a half-second WAIT pacing it.
\ The toggle uses `0=` ( n -- flag ): when `on` is 0 it stores 1, and
\ when `on` is non-zero it stores 0 — a one-bit oscillator.
\
\ Golden is real (no longer xfail) as of bead mforth-0fd, which landed
\ the `0=` word. Regenerate with `pytest tests/golden --update-golden`
\ after any intentional codegen change.

VARIABLE on

: tick
  on @ 0= IF 1 on ! ELSE 0 on ! THEN
  switch1 @config SENSOR
  drop                   \ placeholder until SENSOR-write wiring lands
  0.5 WAIT
;

tick
