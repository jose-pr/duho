"""Tests: a literal ``%`` anywhere duho hands text to argparse as a
``help=``/``version=`` string must never crash parser build or ``--help`` --
argparse ``%``-formats every one of those unconditionally.

The class-docstring case (a % in a Cmd's own docstring) is
covered by ``tests/test_review_findings.py``; these cover the remaining
sites: a field docstring, a `_version_`/`__version__` string, a module
command's docstring, and the raw `%(default)s`-style placeholders duho's own
agent-help JSON document must EXPAND (not just avoid crashing on).
"""

import pytest

import duho
from duho import agenthelp, Arg, Args, Cmd, NS


def test_field_docstring_with_percent_does_not_crash_parser_build():
    class WithPercentField(Args):
        pct: int = 50
        "Use 50% of CPUs"
        ("--pct",)

    parser = WithPercentField._parser_()  # must not raise
    text = parser.format_help()
    assert "Use 50% of CPUs" in text


def test_field_docstring_with_percent_survives_parse_and_help():
    class WithPercentField2(Args):
        pct: int = 50
        "Use 50% of CPUs"
        ("--pct",)

    result = duho.parse(WithPercentField2, ["--pct", "75"])
    assert result.pct == 75


def test_explicit_help_override_is_not_double_escaped():
    """An explicit `NS(help=...)`/`Meta(help=...)` is applied AFTER the
    docstring-derived escape and must not be touched by it."""

    class Explicit(Args):
        pct: "Arg[int, NS(help='Literal %(default)s used verbatim')]" = 10
        "This docstring is replaced by the explicit help="
        ("--pct",)

    parser = Explicit._parser_()
    text = parser.format_help()
    assert "Literal 10 used verbatim" in text


def test_version_string_with_percent_does_not_crash():
    class VersionedApp(Cmd):
        _version_ = "2.0 (100% rewrite)"

        def __call__(self):
            return 0

    parser = VersionedApp._parser_()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])
    assert exc.value.code == 0


def test_dunder_version_with_percent_does_not_crash():
    class VersionedApp2(Cmd):
        __version__ = "2.0 (100% rewrite)"

        def __call__(self):
            return 0

    parser = VersionedApp2._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["--version"])


def test_module_command_docstring_with_percent_does_not_crash_the_whole_app(tmp_path):
    cmds = tmp_path / "cmds"
    cmds.mkdir()
    (cmds / "usage.py").write_text(
        '"""Report disk usage as a % of capacity."""\n'
        "def main(args):\n"
        "    return 0\n",
        encoding="utf-8",
    )
    (cmds / "other.py").write_text(
        '"""A clean sibling command."""\n' "def main(args):\n" "    return 0\n",
        encoding="utf-8",
    )

    # A % in ONE command's docstring must not break registering (or running)
    # every OTHER sibling command.
    rc = duho.app(source=cmds, argv=["other"], setup_logging=False)
    assert rc == 0
    rc2 = duho.app(source=cmds, argv=["usage"], setup_logging=False)
    assert rc2 == 0


def test_agent_help_expands_default_placeholder(monkeypatch):
    """agent-help must EXPAND `%(default)s`-style argparse templates,
    not copy them verbatim into the JSON document."""
    monkeypatch.setenv("AGENT_HELP", "1")

    class TokenCmd(Cmd):
        token: str = "abc"
        "API token (default: %(default)s)"
        ("--token",)

        def __call__(self):
            return 0

    doc = agenthelp.describe(TokenCmd)
    (opt,) = [o for o in doc["options"] if o["dest"] == "token"]
    assert opt["help"] == "API token (default: abc)"


def test_agent_help_unescapes_a_literal_percent_in_field_help(monkeypatch):
    monkeypatch.setenv("AGENT_HELP", "1")

    class PctCmd(Cmd):
        pct: int = 50
        "Percent 50% of it"
        ("--pct",)

        def __call__(self):
            return 0

    doc = agenthelp.describe(PctCmd)
    (opt,) = [o for o in doc["options"] if o["dest"] == "pct"]
    assert opt["help"] == "Percent 50% of it"
