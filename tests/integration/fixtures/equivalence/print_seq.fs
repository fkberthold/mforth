\ Print/printflush sequence with a WAIT in the middle — tests clock advance
\ AND sidecar Mode A (target).
S" hello" PRINT
display PRINTFLUSH
1 WAIT
S" world" PRINT
display PRINTFLUSH
