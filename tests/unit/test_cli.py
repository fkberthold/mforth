"""Unit tests for the mforth CLI scaffold.

Bead mforth-326. Contract pinned here (M3 RED contract for this bead):

* ``mforth.cli.main()`` is the entry point referenced by the
  ``[project.scripts]`` table (``mforth = "mforth.cli:main"``) and by
  ``src/mforth/__main__.py`` (so ``python -m mforth`` works).
* The CLI is built on stdlib ``argparse`` — no new runtime deps.
* Subcommands register themselves via a module-level registry function
  ``register_subcommand(name, help, configure_parser, handler)``. The
  ``version`` subcommand is itself wired through this function — no
  special-casing — proving the pattern future subcommand beads (.20
  viz, .23 LSP, run, compile, repl) will follow.
* Inside ``main()``: ``_load_subcommands()`` runs first so the registry
  is populated before argparse is built. Top-level parser iterates the
  registry to add subparsers, parses args, dispatches the handler.
* ``mforth version`` prints ``mforth <package-version>`` and exits 0.
  Version comes from ``importlib.metadata.version("mforth")`` with a
  fallback to ``mforth.__version__`` if the metadata lookup fails (which
  happens in some editable-install configurations).
* ``mforth --help`` lists every registered subcommand (currently just
  ``version``). New subcommand beads land by adding a
  ``register_subcommand`` call inside ``_load_subcommands`` plus a
  module to import — no edits to ``main()`` proper.

Out of scope for this bead — and therefore for these tests:

* ``run``, ``compile``, ``repl``, ``lsp``, ``--serve`` subcommands.
  Those land in their own beads (mforth-10t.{13, 14, 19, 20, 23}).
* End-to-end shell-out via the installed ``mforth`` console script.
  That's covered by the post-merge manual smoke (``pip install -e .``
  then ``mforth --help`` / ``mforth version``); pytest tests drive
  ``mforth.cli.main`` directly so the suite stays hermetic.

Negative cases (M5):

* Unknown subcommand → argparse usage error, exit code 2.
* No subcommand → help is printed and exit code is non-zero (we
  pin exit code 2, which is argparse's "no required arg" default).
* The registry is a true registry, not a static if/elif chain —
  pinned by ``test_register_subcommand_adds_to_registry``.
* Version handler does not special-case the registry — pinned by
  ``test_version_subcommand_is_registered_via_register_subcommand``.

Bead mforth-d1o adds a test class pinning that the ``lsp`` and ``repl``
subcommands converge on the .14 explicit ``register()`` pattern (a
module-level ``register()`` callable invoked by ``_load_subcommands``),
which survives pytest's registry-clear-and-replay isolation — the
brittle import-side-effect registration did not.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr, redirect_stdout

import pytest

import mforth
from mforth import cli


# --------------------------------------------------------------------------
# Registry contract
# --------------------------------------------------------------------------


def test_register_subcommand_callable_exists():
    """``cli.register_subcommand`` is the public registry entry point.

    Future subcommand beads (.20 viz, .23 LSP, run, compile, repl)
    import this and call it at module import time. Renaming or removing
    it is a breaking change for every downstream subcommand bead.
    """
    assert callable(cli.register_subcommand)


def test_register_subcommand_adds_to_registry():
    """``register_subcommand`` mutates the module-level registry.

    Pins the registry-pattern choice over a static if/elif dispatcher.
    Reset state after the test so other tests see a clean registry.
    """
    saved = dict(cli._REGISTRY)
    try:
        cli._REGISTRY.clear()

        def _noop_configure(parser):
            pass

        def _noop_handler(args):
            return 0

        cli.register_subcommand(
            "smoke",
            help="smoke subcommand for the registry test",
            configure_parser=_noop_configure,
            handler=_noop_handler,
        )
        assert "smoke" in cli._REGISTRY
        entry = cli._REGISTRY["smoke"]
        assert entry.help == "smoke subcommand for the registry test"
        assert entry.configure_parser is _noop_configure
        assert entry.handler is _noop_handler
    finally:
        cli._REGISTRY.clear()
        cli._REGISTRY.update(saved)


def test_register_subcommand_rejects_duplicate_name():
    """Re-registering the same name is a programmer error.

    Two subcommand beads claiming the same name silently overwriting
    each other would be the worst-case parallel-bead bug — surface it
    at registration time.
    """
    saved = dict(cli._REGISTRY)
    try:
        cli._REGISTRY.clear()
        cli.register_subcommand(
            "smoke",
            help="first",
            configure_parser=lambda p: None,
            handler=lambda a: 0,
        )
        with pytest.raises(ValueError, match="smoke"):
            cli.register_subcommand(
                "smoke",
                help="second",
                configure_parser=lambda p: None,
                handler=lambda a: 0,
            )
    finally:
        cli._REGISTRY.clear()
        cli._REGISTRY.update(saved)


# --------------------------------------------------------------------------
# version subcommand
# --------------------------------------------------------------------------


def test_version_subcommand_prints_package_version(capsys):
    """``mforth version`` prints ``mforth <version>`` to stdout, exits 0."""
    rc = cli.main(["version"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.strip() == f"mforth {mforth.__version__}"


def test_version_subcommand_is_registered_via_register_subcommand():
    """The version subcommand is itself wired through ``register_subcommand``.

    Load-bearing: if the version handler were special-cased inside
    ``main()``, the registry pattern wouldn't actually be exercised by
    the v1 ship, and future subcommand beads could land a broken
    integration without anyone noticing. Calling ``_load_subcommands``
    must populate the registry with ``version``.
    """
    saved = dict(cli._REGISTRY)
    try:
        cli._REGISTRY.clear()
        cli._load_subcommands()
        assert "version" in cli._REGISTRY
    finally:
        cli._REGISTRY.clear()
        cli._REGISTRY.update(saved)


def test_version_handler_uses_importlib_metadata_when_available(
    monkeypatch, capsys
):
    """When ``importlib.metadata.version("mforth")`` succeeds, use its value.

    The fallback to ``mforth.__version__`` exists only for editable
    installs that lose dist-info — when metadata works, prefer it
    because it's the single source of truth for installed packages.
    """
    monkeypatch.setattr(cli, "_get_version", lambda: "9.9.9-from-metadata")
    rc = cli.main(["version"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.strip() == "mforth 9.9.9-from-metadata"


def test_version_handler_falls_back_when_metadata_missing(
    monkeypatch, capsys
):
    """``importlib.metadata.PackageNotFoundError`` → fall back to ``__version__``.

    Reproduces the editable-install case where dist-info is absent. The
    fallback path must produce a usable version string, not raise.
    """
    from importlib import metadata as _metadata

    def _raise(name):
        raise _metadata.PackageNotFoundError(name)

    monkeypatch.setattr(_metadata, "version", _raise)
    rc = cli.main(["version"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.strip() == f"mforth {mforth.__version__}"


# --------------------------------------------------------------------------
# Subcommand registration converges on the explicit register() pattern
# (bead mforth-d1o)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("subcommand", ["lsp", "repl"])
def test_subcommand_module_exposes_register_callable(subcommand):
    """``lsp`` and ``repl`` follow the .14 explicit ``register()`` pattern.

    Bead mforth-d1o. The canonical pattern (cli_run.py / cli_compile.py)
    exposes a module-level ``register()`` callable that
    ``_load_subcommands`` invokes after import — NOT a module-import
    side-effect guarded by ``if name not in _REGISTRY``. The import
    side-effect is brittle: once the module is cached in ``sys.modules``,
    re-importing it (after a registry clear) is a no-op, so the guard
    never re-runs and the subcommand silently vanishes from the registry.
    """
    import importlib

    module = importlib.import_module(f"mforth.{subcommand}.cli_subcommand")
    assert hasattr(module, "register"), (
        f"{subcommand} subcommand module must expose a register() callable "
        "(the .14 explicit-register() pattern mirrored from cli_run.py)"
    )
    assert callable(module.register)


@pytest.mark.parametrize("subcommand", ["lsp", "repl"])
def test_subcommand_register_survives_registry_clear_and_replay(subcommand):
    """``lsp`` / ``repl`` re-register after a registry clear + replay.

    Bead mforth-d1o. This is the exact test-isolation failure the bead
    fixes: the subcommand modules are already cached in ``sys.modules``
    (every prior test imported them via ``_load_subcommands``), then a
    fixture clears the registry and re-runs ``_load_subcommands``. With
    the brittle import-side-effect pattern the cached import is a no-op
    and the subcommand never comes back. With the explicit ``register()``
    call from ``_load_subcommands`` it does.
    """
    # Ensure the module is cached in sys.modules (the realistic state
    # after any prior test exercised the CLI).
    import importlib

    importlib.import_module(f"mforth.{subcommand}.cli_subcommand")

    saved = dict(cli._REGISTRY)
    try:
        cli._REGISTRY.clear()
        cli._load_subcommands()
        assert subcommand in cli._REGISTRY, (
            f"{subcommand!r} missing from the registry after a clear + "
            "_load_subcommands() replay — the brittle import-side-effect "
            "registration pattern does not survive test isolation"
        )
        # And the subcommand is invocable: the parser builds with it.
        parser = cli._build_parser()
        with pytest.raises(SystemExit) as excinfo:
            parser.parse_args([subcommand, "--help"])
        assert excinfo.value.code == 0
    finally:
        cli._REGISTRY.clear()
        cli._REGISTRY.update(saved)


@pytest.mark.parametrize("subcommand", ["lsp", "repl"])
def test_subcommand_register_is_idempotent(subcommand):
    """Calling ``register()`` twice does not raise the duplicate ValueError.

    Bead mforth-d1o. The .14 pattern guards ``register()`` with an
    ``if name in _REGISTRY: return`` early-out so a second call (e.g. an
    auto-register-on-import plus a ``_load_subcommands`` call) is a no-op
    rather than a duplicate-name ``ValueError``.
    """
    import importlib

    module = importlib.import_module(f"mforth.{subcommand}.cli_subcommand")

    saved = dict(cli._REGISTRY)
    try:
        cli._REGISTRY.clear()
        module.register()
        assert subcommand in cli._REGISTRY
        # Second call must be a silent no-op, not a ValueError.
        module.register()
        assert subcommand in cli._REGISTRY
    finally:
        cli._REGISTRY.clear()
        cli._REGISTRY.update(saved)


# --------------------------------------------------------------------------
# Top-level parser
# --------------------------------------------------------------------------


def test_help_lists_version_subcommand(capsys):
    """``mforth --help`` exits 0 and mentions the ``version`` subcommand."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "version" in captured.out


def test_no_subcommand_prints_help_and_exits_nonzero(capsys):
    """Running ``mforth`` with no subcommand is a usage error.

    argparse's default behaviour for a missing required subcommand is
    exit code 2. We pin that — future subcommands shouldn't accidentally
    flip it to "default to repl" without an explicit decision.
    """
    with pytest.raises(SystemExit) as excinfo:
        cli.main([])
    assert excinfo.value.code == 2


def test_unknown_subcommand_is_a_usage_error(capsys):
    """``mforth nosuchthing`` → argparse usage error, exit code 2."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["nosuchthing"])
    assert excinfo.value.code == 2


# --------------------------------------------------------------------------
# python -m mforth entry point
# --------------------------------------------------------------------------


def test_dunder_main_module_delegates_to_cli_main():
    """``src/mforth/__main__.py`` imports and calls ``cli.main``.

    Pinned by reading the module source — running ``python -m mforth``
    in-process is awkward in pytest (it re-enters sys.argv handling).
    The source-level check is sufficient: if ``__main__.py`` ever
    diverges from "call cli.main", this test catches it.
    """
    import mforth.__main__ as dunder_main

    src = dunder_main.__file__
    assert src and src.endswith("__main__.py")
    with open(src, encoding="utf-8") as fh:
        body = fh.read()
    assert "from mforth.cli import main" in body or (
        "from mforth import cli" in body and "cli.main(" in body
    )


def test_main_accepts_argv_parameter():
    """``cli.main(argv)`` accepts an explicit argv for testing.

    Default ``None`` falls back to ``sys.argv[1:]`` (standard argparse
    convention). Pinning the signature so test harnesses keep working.
    """
    import inspect

    sig = inspect.signature(cli.main)
    params = list(sig.parameters.values())
    assert len(params) == 1
    assert params[0].name == "argv"
    assert params[0].default is None
