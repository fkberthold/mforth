\ @exercise sim-101/02-label
\ Reference solution.
: readout ( -- )
  S" width=" PRINT
  @mapw PRINT
  display PRINTFLUSH
;
