\ @exercise forth-103/03-report
\ Reference solution. The label is a literal string; the value is on the
\ stack. Two prints fill the buffer in order: label, then number.
: report ( n -- ) S" score=" PRINT PRINT ;
