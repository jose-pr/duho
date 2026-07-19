"""Phase D regression tests (D7 bundle):
C13 (snakecase), C14 (value_sources), M9 (_getcolor), M10 (_parsername_),
M11/M18 (_introspect), M12 (sidecar leak), M16 (override default),
M19 (relative_to), M22 (run_command lifecycle).
"""

import sys
import textwrap

import pytest

import duho
from duho import Arg, Args, Cli, Cmd, NS
from duho import logging as _duho_logging
from duho.qualname import DotQualNamed
from duho.text import camelcase, snakecase


# -- C13: snakecase ----------------------------------------------------------


def test_snakecase_interior_uppercase():
    assert snakecase("CamelCaseName") == "camel_case_name"
    assert snakecase("") == ""
    assert camelcase(snakecase("SomeThingHere")) == "SomeThingHere"


# -- C14: value_sources ------------------------------------------------------


class _FlagNoDefault(Args):
    flag: bool
    "Flag"
    ("--flag",)


def test_value_sources_store_true_default_not_cli():
    r = duho.parse(_FlagNoDefault, [])
    assert duho.value_sources(r)["flag"] == "default"
    r2 = duho.parse(_FlagNoDefault, ["--flag"])
    assert duho.value_sources(r2)["flag"] == "cli"


class _SubCfg(Cmd):
    target: str = "dev"
    "Target"
    ("--target",)

    def __call__(self):
        return 0


class _RootCfg(Cli):
    _subcommands_ = [_SubCfg]

    def __call__(self):
        return 0


def test_value_sources_subcommand_config(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[_SubCfg]\ntarget = "prod"\n')
    result = duho.parse(_RootCfg, ["_SubCfg"], config=cfg)
    assert result.target == "prod"
    assert duho.value_sources(result)["target"] == "config"


# -- M9: _getcolor -----------------------------------------------------------


def test_getcolor_compound_without_colorama(monkeypatch):
    monkeypatch.setattr(_duho_logging, "_color", None)
    # Pre-fix: "red+white".isalpha() is False, so the raw compound string is
    # returned verbatim. Post-fix: no colorama -> "".
    assert _duho_logging._getcolor("red+white") == ""


def test_getcolor_ansi_passthrough(monkeypatch):
    monkeypatch.setattr(_duho_logging, "_color", None)
    assert _duho_logging._getcolor("\033[31m") == "\033[31m"


# -- M10: _parsername_ stickiness --------------------------------------------


class _StickyName(Args):
    x: str = "a"
    "X"
    ("--x",)


def test_caller_supplied_name_not_persisted():
    _StickyName._parser_(name="alias")
    assert getattr(_StickyName, "_parsername_", None) != "alias"


# -- M12: sidecar leak -------------------------------------------------------


class _SetField(Args):
    tags: set
    "Tags"
    ("--tags",)


def test_collection_sidecar_not_leaked():
    r = duho.parse(_SetField, ["--tags", "a", "b"])
    assert r.tags == {"a", "b"}
    assert not any(k.startswith("_duho_items_") for k in vars(r))


# -- M16: child override default wins ----------------------------------------


class _SubOverride(Cmd):
    verbose: int = 3
    "Verbosity"
    ("--verbose",)

    def __call__(self):
        return 0


class _RootOverride(Cli):
    verbose: int = 0
    "Verbosity"
    ("--verbose",)

    _subcommands_ = [_SubOverride]

    def __call__(self):
        return 0


def test_child_override_default_wins():
    r = duho.parse(_RootOverride, ["_SubOverride"])
    assert r.verbose == 3


# -- M18: docstring misattribution after a non-literal expr ------------------


class _Misattr(Args):
    a: int = 1
    int  # bare-name Expr (non-literal); must reset docstring attribution
    "must-not-attach-to-a"
    ("--a",)

    b: str = "x"
    "b doc"
    ("--b",)


def test_docstring_not_misattributed():
    builders = {b.name: b for b in _Misattr._getargs_()}
    assert builders["a"].help != "must-not-attach-to-a"
    assert builders["b"].help == "b doc"


# -- M19: relative_to with an empty base -------------------------------------


def test_relative_to_empty_base_returns_self():
    q = DotQualNamed("a.b.c")
    assert q.relative_to(DotQualNamed("")) == "a.b.c"


def test_relative_to_prefix():
    q = DotQualNamed("a.b.c")
    assert q.relative_to(DotQualNamed("a.b")) == "c"


# -- M22: run_command lifecycle ----------------------------------------------


_MODULE_NONZERO = '''\
"""A module command whose main returns a non-zero exit code."""

TRACE = []


def init(args):
    return "ctx"


def main(args):
    return 2


def success(ctx, args):
    TRACE.append("success")


def finally_(ctx, args):
    TRACE.append("finally")
'''

_MODULE_RAISES_AND_FINALLY_RAISES = '''\
"""main raises; finally_ also raises -- the original must propagate."""


def init(args):
    return "ctx"


def main(args):
    raise RuntimeError("original")


def success(ctx, args):
    pass


def finally_(ctx, args):
    raise RuntimeError("from-finally")
'''


@pytest.fixture(autouse=True)
def _clean_discovered():
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        if name.startswith("duho._discovered."):
            sys.modules.pop(name, None)


def _module_command(tmp_path, name, source):
    (tmp_path / name).write_text(source)
    cmds = duho.discover_commands(tmp_path)
    return cmds[0]


def test_success_not_run_on_nonzero(tmp_path):
    from duho.runtime import run_command

    cmd = _module_command(tmp_path, "job.py", _MODULE_NONZERO)
    rc = run_command(cmd, object())
    assert rc == 2
    assert cmd.module.TRACE == ["finally"]  # success skipped, finally ran


def test_finally_does_not_mask_original(tmp_path):
    from duho.runtime import run_command

    cmd = _module_command(tmp_path, "job2.py", _MODULE_RAISES_AND_FINALLY_RAISES)
    with pytest.raises(RuntimeError, match="original"):
        run_command(cmd, object())
