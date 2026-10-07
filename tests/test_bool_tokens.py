"""Tests for the single shared bool-token table.

Every reader of the truthy/falsy word sets must agree: ``logging``'s falsy
set needs "n"/"f" so ``DUHO_TRACEBACK=n`` is OFF like ``AGENT_HELP=n``, and
``Env.bool`` strips whitespace like the layered (env/config) converter. All
read from ``duho.text.BOOL_TRUE``/``BOOL_FALSE``.

All classes are declared at module level in this real ``.py`` file so their
AST-derived flags/env resolve normally.
"""

import pytest

from duho import Arg, Args, Meta, _compat
from duho.env import Env
from duho.logging import traceback_enabled


@pytest.mark.parametrize("false_token", sorted(_compat.BOOL_FALSE - {""}))
def test_traceback_enabled_respects_shared_falsey_set(monkeypatch, false_token):
    # logging's falsy set must include "n"/"f": DUHO_TRACEBACK=n must turn
    # tracebacks OFF, not ON.
    monkeypatch.setenv("DUHO_TRACEBACK", false_token)
    assert traceback_enabled() is False


@pytest.mark.parametrize("true_token", sorted(_compat.BOOL_TRUE))
def test_traceback_enabled_respects_shared_truthy_set(monkeypatch, true_token):
    monkeypatch.setenv("DUHO_TRACEBACK", true_token)
    assert traceback_enabled() is True


class _EnvBoolArgs(Args):
    """A bool field layered from the same env var `Env.bool` reads."""

    debug: "Arg[bool, Meta(env='WSAPP_DEBUG')]" = False
    ("--debug",)


def test_env_bool_strips_whitespace_like_the_layered_converter(monkeypatch):
    # The classic cmd.exe `set VAR=1 && ...` trailing-space pitfall: a
    # layered Meta(env=...) bool field already saw "1 " as True (it strips);
    # Env.bool disagreed by not stripping, and read the same text as False.
    monkeypatch.setenv("WSAPP_DEBUG", "1 ")
    import duho

    inst = duho.parse(_EnvBoolArgs, [])
    assert inst.debug is True
    assert Env("wsapp", autoload=False).bool("DEBUG") is True


# --- a bool field set True by a layer can be turned back off --------------


def test_env_layered_bool_can_be_turned_off_from_cli(monkeypatch):
    """`_EnvBoolArgs.debug` defaults False but reads an env var -- when that
    env var sets it True, `store_true` (chosen only from the DECLARED False
    default) has no `--no-debug` to reach back to False from the CLI. A field
    that CAN receive a layered value now gets `BooleanOptionalAction`
    instead."""
    import duho

    monkeypatch.setenv("WSAPP_DEBUG", "1")
    assert duho.parse(_EnvBoolArgs, []).debug is True
    assert duho.parse(_EnvBoolArgs, ["--no-debug"]).debug is False


class _ConfigBoolArgs(Args):
    """A bool field on a class with a config source -- config can ALSO set
    it True with no CLI way back to False, for the same reason. The
    static `_config_` declaration is what `layered` keys off; the actual
    file loaded per-call is overridden via the `config=` kwarg below."""

    _config_ = "unused-default.toml"

    dry_run: bool = False
    ("--dry-run",)


@pytest.mark.requires_toml
def test_config_layered_bool_can_be_turned_off_from_cli(tmp_path):
    import duho

    cfg = tmp_path / "cfg.toml"
    cfg.write_text("dry_run = true\n")
    assert duho.parse(_ConfigBoolArgs, [], config=cfg).dry_run is True
    assert duho.parse(_ConfigBoolArgs, ["--no-dry-run"], config=cfg).dry_run is False


def test_plain_bool_without_layer_still_uses_store_true():
    """A bool field with no env= and no config source keeps the plain
    store_true flag (no `--no-*` pair) -- the layered upgrade must not apply
    universally (would clutter --help for the common case)."""

    class PlainBoolArgs(Args):
        verbose: bool = False
        ("--verbose",)

    parser = PlainBoolArgs._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["--no-verbose"])
