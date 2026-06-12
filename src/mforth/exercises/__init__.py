"""Bundled exercise specs for the "Learn Forth with mforth" tutorial
(bead mforth-roz.1).

This package is the *data substrate* the ``mforth check`` subcommand
(see :mod:`mforth.cli_check`) runs against. Each exercise is a pair of
files shipped as **package data** under ``<track>/<id-basename>``:

* ``<name>.spec.toml`` — the exercise definition (schema below).
* ``<name>.solution.fs`` — the reference answer, carrying the
  ``\\ @exercise <track>/<name>`` metadata marker.

Why package data (importlib.resources) and not on-disk paths
============================================================

A learner runs ``mforth check my-answer.fs`` from *their* directory,
which has none of the project's files. The checker must still find the
bundled spec. :func:`importlib.resources.files` resolves resources
relative to the installed package regardless of the process cwd (and
works for both editable installs and a built wheel), so the loader never
touches ``__file__``-relative paths.

The hatchling wheel target (``packages = ["src/mforth"]`` in
``pyproject.toml``) ships every non-``.py`` file inside the package tree
by default; a ``[tool.hatch.build.targets.wheel.force-include]`` /
``artifacts`` guard makes the ``*.spec.toml`` + ``*.solution.fs``
inclusion explicit so a future ``.gitignore`` / build-config change
can't silently drop them from the wheel.

Spec schema (TOML)
==================

::

    id      = "forth-101/02-nip"   # "<track>/<name>" — matches dir+filename
    prompt  = "Define `nip` ..."    # what the learner implements
    hint    = "SWAP brings ..."     # shown on failure
    sidecar = '''                   # OPTIONAL inline .world.toml text
    [links.display]                 # for simulator exercises (Part II);
    type = "message"                # abstract Part-I exercises omit it.
    target = "message1"
    '''
    [[case]]                        # >= 1 case
    driver = "1 2 nip ."            # Forth appended AFTER the learner code
    expect = ["2"]                  # ordered list of expected printed strings

A *case* runs ``<learner code>\\n<driver>`` through the host
:class:`~mforth.backend.runner.Runner` against a ``MockWorld`` and
compares the sequence of printed strings (``MessagePrintEvent.text``, as
produced by ``.`` and ``PRINT``) to ``expect``.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Optional

__all__ = [
    "Case",
    "ExerciseSpec",
    "UnknownExerciseError",
    "SpecError",
    "list_ids",
    "load_spec",
    "load_solution_text",
    "has_solution",
]

_SPEC_SUFFIX = ".spec.toml"
_SOLUTION_SUFFIX = ".solution.fs"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SpecError(Exception):
    """Raised when a bundled spec is malformed (missing required key,
    bad case shape, etc.). A packaging-time / authoring error — never
    something a learner can trigger."""


class UnknownExerciseError(Exception):
    """Raised when an exercise id does not resolve to a bundled spec.

    Carries the offending id so the CLI surface can echo it back to the
    learner (e.g. ``mforth check --solution forth-101/typo``).
    """

    def __init__(self, exercise_id: str) -> None:
        super().__init__(f"unknown exercise id: {exercise_id!r}")
        self.exercise_id = exercise_id


# ---------------------------------------------------------------------------
# Schema dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """One driver/expect pair within an exercise spec."""

    driver: str
    expect: list[str]


@dataclass(frozen=True)
class ExerciseSpec:
    """A parsed exercise spec (one ``<id>.spec.toml``)."""

    id: str
    prompt: str
    hint: str
    cases: list[Case]
    sidecar: Optional[str] = None
    track: str = field(default="", repr=False)
    name: str = field(default="", repr=False)


# ---------------------------------------------------------------------------
# Resource traversal
# ---------------------------------------------------------------------------


def _root() -> Traversable:
    """Return the package-data root (``mforth.exercises``)."""
    return resources.files(__package__)


def _id_to_parts(exercise_id: str) -> tuple[str, str]:
    """Split ``"<track>/<name>"`` into ``(track, name)``.

    The id must contain exactly one ``/`` — ``<track>`` is the
    sub-directory, ``<name>`` is the file basename (sans suffix). A bare
    id with no slash is treated as ``track=""`` so a future flat layout
    still resolves.
    """
    if "/" in exercise_id:
        track, name = exercise_id.rsplit("/", 1)
    else:
        track, name = "", exercise_id
    return track, name


def _spec_resource(exercise_id: str) -> Traversable:
    track, name = _id_to_parts(exercise_id)
    node = _root()
    if track:
        node = node / track
    return node / f"{name}{_SPEC_SUFFIX}"


def _solution_resource(exercise_id: str) -> Traversable:
    track, name = _id_to_parts(exercise_id)
    node = _root()
    if track:
        node = node / track
    return node / f"{name}{_SOLUTION_SUFFIX}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_ids() -> list[str]:
    """Return every bundled exercise id, sorted.

    Walks each ``<track>/`` sub-package one level deep and reports
    ``"<track>/<name>"`` for every ``<name>.spec.toml`` found. The
    top-level package itself (``__init__.py``, ``__pycache__``) is
    skipped; only directory children are treated as tracks.
    """
    ids: list[str] = []
    root = _root()
    for track_node in root.iterdir():
        # ``is_dir`` is part of the Traversable protocol for both the
        # filesystem and zipfile backends.
        if not track_node.is_dir():
            continue
        track = track_node.name
        if track.startswith("__"):  # __pycache__ etc.
            continue
        for child in track_node.iterdir():
            cname = child.name
            if cname.endswith(_SPEC_SUFFIX):
                base = cname[: -len(_SPEC_SUFFIX)]
                ids.append(f"{track}/{base}")
    return sorted(ids)


def _parse_spec_text(text: str, exercise_id: str) -> ExerciseSpec:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:  # pragma: no cover - authoring error
        raise SpecError(f"{exercise_id}: malformed spec TOML: {e}") from e

    for key in ("id", "prompt", "hint"):
        if key not in data:
            raise SpecError(f"{exercise_id}: spec missing required key {key!r}")

    raw_cases = data.get("case", [])
    if not raw_cases:
        raise SpecError(f"{exercise_id}: spec declares no [[case]] entries")

    cases: list[Case] = []
    for i, rc in enumerate(raw_cases):
        if "driver" not in rc or "expect" not in rc:
            raise SpecError(
                f"{exercise_id}: case {i} missing 'driver' or 'expect'"
            )
        expect = rc["expect"]
        if not isinstance(expect, list):
            raise SpecError(
                f"{exercise_id}: case {i} 'expect' must be a list of strings"
            )
        cases.append(
            Case(driver=str(rc["driver"]), expect=[str(x) for x in expect])
        )

    track, name = _id_to_parts(str(data["id"]))
    return ExerciseSpec(
        id=str(data["id"]),
        prompt=str(data["prompt"]),
        hint=str(data["hint"]),
        cases=cases,
        sidecar=(str(data["sidecar"]) if data.get("sidecar") is not None else None),
        track=track,
        name=name,
    )


def load_spec(exercise_id: str) -> ExerciseSpec:
    """Load + parse the bundled spec for ``exercise_id``.

    Raises
    ------
    UnknownExerciseError
        If no ``<id>.spec.toml`` is bundled for the id.
    SpecError
        If the spec exists but is malformed.
    """
    resource = _spec_resource(exercise_id)
    if not resource.is_file():
        raise UnknownExerciseError(exercise_id)
    text = resource.read_text(encoding="utf-8")
    return _parse_spec_text(text, exercise_id)


def has_solution(exercise_id: str) -> bool:
    """Return True if a reference ``<id>.solution.fs`` is bundled."""
    return _solution_resource(exercise_id).is_file()


def load_solution_text(exercise_id: str) -> str:
    """Return the bundled reference ``.solution.fs`` text for ``exercise_id``.

    Raises
    ------
    UnknownExerciseError
        If no reference solution is bundled for the id.
    """
    resource = _solution_resource(exercise_id)
    if not resource.is_file():
        raise UnknownExerciseError(exercise_id)
    return resource.read_text(encoding="utf-8")
