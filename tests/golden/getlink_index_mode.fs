\ getlink_index_mode.fs — exercises the sidecar's index-mode link
\ binding (Mode B), which emits a GETLINK prologue per CLAUDE.md
\ "Sidecar `.world.toml` indirection" rule.  xfail until
\ mforth-10t.18 (Mindustry primitives) + mforth-10t.19 (final pass:
\ label resolution + sidecar substitution + getlink prologue) ship.
\
\ Once those ship, drop the `getlink_index_mode` entry from
\ XFAIL_FIXTURES in test_golden.py and run
\ `pytest tests/golden --update-golden` to populate the expected mlog.

display PRINT
display PRINTFLUSH
