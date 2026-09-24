"""Regression tests for the deferred env/config/instance layering pipeline:
provenance correctness across sibling/nested subcommands, conflicts= groups,
non-idempotent type= factories, and duho.parse(instance) semantics.

All classes are declared at module level in this real ``.py`` file so their
AST-derived flags/env/docstrings resolve normally (never via ``-c``).
"""

import duho
from duho import NS, Arg, Args

# --------------------------------------------------------------------------
# A028 / A031: a layered/instance value must go through a `type=` factory
# exactly once, never twice -- and duho.parse(instance) must only treat a
# field the caller EXPLICITLY passed to __init__ as an override, not a
# placeholder Args.__init__ itself seeded for an omitted field.
# --------------------------------------------------------------------------


def _bang(text: str) -> str:
    """A deliberately NON-idempotent factory: applying it twice is visible."""
    return text + "!"


class _NonIdempotent(Args):
    token: "Arg[str, NS(type=_bang, env='DUHO_TEST_BANG_TOKEN')]" = "class-default"
    ("--token",)

    verbose: bool = False
    "Seeded by Args.__init__ when omitted -- must not look like an override."
    ("--verbose",)


def test_env_value_runs_through_factory_exactly_once(monkeypatch):
    monkeypatch.setenv("DUHO_TEST_BANG_TOKEN", "x")
    result = duho.parse(_NonIdempotent, [])
    monkeypatch.delenv("DUHO_TEST_BANG_TOKEN", raising=False)
    assert result.token == "x!"


def test_cli_value_runs_through_factory_exactly_once(monkeypatch):
    monkeypatch.delenv("DUHO_TEST_BANG_TOKEN", raising=False)
    result = duho.parse(_NonIdempotent, ["--token", "c"])
    assert result.token == "c!"


def test_parse_instance_value_is_not_reconverted(monkeypatch):
    # A031/A028: an already-parsed instance's field is a FINAL value -- piping
    # it through duho.parse(instance) again must not re-run the factory.
    monkeypatch.delenv("DUHO_TEST_BANG_TOKEN", raising=False)
    once = duho.parse(_NonIdempotent, ["--token", "c"])
    assert once.token == "c!"
    twice = duho.parse(once, [])
    assert twice.token == "c!"
    assert duho.value_sources(twice)["token"] == "instance"


def test_parse_instance_seeded_placeholder_does_not_outrank_env(monkeypatch):
    # A031: a bare `bool` field NOT explicitly passed to __init__ is seeded to
    # its effective default by Args.__init__ (so a direct instance has the
    # same attribute surface as a parsed one) -- that placeholder must not be
    # mistaken for an explicit instance override that would outrank env.
    monkeypatch.setenv("DUHO_TEST_BANG_TOKEN", "fromenv")
    instance = _NonIdempotent(token="explicit")  # verbose NOT passed
    assert instance.verbose is False  # seeded placeholder

    result = duho.parse(instance, [])
    monkeypatch.delenv("DUHO_TEST_BANG_TOKEN", raising=False)
    # The EXPLICITLY passed field still wins over env...
    assert result.token == "explicit"
    # ...but env was never even considered irrelevant here (no env for
    # `verbose`); the real check is that an instance value that WAS only
    # seeded is not reported as "instance" provenance.
    sources = duho.value_sources(result)
    assert sources["token"] == "instance"
    assert sources["verbose"] != "instance"


class _EnvOverridable(Args):
    flag: "Arg[bool, NS(env='DUHO_TEST_A31_FLAG')]" = False
    ("--flag",)


def test_parse_instance_placeholder_lets_env_through(monkeypatch):
    # The sharper version of the above: the placeholder field itself is
    # backed by env, so the pre-fix bug (seeded False outranking env) is
    # directly observable.
    monkeypatch.setenv("DUHO_TEST_A31_FLAG", "1")
    instance = _EnvOverridable()  # flag NOT explicitly passed -> seeded False
    result = duho.parse(instance, [])
    monkeypatch.delenv("DUHO_TEST_A31_FLAG", raising=False)
    assert result.flag is True
    assert duho.value_sources(result)["flag"] == "env"


# --------------------------------------------------------------------------
# A032: env/config layers must respect conflicts= groups.
# --------------------------------------------------------------------------


class _Auth(Args):
    token: "Arg[str, NS(env='DUHO_TEST_AUTH_TOKEN', conflicts='auth', conflicts_required=True)]" = ("")
    ("--token",)

    token_file: "Arg[str, NS(conflicts='auth')]" = ""
    ("--token-file",)


def test_env_value_satisfies_required_conflicts_group(monkeypatch):
    monkeypatch.setenv("DUHO_TEST_AUTH_TOKEN", "secret")
    # Pre-fix this raised SystemExit("one of the arguments --token
    # --token-file is required") even though env supplied a group member.
    result = duho.parse(_Auth, [])
    monkeypatch.delenv("DUHO_TEST_AUTH_TOKEN", raising=False)
    assert result.token == "secret"


def test_cli_sibling_drops_stale_layered_conflicts_member(monkeypatch):
    monkeypatch.setenv("DUHO_TEST_AUTH_TOKEN", "secret")
    result = duho.parse(_Auth, ["--token-file", "f"])
    monkeypatch.delenv("DUHO_TEST_AUTH_TOKEN", raising=False)
    assert result.token_file == "f"
    # The stale env value must NOT coexist with the CLI-chosen sibling.
    assert result.token == ""


# --------------------------------------------------------------------------
# A033: a bad env/config value for a subcommand the user did NOT select must
# never crash an unrelated command (or --help).
# --------------------------------------------------------------------------


class Serve33(Args):
    port: "Arg[int, NS(env='DUHO_TEST_A33_PORT')]" = 80
    ("--port",)

    def __call__(self):
        return 0


class Status33(Args):
    def __call__(self):
        return 0


class App33(Args):
    _subcommands_ = [Serve33, Status33]

    def __call__(self):
        return 0


def test_bad_env_for_unselected_subcommand_does_not_crash(monkeypatch):
    monkeypatch.setenv("DUHO_TEST_A33_PORT", "eighty")
    result = duho.parse(App33, ["Status33"])
    monkeypatch.delenv("DUHO_TEST_A33_PORT", raising=False)
    assert isinstance(result, Status33)


def test_bad_env_for_unselected_subcommand_does_not_break_help(monkeypatch, capsys):
    monkeypatch.setenv("DUHO_TEST_A33_PORT", "eighty")
    import pytest

    with pytest.raises(SystemExit) as exc:
        duho.main(App33, ["--help"], setup_logging=False)
    monkeypatch.delenv("DUHO_TEST_A33_PORT", raising=False)
    assert exc.value.code == 0
    assert "usage:" in capsys.readouterr().out


# --------------------------------------------------------------------------
# A037/A038: value_sources provenance must be scoped per actually-selected
# parser, not merged across every sibling at build time, and must never be
# inherited via the MRO from an unrelated already-parsed class.
# --------------------------------------------------------------------------


class SiblingA37(Args):
    port: int = 1
    ("--port",)

    def __call__(self):
        return 0


class SiblingB37(Args):
    port: int = 1
    ("--port",)

    def __call__(self):
        return 0


class App37(Args):
    _subcommands_ = [SiblingA37, SiblingB37]

    def __call__(self):
        return 0


def test_sibling_subcommand_config_does_not_leak_provenance(tmp_path):
    cfg = tmp_path / "duho.toml"
    cfg.write_text("[SiblingA37]\nport = 10\n\n[SiblingB37]\nport = 20\n")

    result_a = duho.parse(App37, ["SiblingA37"], config=cfg)
    assert result_a.port == 10
    assert duho.value_sources(result_a)["port"] == "config"

    result_b = duho.parse(App37, ["SiblingB37"], config=cfg)
    assert result_b.port == 20
    assert duho.value_sources(result_b)["port"] == "config"


def test_unselected_sibling_config_does_not_apply_to_selected_one(tmp_path):
    cfg = tmp_path / "duho.toml"
    cfg.write_text("[SiblingB37]\nport = 20\n")

    result = duho.parse(App37, ["SiblingA37"], config=cfg)
    assert result.port == 1
    assert duho.value_sources(result)["port"] == "default"


class Base38(Args):
    x: str = "y"
    ("--x",)

    def __call__(self):
        return 0


class NeverParsed38(Base38):
    """A subclass of an already-parsed class that is never itself parsed."""


def test_value_sources_of_never_parsed_subclass_ignores_parsed_base():
    duho.parse(Base38, [])
    instance = NeverParsed38(x="z")
    assert duho.value_sources(instance) == {}


# --------------------------------------------------------------------------
# R021: value_sources on a SUBCOMMAND instance must also report inherited
# root/global fields, not just the subcommand's own declared fields.
# --------------------------------------------------------------------------


class InstallR21(Args):
    target: str = "default-target"
    ("--target",)

    def __call__(self):
        return 0


class RootR21(Args):
    token: "Arg[str, NS(env='DUHO_TEST_R21_TOKEN')]" = ""
    ("--token",)

    verbose: bool = False
    ("--verbose",)

    _subcommands_ = [InstallR21]

    def __call__(self):
        return 0


def test_value_sources_on_subcommand_includes_root_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("DUHO_TEST_R21_TOKEN", "envtok")
    cfg = tmp_path / "duho.toml"
    cfg.write_text('verbose = true\n\n[InstallR21]\ntarget = "from-config"\n')

    result = duho.parse(RootR21, ["InstallR21"], config=cfg)
    monkeypatch.delenv("DUHO_TEST_R21_TOKEN", raising=False)

    assert isinstance(result, InstallR21)
    sources = duho.value_sources(result)
    assert sources["token"] == "env"
    assert sources["verbose"] == "config"
    assert sources["target"] == "config"
