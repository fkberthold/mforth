\ @exercise forth-101/11-quadruple
\ Reference solution. Factor quadruple out of double: doubling twice is *4.
: double ( n -- n*2 ) DUP + ;
: quadruple ( n -- n*4 ) double double ;
