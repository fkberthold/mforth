\ @exercise sim-101/04-half-full
\ Reference solution.
: over-half? ( -- flag )
  vault1 @totalItems SENSOR 2 *
  vault1 @itemCapacity SENSOR
  >
;
