\ Part 3 — Just Charge port: keep a power network charged to ~95%
\ with hysteresis. The VARIABLE `charging` carries last tick's
\ decision into this tick — that's what makes the toggle sticky.
VARIABLE charging
VARIABLE max
VARIABLE power

node1 @powerNetCapacity SENSOR max !
node1 @powerNetStored   SENSOR power !

\ notMin = power < max * 0.95. v1 mforth has no float literals,
\ so use integer math: power*100 < max*95 says the same thing.
power @ 100 *  max @ 95 *  <        \ ( -- notMin )
charging @ OR                       \ ( -- charging|notMin )

\ notFull = power < max
power @  max @  <                   \ ( -- (charging|notMin) notFull )
AND                                 \ ( -- charging' )

DUP charging !                      \ save for next tick, keep flag on stack
IF generator1 1 CONTROL-ENABLED
ELSE generator1 0 CONTROL-ENABLED
THEN
