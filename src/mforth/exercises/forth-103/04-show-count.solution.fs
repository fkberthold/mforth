\ @exercise forth-103/04-show-count
\ Reference solution. State (the `count` cell) meets output (a labelled
\ message): print the label, then fetch-and-print the value.
VARIABLE count
0 count !
: show-count ( -- ) S" count=" PRINT count @ PRINT ;
