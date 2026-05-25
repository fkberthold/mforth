# mlog lowering

The exact instruction(s) mforth emits for each Forth construct it
lowers. One row per surface word. The "mlog" column shows the
emission template; the "notes" column flags operand staging, label
conventions, and fusion quirks.

This page documents *what mforth emits*. It is not a spec of the mlog
instruction set itself — for that, see Mindustry's logic-processor
documentation. For the per-word *semantic*, see `dictionary.md`. For
how sidecar bindings rewrite block-handle operands in the finalize
pass, see `sidecar-schema.md`.

## Conventions used in this page

- `s<i>` is a stack-slot mlog variable. The slot allocator assigns
  indices statically: at static depth `D`, the bottom-of-stack lives in
  `s0` and the top in `s<D-1>`. A push at depth `D` writes `s<D>`; a
  pop at depth `D` reads `s<D-1>`.
- `s<a>`, `s<b>`, `s<c>` denote distinct slot indices the allocator
  picked for a particular word's input/output positions.
- `__swap_tmp` is a reserved scratch variable used by `SWAP` / `ROT` /
  `TUCK`. Forth identifiers starting with `__` are reserved.
- `__do_idx_<N>` and `__do_limit_<N>` are the loop counter and bound
  for the `N`-th `DO/LOOP` in the program (counted globally, not per
  scope). Nesting cannot collide.
- `L_if_<N>_end` / `L_if_<N>_else` / `L_begin_<N>_top` /
  `L_begin_<N>_after` / `L_do_<N>_top` are symbolic labels emitted by
  the control-flow arms; the finalize pass resolves them to 0-indexed
  line numbers.
- "(2, 1)" is shorthand for stack effect `( a b -- c )` — two inputs,
  one output.

## Literals

| Forth | mlog | Notes |
|-------|------|-------|
| `<int>`        | `set s<i> <int>`        | (0, 1). `<int>` is rendered via Python `str(value)`. |
| `<float>`      | `set s<i> <float>`      | (0, 1). Decimal-with-fractional-digits literal (e.g. `0.95`, `3.14`, `-2.5`, `1.0e-3`). Bead mforth-xk7. Rendered via Python `repr(value)` so the operand round-trips through mlog's tokenizer cleanly (decimal for ordinary magnitudes; scientific form for very small/large). |
| `S" <text>"`   | `set s<i> "<text>"`     | (0, 1). Quotes preserved in the operand. mlog's `set` accepts a quoted-string r-value. |

A literal inside a Mindustry-primitive lifting window (`<lit> PRINT`,
`<lit> PRINTFLUSH`, `<lit> <lit> SENSOR`) does *not* emit a standalone
`set` — the literal value folds into the primitive's operand and the
otherwise-required staging `set` is elided. See "Mindustry primitives
— lifted forms" below.

## Arithmetic, comparison, logical

All binary; all emit `op <mlog-op> <write> <read-a> <read-b>` against
the allocator's slot picks. The 0/1 result of a comparison is kept
verbatim — mforth does *not* translate to Forth-traditional 0/-1 (see
the `emit.py` module docstring for the rationale; the host REPL uses
the same encoding for equivalence).

| Forth | mlog | Notes |
|-------|------|-------|
| `+`   | `op add s<c> s<a> s<b>`        | (2, 1) |
| `-`   | `op sub s<c> s<a> s<b>`        | (2, 1) |
| `*`   | `op mul s<c> s<a> s<b>`        | (2, 1) |
| `/`   | `op div s<c> s<a> s<b>`        | (2, 1). Float division (NOT `idiv`) — matches the REPL's Python `/`. The pragmatic-dialect choice; see CLAUDE.md "REPL ↔ mlog convergence decisions". |
| `MOD` | `op mod s<c> s<a> s<b>`        | (2, 1) |
| `=`   | `op equal s<c> s<a> s<b>`      | (2, 1). Writes 0 or 1. |
| `<>`  | `op notEqual s<c> s<a> s<b>`   | (2, 1) |
| `<`   | `op lessThan s<c> s<a> s<b>`   | (2, 1) |
| `>`   | `op greaterThan s<c> s<a> s<b>`| (2, 1) |
| `<=`  | `op lessThanEq s<c> s<a> s<b>` | (2, 1) |
| `>=`  | `op greaterThanEq s<c> s<a> s<b>` | (2, 1) |
| `AND` | `op land s<c> s<a> s<b>`       | (2, 1). `land` = logical-and. |
| `OR`  | `op or s<c> s<a> s<b>`         | (2, 1) |
| `NOT` | `op not s<a> s<a> 0`           | (1, 1). Allocator gives the same slot for read and write; mlog's `op not` is unary but takes a third operand slot, which we pad with `0`. |

## Stack juggling

The allocator's `read_slots` / `write_slots` for a stack-op already
encode "which slot the value started in" and "which slot it ends up
in". The emitter just shuffles values between those slot variables.

| Forth | mlog | Notes |
|-------|------|-------|
| `DUP`  | `set s<i+1> s<i>` | (1, 2). One copy. |
| `DROP` | *(no instruction)* | (1, 0). Slot becomes dead; the allocator stops referencing it. Zero-cost. |
| `OVER` | `set s<i+1> s<i-1>` | (2, 3). Copies the under-value to the new top. |
| `NIP`  | `set s<i-1> s<i>` | (2, 1). Squashes the under-value with the top. |
| `SWAP` | `set __swap_tmp s<a>` <br> `set s<a> s<b>` <br> `set s<b> __swap_tmp` | (2, 2). Three sets via the scratch variable; a reserved slot would inflate worst-case slot count for no gain. |
| `ROT`  | `set __swap_tmp s<a>` <br> `set s<a> s<b>` <br> `set s<b> s<c>` <br> `set s<c> __swap_tmp` | (3, 3). Four sets via the scratch. |
| `TUCK` | `set __swap_tmp s<b>` <br> `set s<b> s<a>` <br> `set s<a> __swap_tmp` <br> `set s<c> __swap_tmp` | (2, 3). `a b -- b a b`. Four sets. |

## Variables — declare, fetch, store

The dictionary models `<varname>` (a `UserVariable` reference) as a
(0, 1) "pushes address" word. v1 has no addressable cells, so the
emitter fuses the address-push with the immediately-following `@` or
`!` into a single `set` and never materializes an on-stack address.

The fusion runs at the AST level (`_fuse_variable_patterns` in
`emit.py`) before stack allocation, so the slot map agrees with the
emitted instruction stream.

| Forth | mlog | Notes |
|-------|------|-------|
| `VARIABLE <name>`        | *(no instruction)* | Declaration only; the mlog variable is created the first time it is written. |
| `<value> <name> !` (fused) | `set <name> s<i>` | (writes the value-slot into the named variable). The standalone bare-address WordCall emits nothing; the `!` is what materializes. |
| `<name> @` (fused) | `set s<i> <name>` | (reads the named variable into the next stack slot). |
| `<name>` (bare, no @/!) | *(error)* | v1 is cell-free. `NotImplementedError`: "VARIABLE address '<name>' left on the stack". `<name> DUP @` falls into this; the fix is `<name> @ DUP`. |

## Control flow

Labels are minted from per-construct counters (`_if_counter`,
`_begin_counter`, `_do_counter`) that are program-global, not
per-scope — mlog's address space is flat. Labels attach to the *next*
emitted instruction; if no instruction follows, the label is held as a
sentinel until the finalize pass resolves it to "line past the last
instruction" (which mlog's auto-loop turns into "jump back to line
0", correct for IF/THEN fall-through).

### IF / THEN

| Forth | mlog | Notes |
|-------|------|-------|
| `IF <then> THEN` | `jump L_if_<N>_end equal s<flag> 0` <br> `<then-body>` <br> `L_if_<N>_end:` | (1, 0) at the IF. Flag is at `s<D-1>`. Falsey skips to end. |
| `IF <then> ELSE <else> THEN` | `jump L_if_<N>_else equal s<flag> 0` <br> `<then-body>` <br> `jump L_if_<N>_end always 0 0` <br> `L_if_<N>_else:` <br> `<else-body>` <br> `L_if_<N>_end:` | Both bodies must produce the same net depth (stackcheck enforces). |

### BEGIN / UNTIL

| Forth | mlog | Notes |
|-------|------|-------|
| `BEGIN <body> UNTIL` | `L_begin_<N>_top:` <br> `<body>` <br> `jump L_begin_<N>_top equal s<flag> 0` | Loops while flag is zero. Flag is at the entry-depth slot of the body. |

### BEGIN / WHILE / REPEAT

| Forth | mlog | Notes |
|-------|------|-------|
| `BEGIN <test> WHILE <body> REPEAT` | `L_begin_<N>_top:` <br> `<test-body>` <br> `jump L_begin_<N>_after equal s<flag> 0` <br> `<body>` <br> `jump L_begin_<N>_top always 0 0` <br> `L_begin_<N>_after:` | Falsey flag from the test exits the loop. Both branches are stack-neutral. |

### DO / LOOP

ANS Forth convention: `( limit index -- )`, index on top.

| Forth | mlog | Notes |
|-------|------|-------|
| `DO <body> LOOP` | `set __do_idx_<N> s<index>` <br> `set __do_limit_<N> s<limit>` <br> `L_do_<N>_top:` <br> `<body>` <br> `op add __do_idx_<N> __do_idx_<N> 1` <br> `jump L_do_<N>_top lessThan __do_idx_<N> __do_limit_<N>` | Per-N counters mean nested DO/LOOPs never collide. |
| `I` (inside DO/LOOP) | `set s<i> __do_idx_<N>` | (0, 1). `N` is the innermost active loop. |
| `J` (inside doubly-nested DO/LOOP) | `set s<i> __do_idx_<N>` | (0, 1). `N` is the *outer* of the two innermost loops. Using `J` with less than two enclosing loops is an emit-time error. |

## Mindustry primitives — slot-reference forms

When the operands are runtime-computed (came from arithmetic, a
fetched variable, an earlier primitive), the primitive emits against
slot references. The lifted forms (next section) preempt this when
the operands are compile-time literals.

| Forth | mlog | Notes |
|-------|------|-------|
| `PRINT`      | `print s<i>`                       | (1, 0) |
| `PRINTFLUSH` | `printflush s<i>`                  | (1, 0). Block handle as a slot is unusual in v1 — usually lifts. |
| `WAIT`       | `wait s<i>`                        | (1, 0). Never lifts (intrinsically runtime). |
| `SENSOR`     | `sensor s<i-2> s<i-2> s<i-1>`      | (2, 1). Result aliases the block slot — mlog reads operands before writing within an instruction. |
| `GETLINK`    | `getlink s<i> s<i>`                | (1, 1). Index slot is reused as the output handle slot. Never lifts. |

## Mindustry primitives — lifted forms

The emit pass scans for two- and three-term windows where a literal,
a `UserVariable` (sidecar-pre-seeded link name), or an `@-identifier`
immediately precedes a Mindustry primitive. The match folds operands
into the primitive instruction and elides the staging `set`.

Selection rules: `PRINT` accepts any of LitInt/LitFloat/LitStr/
UserVariable/@-identifier; `PRINTFLUSH` accepts LitStr/UserVariable/
@-identifier (no LitInt/LitFloat — numeric block handles are
nonsense); `SENSOR` accepts any block-source for operand 1 and
LitStr/@-identifier for operand 2.

| Forth | mlog | Notes |
|-------|------|-------|
| `<int> PRINT`           | `print <int>`              | Literal lift. |
| `<float> PRINT`         | `print <float>`            | Float-literal lift (bead mforth-xk7). `<float>` rendered via Python `repr()` to match the slot-form lowering. |
| `S" <text>" PRINT`      | `print "<text>"`           | Quotes preserved. |
| `<linkname> PRINT`      | `print <linkname>`         | UserVariable lift. After Mode A sidecar substitution, `<linkname>` becomes the in-game name. |
| `@<id> PRINT`           | `print @<id>`              | @-identifier lift (magic var, content, sensor prop). |
| `S" <name>" PRINTFLUSH` | `printflush <name>`        | Quotes stripped — block handles are bare identifiers in mlog. |
| `<linkname> PRINTFLUSH` | `printflush <linkname>`    | UserVariable lift. Sidecar substitution rewrites the operand. |
| `@<id> PRINTFLUSH`      | `printflush @<id>`         | @-identifier lift. |
| `S" <block>" S" <prop>" SENSOR` | `sensor s<i> <block> <prop>` | Both literals lift. |
| `<linkname> S" <prop>" SENSOR`  | `sensor s<i> <linkname> <prop>` | Block from a UserVariable. |
| `<linkname> @<prop> SENSOR`     | `sensor s<i> <linkname> @<prop>` | Block from a UserVariable, prop from an @-identifier. |
| `@<block> @<prop> SENSOR`       | `sensor s<i> @<block> @<prop>` | Both @-identifiers. |
| `S" <block>" @<prop> SENSOR`    | `sensor s<i> <block> @<prop>` | LitStr block + @-id prop. |
| `<any> S" <prop>" SENSOR` (tail) | `sensor s<i> s<j> <prop>` | Prop-only lift; block came from an earlier-emitted term. |
| `<any> @<prop> SENSOR` (tail)    | `sensor s<i> s<j> @<prop>` | Prop-only lift; block came from an earlier-emitted term. |

For the catalogue of `@-identifiers` recognized by the resolver, see
the dictionary reference page.

## CONTROL-\* block-instructions

mlog's `control` instruction is always 5 operands after the
sub-command. Unused tail slots are padded with `0`. The two most
common shapes (`CONTROL-ENABLED`, `CONTROL-CONFIG`) have lifted forms
when the block is a UserVariable/LitStr/@-identifier *and* the value
is a LitInt/LitStr/@-identifier.

| Forth | mlog | Notes |
|-------|------|-------|
| `<block> <flag> CONTROL-ENABLED` (lifted)  | `control enabled <block> <flag> 0 0 0`   | 2 stack ops + 3 zero-pads. |
| `<block> <value> CONTROL-CONFIG` (lifted)  | `control config <block> <value> 0 0 0`   | 2 stack ops + 3 zero-pads. |
| `CONTROL-ENABLED` (slot-form fallback)     | `control enabled s<i-1> s<i> 0 0 0`      | When operands didn't match a lift window. |
| `CONTROL-CONFIG` (slot-form fallback)      | `control config s<i-1> s<i> 0 0 0`       | |
| `CONTROL-SHOOT`                            | `control shoot s<i-3> s<i-2> s<i-1> s<i> 0` | (4, 0). Block + x + y + shoot-flag. 1 zero-pad. |
| `CONTROL-SHOOTP`                           | `control shootp s<i-2> s<i-1> s<i> 0 0`  | (3, 0). Block + unit + shoot-flag. 2 zero-pads. |
| `CONTROL-COLOR`                            | `control color s<i-3> s<i-2> s<i-1> s<i> 0` | (4, 0). Block + r + g + b. 1 zero-pad. |

## User-defined word calls

A `: name <body> ;` definition emits *nothing* at its declaration
site. Each call site inlines the body with every internal `s<k>`
rewritten to `s<k + caller_base>`. Named variables (`foo`,
`__swap_tmp`, `__do_idx_<N>`) pass through unchanged — they live in
mlog's flat global namespace.

This is the v1 strategy. Inlining always wins on speed; the
`@counter`-trick subroutine emission is held in reserve as a v2
size-only fallback (see "Forward pointer" below).

## Finalize-pass transforms

The emit pass produces a flat `list[MlogInstr]` of
`(label, opcode, operands)` tuples. The finalize pass
(`finalize.py`) chains four sub-passes whose order is load-bearing.

1. **Sidecar substitution** (`substitute_sidecar`). Walks the
   instruction list and rewrites operand tokens for `printflush`
   (position 0) and `sensor` (position 1) when they resolve to a
   Mode A (`target = "<in-game-name>"`) link. Two shapes are
   recognized:
   - **Bare-name** (the literal-lift produced `printflush display`):
     operand replaced in place with the in-game name.
   - **Slot-reference** (`set s<i> "display"` followed by
     `printflush s<i>`): consumer's operand replaced with the in-game
     name and the staging `set` elided.

2. **Prologue emission** (`emit_prologue`). For each Mode B
   (`index = N`) link in the sidecar, prepend a `getlink <mforth-name>
   <N>` instruction. Must run before label resolution so label
   line-numbers account for the prologue offset.

3. **Label resolution** (`resolve_labels`). Two-pass walk: build a
   `label → 0-indexed line number` map (sentinel tuples consume zero
   lines), then rewrite every `jump` operand-0 from the symbolic
   label to its line number as a string. Sentinels are stripped on
   the way out.

4. **Writer** (`write_mlog`). Emits a `# mforth output — <count>
   instructions; SOURCE=<path>; SIDECAR=<path>` header line, then one
   `opcode op1 op2 ...` per instruction, with exactly one trailing
   newline at EOF. Raises `ValueError` if any unresolved label leaks
   to this layer.

## Forward pointer — v2 subroutine emission

v1 inlines every user-defined word. v2 will add a `-Osize` opt-in
(and an automatic fallback when inlining exceeds the per-processor
instruction budget) that emits user definitions as mlog subroutines
using the writable `@counter` trick — the caller saves the return
address, sets `@counter` to the entry label, and the callee ends with
`set @counter <return-addr-var>`. The same lever enables jump-table
dispatch via `op add @counter @counter <offset>`. The v2 optimization
roadmap (beads `mforth-10t.33` through `mforth-10t.40`) covers the
peephole, common-subexpression, and dead-code passes that will sit
alongside subroutine emission. None of those ship in v1; nothing on
this page is affected by them today.
