\ @exercise forth-101/05-triplicate
\ Reference solution. DUP twice: ( a -- a a ) then ( a a -- a a a ).
: triplicate ( a -- a a a ) DUP DUP ;
