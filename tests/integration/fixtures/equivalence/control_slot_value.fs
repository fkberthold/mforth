\ mforth-vdt equivalence fixture — CONTROL-ENABLED / CONTROL-CONFIG with
\ a stack-computed VALUE operand (block stays literal). Models the
\ pedagogical heart of USR's wiki "All In" / "ConveyorBlock" port: a
\ runtime threshold comparison drives the flag, the block name stays a
\ sidecar link.
\
\ The headline property: this `.fs` must produce identical event
\ sequences when run via the host REPL and when compiled-then-
\ interpreted via the in-repo mlog interpreter. Specifically:
\
\   1. CONTROL-ENABLED with a slot-value (computed flag) and literal
\      block lifts to a SINGLE `control enabled graphC s<i> 0 0 0`
\      instruction — no IF/ELSE inflation.
\   2. CONTROL-CONFIG with a slot-value (computed @-id) and literal
\      block lifts symmetrically.
\   3. Sidecar Mode A substitution still works for PRINTFLUSH after
\      the CONTROL ops (orthogonal-passes invariant).
\
\ Block links: `graphC` (switch), `sorter1` (generic), `display`
\ (message → message1 by Mode A).

\ CONTROL-ENABLED with computed flag (3 > 2 → 1):
\   graphC is the literal block; the `3 2 >` computes the flag.
\   Forth stack order is `( block flag -- )` so source is block-first,
\   then flag-computation. The lift recognises body[i] = graphC as a
\   block-literal-source and scans forward to the matching CONTROL
\   primitive, emitting one `control enabled graphC s<i> 0 0 0`.
graphC 3 2 > CONTROL-ENABLED

\ CONTROL-CONFIG with computed value: arithmetic produces the value.
\ Same shape as above; pin both lifters.
sorter1 5 3 + CONTROL-CONFIG

S" status: armed" PRINT
display PRINTFLUSH
