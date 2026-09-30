"""Regression tests for the deferred env/config/instance layering pipeline:
provenance correctness across sibling/nested subcommands, conflicts= groups,
non-idempotent type= factories, and duho.parse(instance) semantics.

All classes are declared at module level in this real ``.py`` file so their
AST-derived flags/env/docstrings resolve normally (never via ``-c``).
"""

import typing as _t

import pytest

import duho
from duho import NS, Arg, Args

# --------------------------------------------------------------------------
# A layered/instance value must go through a `type=` factory
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
    # An already-parsed instance's field is a FINAL value -- piping
    # it through duho.parse(instance) again must not re-run the factory.
    monkeypatch.delenv("DUHO_TEST_BANG_TOKEN", raising=False)
    once = duho.parse(_NonIdempotent, ["--token", "c"])
    assert once.token == "c!"
    twice = duho.parse(once, [])
    assert twice.token == "c!"
    assert duho.value_sources(twice)["token"] == "instance"


def test_parse_instance_seeded_placeholder_does_not_outrank_env(monkeypatch):
    # A bare `bool` field NOT explicitly passed to __init__ is seeded to
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
# env/config layers must respect conflicts= groups.
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
# A bad env/config value for a subcommand the user did NOT select must
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
    result = duho.parse(App33, ["status33"])
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
# value_sources provenance must be scoped per actually-selected
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


@pytest.mark.requires_toml
def test_sibling_subcommand_config_does_not_leak_provenance(tmp_path):
    cfg = tmp_path / "duho.toml"
    cfg.write_text("[sibling-a37]\nport = 10\n\n[sibling-b37]\nport = 20\n")

    result_a = duho.parse(App37, ["sibling-a37"], config=cfg)
    assert result_a.port == 10
    assert duho.value_sources(result_a)["port"] == "config"

    result_b = duho.parse(App37, ["sibling-b37"], config=cfg)
    assert result_b.port == 20
    assert duho.value_sources(result_b)["port"] == "config"


@pytest.mark.requires_toml
def test_unselected_sibling_config_does_not_apply_to_selected_one(tmp_path):
    cfg = tmp_path / "duho.toml"
    cfg.write_text("[sibling-b37]\nport = 20\n")

    result = duho.parse(App37, ["sibling-a37"], config=cfg)
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
# value_sources on a SUBCOMMAND instance must also report inherited
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


@pytest.mark.requires_toml
def test_value_sources_on_subcommand_includes_root_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("DUHO_TEST_R21_TOKEN", "envtok")
    cfg = tmp_path / "duho.toml"
    cfg.write_text('verbose = true\n\n[install-r21]\ntarget = "from-config"\n')

    result = duho.parse(RootR21, ["install-r21"], config=cfg)
    monkeypatch.delenv("DUHO_TEST_R21_TOKEN", raising=False)

    assert isinstance(result, InstallR21)
    sources = duho.value_sources(result)
    assert sources["token"] == "env"
    assert sources["verbose"] == "config"
    assert sources["target"] == "config"


# --------------------------------------------------------------------------
# value_sources() is a per-INSTANCE report -- an OLDER instance of the same
# class must not retroactively change once the class is parsed again.
# --------------------------------------------------------------------------


class RepeatParsed(Args):
    x: "Arg[int, NS(env='DUHO_TEST_REPEAT_X')]" = 1
    ("--x",)


def test_value_sources_does_not_flip_for_an_older_instance_of_the_same_class(
    monkeypatch,
):
    monkeypatch.setenv("DUHO_TEST_REPEAT_X", "5")
    older = duho.parse(RepeatParsed, [])  # x=5, sourced from env
    monkeypatch.delenv("DUHO_TEST_REPEAT_X", raising=False)
    newer = duho.parse(RepeatParsed, ["--x", "2"])  # x=2, sourced from the CLI, no env

    # Parsing the class a SECOND time must not retroactively change what the
    # FIRST (older) instance reports.
    assert duho.value_sources(older) == {"x": "env"}
    assert duho.value_sources(newer) == {"x": "cli"}


# --------------------------------------------------------------------------
# A Union factory's exhaustion error must name the declared member TYPES
# (int, str, ...), not repr() the resolved conversion callables.
# --------------------------------------------------------------------------


class UnionExhaustion(Args):
    value: "Arg[_t.Union[int, float], NS()]" = 0
    ("--value",)


def test_union_conversion_error_names_member_types(capsys):
    with pytest.raises(SystemExit):
        duho.parse(UnionExhaustion, ["--value", "not-a-number"])
    err = capsys.readouterr().err
    assert "could not convert 'not-a-number' using any of int, float" in err
    assert "0x" not in err  # no <function ... at 0x...> repr leaking through


# --------------------------------------------------------------------------
# A layered value for a field whose action ACCUMULATES onto the existing
# namespace value (count/append) must not be staged as a not-yet-converted
# placeholder: `-v` with a config `verbose = 1` tried `1 + placeholder`.
# --------------------------------------------------------------------------


class LayeredCount(Args):
    verbose: "Arg[int, NS(env='DUHO_TEST_COUNT_VERBOSE'), duho.Count()]" = 0
    ("-v", "--verbose")


def test_layered_count_field_starts_from_the_converted_value(monkeypatch):
    monkeypatch.setenv("DUHO_TEST_COUNT_VERBOSE", "1")
    result = duho.parse(LayeredCount, [])
    monkeypatch.delenv("DUHO_TEST_COUNT_VERBOSE", raising=False)
    assert result.verbose == 1


def test_layered_count_field_cli_increments_on_top_of_it(monkeypatch):
    monkeypatch.setenv("DUHO_TEST_COUNT_VERBOSE", "1")
    result = duho.parse(LayeredCount, ["-v"])
    monkeypatch.delenv("DUHO_TEST_COUNT_VERBOSE", raising=False)
    assert result.verbose == 2


class LayeredAppend(Args):
    tags: "Arg[_t.List[str], NS(env='DUHO_TEST_APPEND_TAGS'), duho.Append()]" = []
    ("--tags",)


def test_layered_append_field_starts_from_the_converted_value(monkeypatch):
    monkeypatch.setenv("DUHO_TEST_APPEND_TAGS", "fromenv")
    result = duho.parse(LayeredAppend, [])
    monkeypatch.delenv("DUHO_TEST_APPEND_TAGS", raising=False)
    assert result.tags == ["fromenv"]


def test_layered_append_field_cli_replaces_it_on_the_first_occurrence(monkeypatch):
    # "CLI wins" applies to Append() exactly like every other collection
    # option (Extend(), a plain list/set/tuple field): the first CLI
    # occurrence REPLACES a layered (env/config) value, it does not merge
    # onto it -- previously it accumulated onto the env value the same way
    # stdlib's own "append" action accumulates onto a class default.
    monkeypatch.setenv("DUHO_TEST_APPEND_TAGS", "fromenv")
    result = duho.parse(LayeredAppend, ["--tags", "a", "--tags", "b"])
    monkeypatch.delenv("DUHO_TEST_APPEND_TAGS", raising=False)
    assert result.tags == ["a", "b"]


class LayeredPositionalList(Args):
    files: "Arg[_t.List[str], NS(flags=('files',), env='DUHO_TEST_POS_FILES')]" = []
    ("files",)


def test_layered_zero_token_positional_list_uses_the_converted_value(monkeypatch):
    # A zero-token nargs="*" positional hands the action its own DEFAULT
    # object back as `values` -- when that default is a not-yet-converted
    # placeholder, it must be passed through untouched (for `_finalize_layers`
    # to convert), never iterated/coerced as if it were a real collection.
    monkeypatch.setenv("DUHO_TEST_POS_FILES", "fromenv")
    result = duho.parse(LayeredPositionalList, [])
    monkeypatch.delenv("DUHO_TEST_POS_FILES", raising=False)
    assert result.files == ["fromenv"]


# --------------------------------------------------------------------------
# A cached/reused parser (`duho.parser(cls)` built once, `.parse_args()`
# called more than once) must behave like a fresh one on EVERY call -- a
# dest whose layered value applied on an earlier call, but not this one
# (e.g. its env var was unset in between), must not keep the earlier call's
# converted default/un-required state.
# --------------------------------------------------------------------------


class ReusedParserArgs(Args):
    port: "Arg[int, NS(env='DUHO_TEST_REUSE_PORT')]" = 80
    ("--port",)

    name: "Arg[str, NS(env='DUHO_TEST_REUSE_NAME')]"
    ("--name",)


def test_reused_parser_does_not_leak_layered_state_across_calls(monkeypatch):
    parser = duho.parser(ReusedParserArgs)
    monkeypatch.setenv("DUHO_TEST_REUSE_PORT", "8080")
    monkeypatch.setenv("DUHO_TEST_REUSE_NAME", "x")
    first = parser.parse_args([])
    assert first.port == 8080
    assert first.name == "x"

    monkeypatch.delenv("DUHO_TEST_REUSE_PORT", raising=False)
    monkeypatch.delenv("DUHO_TEST_REUSE_NAME", raising=False)
    # `name` has no class default and no env this time -- the parser must
    # re-require it exactly as a freshly built one would, instead of
    # silently reusing the first call's already-converted values.
    with pytest.raises(SystemExit):
        parser.parse_args([])
