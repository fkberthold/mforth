"""mforth dictionary + word-resolution pass.

The dictionary maps word names (case-insensitive) to one of three entry
kinds:

* `BuiltinWord` — language primitives (stack ops, arithmetic, comparison,
  logical, IO, memory) and Mindustry primitives (PRINT, PRINTFLUSH, WAIT,
  SENSOR, GETLINK). Built-ins carry a stack effect (arity-in, arity-out),
  a one-line doc string for LSP hover, and a tag classifying the family.
* `Definition` — user `: name ... ;` definitions, registered from the
  parser's AST. Forth semantics: a later definition replaces an earlier
  one of the same name.
* `UserVariable` — names introduced via `VARIABLE <name>`. Pre-scanned
  before resolution so forward references work.

`resolve(program)` walks the AST once, asserts every `WordCall.name` can
be looked up, and returns the populated dictionary. Unresolved names
raise `UnresolvedWordError(name, src_loc)` so the LSP and CLI can point
the user at the exact source location.

Out of scope for this bead: stack-effect inference for user definitions
(bead mforth-10t.7), and synthesising `VarRef` nodes from VARIABLE/@/!
patterns (deferred — the parser's `VarRef` export is reserved for that
work).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

from mforth.parse import (
    Begin,
    Definition,
    DoLoop,
    IfThen,
    LitInt,
    LitStr,
    Program,
    SrcLoc,
    WordCall,
)


# ---------------------------------------------------------------------------
# Entry types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StackEffect:
    in_arity: int
    out_arity: int


@dataclass(frozen=True)
class BuiltinWord:
    name: str
    stack_effect: StackEffect
    doc: str
    tag: str  # 'arith' | 'stack' | 'control' | 'mindustry' | 'var' | 'io'


@dataclass(frozen=True)
class UserVariable:
    name: str
    src_loc: SrcLoc
    tag: str = "var"


DictEntry = Union[BuiltinWord, Definition, UserVariable]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class UnresolvedWordError(Exception):
    """Raised when a WordCall references a name not in the dictionary."""

    def __init__(self, name: str, src_loc: SrcLoc) -> None:
        super().__init__(
            f"{src_loc.file}:{src_loc.line}:{src_loc.col}: unresolved word '{name}'"
        )
        self.name = name
        self.src_loc = src_loc


# ---------------------------------------------------------------------------
# Dictionary
# ---------------------------------------------------------------------------


class Dictionary:
    """Case-insensitive mapping from word name → dictionary entry."""

    def __init__(self) -> None:
        self._entries: dict[str, DictEntry] = {}

    def add_builtin(self, word: BuiltinWord) -> None:
        self._entries[word.name.lower()] = word

    def add_definition(self, defn: Definition) -> None:
        self._entries[defn.name.lower()] = defn

    def add_variable(self, var: UserVariable) -> None:
        self._entries[var.name.lower()] = var

    def lookup(self, name: str) -> Optional[DictEntry]:
        return self._entries.get(name.lower())

    def __contains__(self, name: str) -> bool:
        return name.lower() in self._entries

    def __len__(self) -> int:
        return len(self._entries)


# ---------------------------------------------------------------------------
# Built-in registry
# ---------------------------------------------------------------------------


_BUILTINS: list[BuiltinWord] = [
    # Stack ops
    BuiltinWord("DUP", StackEffect(1, 2), "duplicate top of stack", "stack"),
    BuiltinWord("DROP", StackEffect(1, 0), "discard top of stack", "stack"),
    BuiltinWord("SWAP", StackEffect(2, 2), "swap top two items", "stack"),
    BuiltinWord("OVER", StackEffect(2, 3), "copy second item to top", "stack"),
    BuiltinWord("ROT", StackEffect(3, 3), "rotate top three: a b c -> b c a", "stack"),
    BuiltinWord("NIP", StackEffect(2, 1), "remove second item", "stack"),
    BuiltinWord("TUCK", StackEffect(2, 3), "copy top under second: a b -> b a b", "stack"),
    # Arithmetic
    BuiltinWord("+", StackEffect(2, 1), "add", "arith"),
    BuiltinWord("-", StackEffect(2, 1), "subtract", "arith"),
    BuiltinWord("*", StackEffect(2, 1), "multiply", "arith"),
    BuiltinWord("/", StackEffect(2, 1), "divide", "arith"),
    BuiltinWord("MOD", StackEffect(2, 1), "modulo", "arith"),
    # Comparison
    BuiltinWord("=", StackEffect(2, 1), "equal", "arith"),
    BuiltinWord("<>", StackEffect(2, 1), "not equal", "arith"),
    BuiltinWord("<", StackEffect(2, 1), "less than", "arith"),
    BuiltinWord(">", StackEffect(2, 1), "greater than", "arith"),
    BuiltinWord("<=", StackEffect(2, 1), "less than or equal", "arith"),
    BuiltinWord(">=", StackEffect(2, 1), "greater than or equal", "arith"),
    # Logical
    BuiltinWord("AND", StackEffect(2, 1), "logical and", "arith"),
    BuiltinWord("OR", StackEffect(2, 1), "logical or", "arith"),
    BuiltinWord("NOT", StackEffect(1, 1), "logical not", "arith"),
    # IO
    BuiltinWord(".", StackEffect(1, 0), "print top of stack", "io"),
    # Memory / variables
    BuiltinWord("@", StackEffect(1, 1), "fetch variable value: ( addr -- value )", "var"),
    BuiltinWord("!", StackEffect(2, 0), "store value into variable: ( value addr -- )", "var"),
    BuiltinWord(
        "VARIABLE",
        StackEffect(0, 0),
        "declare a variable: VARIABLE <name>",
        "var",
    ),
    # Mindustry primitives
    BuiltinWord("PRINT", StackEffect(1, 0), "queue value to print buffer", "mindustry"),
    BuiltinWord(
        "PRINTFLUSH",
        StackEffect(1, 0),
        "flush print buffer to a message block: ( block -- )",
        "mindustry",
    ),
    BuiltinWord("WAIT", StackEffect(1, 0), "pause execution for N seconds", "mindustry"),
    BuiltinWord(
        "SENSOR",
        StackEffect(2, 1),
        "read block property: ( block prop -- value )",
        "mindustry",
    ),
    BuiltinWord(
        "GETLINK",
        StackEffect(1, 1),
        "retrieve i-th linked block: ( i -- block )",
        "mindustry",
    ),
]


def standard_dictionary() -> Dictionary:
    """Build a fresh `Dictionary` populated with all v1 built-ins."""
    d = Dictionary()
    for w in _BUILTINS:
        d.add_builtin(w)
    return d


# ---------------------------------------------------------------------------
# Resolution pass
# ---------------------------------------------------------------------------


def _walk_terms(terms: list, visitor) -> None:
    for t in terms:
        visitor(t)
        if isinstance(t, IfThen):
            _walk_terms(t.then_body, visitor)
            _walk_terms(t.else_body, visitor)
        elif isinstance(t, Begin):
            _walk_terms(t.body, visitor)
            _walk_terms(t.cond_body, visitor)
        elif isinstance(t, DoLoop):
            _walk_terms(t.body, visitor)


def _collect_variable_declarations(program: Program) -> list[UserVariable]:
    """Pre-scan: every `WordCall("VARIABLE")` followed by a `WordCall(<name>)`
    declares <name>. Walks main and all definition bodies (Forth has no
    lexical scoping, so a VARIABLE inside a definition is still global).
    """
    declarations: list[UserVariable] = []

    def scan_list(terms: list) -> None:
        i = 0
        while i < len(terms):
            t = terms[i]
            if (
                isinstance(t, WordCall)
                and t.name.upper() == "VARIABLE"
                and i + 1 < len(terms)
                and isinstance(terms[i + 1], WordCall)
            ):
                nxt = terms[i + 1]
                declarations.append(UserVariable(name=nxt.name, src_loc=nxt.src_loc))
            # Recurse into nested control-flow bodies
            if isinstance(t, IfThen):
                scan_list(t.then_body)
                scan_list(t.else_body)
            elif isinstance(t, Begin):
                scan_list(t.body)
                scan_list(t.cond_body)
            elif isinstance(t, DoLoop):
                scan_list(t.body)
            i += 1

    scan_list(program.main)
    for d in program.definitions:
        scan_list(d.body)
    return declarations


def resolve(program: Program, dictionary: Optional[Dictionary] = None) -> Dictionary:
    """Resolve every `WordCall.name` in `program`.

    * Adds the program's `Definition`s to the dictionary (in source order;
      a later `:` redefines an earlier one of the same name).
    * Adds VARIABLE-declared names as `UserVariable` entries.
    * Walks every `WordCall` (in main and definition bodies, including
      nested control-flow) and verifies each name is in the dictionary.

    Raises `UnresolvedWordError` on the first miss. The AST itself is not
    modified. Returns the (now-populated) dictionary so callers can pass
    it to the next stage.
    """
    d = dictionary if dictionary is not None else standard_dictionary()

    for defn in program.definitions:
        d.add_definition(defn)

    for var in _collect_variable_declarations(program):
        d.add_variable(var)

    def check(t) -> None:
        if isinstance(t, WordCall) and t.name not in d:
            raise UnresolvedWordError(t.name, t.src_loc)

    _walk_terms(program.main, check)
    for defn in program.definitions:
        _walk_terms(defn.body, check)

    return d


__all__ = [
    "BuiltinWord",
    "Dictionary",
    "StackEffect",
    "UnresolvedWordError",
    "UserVariable",
    "resolve",
    "standard_dictionary",
]
