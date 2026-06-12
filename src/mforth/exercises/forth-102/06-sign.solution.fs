\ @exercise forth-102/06-sign
\ Reference solution. Nested IF/ELSE: the outer ELSE still has the
\ original n on the stack, so the inner test re-uses it. Every leaf of
\ the tree leaves one value, so all branches balance.
: sign ( n -- s )
  DUP 0 > IF
    DROP 1
  ELSE
    0 < IF -1 ELSE 0 THEN
  THEN ;
