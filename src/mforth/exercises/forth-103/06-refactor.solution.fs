\ @exercise forth-103/06-refactor
\ Reference solution. The doubling lives in its own named word, so
\ `announce` reads top-to-bottom as intent: print a label, then print the
\ doubled number. Each line of the body says what it does.
: double   ( n -- n*2 ) DUP + ;
: announce ( n -- ) S" doubled=" PRINT double PRINT ;
