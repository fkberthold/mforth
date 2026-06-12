\ Part 4 — Sorter Picker port.
\ Drain whichever item vault1 holds more of, to keep them balanced.
vault1 @surge-alloy SENSOR       \ ( -- hasSurge )
vault1 @blast-compound SENSOR    \ ( hasSurge -- hasSurge hasBlast )

> IF
  unloader1 @blast-compound CONTROL-CONFIG
ELSE
  unloader1 @surge-alloy CONTROL-CONFIG
THEN
