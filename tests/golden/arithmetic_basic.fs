\ arithmetic_basic.fs — canonical golden fixture for bead mforth-10t.29.
\
\ Exercises:
\   * LitInt push (slot allocation s0, s1).
\   * `+`  → mlog `op add` with slot reuse.
\   * `*`  → mlog `op mul` with slot reuse.
\
\ Hand-verified against the mforth-10t.16 emitter contract on 2026-05-23.
\ Re-derive by running `pytest tests/golden --update-golden`.

1 2 + 3 *
