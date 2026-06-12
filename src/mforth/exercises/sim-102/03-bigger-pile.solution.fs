\ @exercise sim-102/03-bigger-pile
\ Milestone 1 of the sorter capstone: pick the bigger of two piles.
\ `>` is ( left right -- flag ); ties fall to the ELSE arm (RIGHT).
: bigger-pile ( left right -- )
  > IF S" LEFT" PRINT ELSE S" RIGHT" PRINT THEN
;
