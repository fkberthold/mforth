\ @exercise forth-101/09-cube
\ Reference solution. Three copies, then fold with two multiplies.
: cube ( n -- n*n*n ) DUP DUP * * ;
