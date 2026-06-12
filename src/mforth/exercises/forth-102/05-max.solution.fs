\ @exercise forth-102/05-max
\ Reference solution. ( a b ) OVER OVER -> ( a b a b ); a < b leaves
\ ( a b flag ). If a < b we SWAP so b is on top; either way DROP the
\ smaller. Both branches leave depth ( a b -- 1 ), so it balances.
: max ( a b -- max ) OVER OVER < IF SWAP THEN DROP ;
