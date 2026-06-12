\ @exercise forth-103/05-vocabulary
\ Reference solution. `cubed` is built ON TOP of `squared` — the larger
\ word names the smaller one instead of repeating `DUP *`. That reuse is
\ the whole point of factoring.
: squared ( n -- n*n ) DUP * ;
: cubed   ( n -- n*n*n ) DUP squared * ;
