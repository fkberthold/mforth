\ @exercise sim-101/01-greet
\ Reference solution. The `\ @exercise <id>` line is the marker
\ `mforth check` reads; the learner edits only their own copy.
: greet ( -- )
  S" online" PRINT
  display PRINTFLUSH
;
