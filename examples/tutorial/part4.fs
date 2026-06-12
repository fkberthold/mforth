\ Part 4 — Sorter Picker port (full three-arm version).
\ Drain whichever item vault1 holds more of; if BOTH are gone,
\ stop the unloader by configuring it to null.
vault1 @surge-alloy SENSOR       \ ( -- hasSurge )
vault1 @blast-compound SENSOR    \ ( hasSurge -- hasSurge hasBlast )
OVER OVER                        \ ( hasSurge hasBlast -- hasSurge hasBlast hasSurge hasBlast )
+ 0 = IF
  \ Both piles empty — stop unloading.
  DROP DROP
  unloader1 NULL CONTROL-CONFIG
ELSE
  > IF
    unloader1 @blast-compound CONTROL-CONFIG
  ELSE
    unloader1 @surge-alloy CONTROL-CONFIG
  THEN
THEN
