\ @exercise forth-101/01-double
\ Reference solution. The `\ @exercise <id>` line above is the metadata
\ marker `mforth check` reads to pick the spec; the learner edits only
\ their own copy of this file.
: double ( n -- n*2 ) DUP + ;
