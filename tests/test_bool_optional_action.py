"""Regression tests for BooleanOptionalAction compatibility across Python
versions.

A `bool` field that defaults to `True` becomes `argparse.BooleanOptionalAction`
(`--flag`/`--no-flag`). Two version-specific argparse behaviors around that
action needed handling:

* Python 3.14 removed the (already-deprecated) `type`/`choices`/`metavar`
  parameters outright, and rejects any option string starting with `--no-`
  -- a bare `bool = True` field whose auto-derived flag already starts
  with `--no-` (or one given an explicit `metavar=`) crashed parser
  construction on 3.14+, on every invocation including `--help`.
* Python 3.9/3.10's `BooleanOptionalAction.__init__` unconditionally appends
  " (default: %(default)s)" to any non-None help (removed in 3.11) -- this
  broke `NS(help=argparse.SUPPRESS)` (the option became visible again, since
  argparse hides a help string only by IDENTITY with SUPPRESS) and would leak
  a raw "%(default)s" placeholder into agent-help JSON.

All classes are declared at module level so the AST-derived flag tuples
resolve from a real file.
"""

import argparse

import pytest

from duho import Arg, Args, NS


class NoPrefixTrueDefaultArgs(Args):
    """A True-default bool whose auto-derived flag ALREADY starts with
    `--no-`: BooleanOptionalAction tried to synthesize a
    `--no-no-verify` pair, which 3.14+ rejects outright."""

    no_verify: bool = True
    "Skip the verification step"
    ("--no-verify",)


def test_no_prefixed_true_default_bool_builds_and_parses():
    parser = NoPrefixTrueDefaultArgs._parser_()
    args = parser.parse_args([])
    assert args.no_verify is True

    args = parser.parse_args(["--no-verify"])
    assert args.no_verify is False


def test_no_prefixed_true_default_bool_has_no_double_no_pair():
    """The old `--no-verify`/`--no-no-verify` pair is gone -- a single
    `--no-verify` flag means what it always meant."""
    parser = NoPrefixTrueDefaultArgs._parser_()
    option_strings = {s for action in parser._actions for s in action.option_strings}
    assert "--no-verify" in option_strings
    assert "--no-no-verify" not in option_strings


class MetavarTrueDefaultArgs(Args):
    """A True-default bool with an explicit metavar= -- 3.14 removed the
    (deprecated) metavar/choices/type params from BooleanOptionalAction
    outright, so building this used to crash on 3.14+."""

    sign: Arg[bool, NS(metavar="SIGN")] = True
    "Sign the result"
    ("--sign",)


def test_metavar_true_default_bool_builds_and_parses():
    parser = MetavarTrueDefaultArgs._parser_()
    args = parser.parse_args([])
    assert args.sign is True
    args = parser.parse_args(["--no-sign"])
    assert args.sign is False


class SuppressedTrueDefaultArgs(Args):
    """A True-default bool hidden via NS(help=argparse.SUPPRESS), alongside a
    plain visible one with real help text."""

    telemetry: Arg[bool, NS(help=argparse.SUPPRESS)] = True
    "unused"
    ("--telemetry",)

    cache: bool = True
    "Use the cache"
    ("--cache",)


def test_suppressed_true_default_bool_stays_out_of_help():
    """On 3.9/3.10, BooleanOptionalAction's own help-rewriting turned
    SUPPRESS into a new string ("==SUPPRESS== (default: %(default)s)"),
    defeating argparse's identity check for hiding it, so the option
    reappeared in usage/help."""
    parser = SuppressedTrueDefaultArgs._parser_()
    help_text = parser.format_help()
    assert "--telemetry" not in help_text
    assert "%(" not in help_text


def test_true_default_bool_help_text_is_not_rewritten():
    """No literal "%(default)s" placeholder leaks into `action.help` (what
    agent-help JSON reads raw) on any version."""
    parser = SuppressedTrueDefaultArgs._parser_()
    for action in parser._actions:
        if action.dest == "cache":
            assert action.help == "Use the cache"
            break
    else:
        pytest.fail("cache action not found")


class NoPrefixLayeredFalseDefaultArgs(Args):
    """A FALSE-default bool whose auto-derived flag ALREADY starts with
    `--no-` (the field's own name, not a True-default reversed via --no-),
    AND carries `env=` -- so it is "layered" (can receive True from
    somewhere other than the CLI). BooleanOptionalAction tried to
    synthesize a `--no-no-verify` pair here too, crashing parser
    construction on 3.14+ the same way the True-default case above did."""

    no_verify: Arg[bool, NS(env="DUHO_TEST_LAYERED_NO_VERIFY")] = False
    "Skip verification"
    ("--no-verify",)


def test_no_prefixed_layered_false_default_bool_builds_and_parses():
    parser = NoPrefixLayeredFalseDefaultArgs._parser_()
    option_strings = {s for action in parser._actions for s in action.option_strings}
    assert "--no-verify" in option_strings
    assert "--no-no-verify" not in option_strings

    # The flag's own plain meaning wins: presence means "yes, skip it" (a
    # store_true, not a store_false that would invert a False default).
    args = parser.parse_args(["--no-verify"])
    assert args.no_verify is True


def test_no_prefixed_layered_false_default_bool_env_still_applies(monkeypatch):
    monkeypatch.setenv("DUHO_TEST_LAYERED_NO_VERIFY", "1")
    import duho

    result = duho.parse(NoPrefixLayeredFalseDefaultArgs, [])
    monkeypatch.delenv("DUHO_TEST_LAYERED_NO_VERIFY", raising=False)
    assert result.no_verify is True


class PlainBoolNoConfigAttr(Args):
    """A bare bool field with no `env=` of its own, on a class that declares
    no `_config_` either -- so nothing STATIC on the class says a config
    layer could ever reach it."""

    flag: bool = False
    "A plain flag"
    ("--flag",)


def test_explicit_parse_config_kwarg_makes_a_plain_bool_reversible(tmp_path):
    """`duho.parse(cls, config=path)` supplies a config table at the CALL
    site, which the class itself has no static way to see when its parser is
    BUILT -- previously only `cls._config_` (a class attribute) was checked,
    so this field stayed a plain `store_true` with no way to turn a
    config-supplied `True` back off from the command line."""
    cfg = tmp_path / "c.json"
    cfg.write_text('{"flag": true}')
    import duho

    result = duho.parse(PlainBoolNoConfigAttr, ["--no-flag"], config=cfg)
    assert result.flag is False


def test_without_a_config_kwarg_the_plain_bool_stays_store_true():
    """No `config=` at all: the field has no reason to be reversible, and
    stays a plain `store_true` (no `--no-flag` synthesized)."""
    parser = PlainBoolNoConfigAttr._parser_()
    option_strings = {s for action in parser._actions for s in action.option_strings}
    assert "--flag" in option_strings
    assert "--no-flag" not in option_strings
