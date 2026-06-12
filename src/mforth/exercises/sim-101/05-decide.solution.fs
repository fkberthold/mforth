\ @exercise sim-101/05-decide
\ Reference solution.
: should-run? ( -- flag )
  vault1 @totalItems SENSOR
  vault1 @itemCapacity SENSOR
  <
;
