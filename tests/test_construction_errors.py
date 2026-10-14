"""A ``CommandError`` (or an ``_errors_`` match) raised while a command is built
during the parse is reported like one raised while it runs."""

import pytest

import duho
from duho import Arg, Cli, Cmd, CommandError, Meta

CALLS = []


class Validates(Cmd):
    _parsername_ = "validates"
    fmt: str = "json"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.fmt != "json":
            raise CommandError("unknown format %s" % self.fmt, 2)

    def __call__(self):
        return 0


class Maps(Cmd):
    _parsername_ = "maps"
    path: str = "a"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        raise FileNotFoundError("no such " + self.path)


class Breaks(Cmd):
    _parsername_ = "breaks"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        raise ValueError("not mapped")


class Exits(Cmd):
    _parsername_ = "exits"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        raise SystemExit(9)


class Fine(Cmd):
    _parsername_ = "fine"

    def __call__(self):
        return 0


def _four(message, code, error, command):
    CALLS.append((message, code, error, command))


def _two(message, code):
    CALLS.append((message, code))


def _root(report=None):
    return type(
        "Building",
        (Cli,),
        {
            "_parsername_": "building",
            "_subcommands_": [Validates, Maps, Breaks, Exits, Fine],
            "_errors_": {FileNotFoundError: 66},
            "_report_error_": staticmethod(report) if report else None,
        },
    )


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    CALLS.clear()
    monkeypatch.delenv("DUHO_TRACEBACK", raising=False)


def _via(kind, root, argv):
    """The status, whether it came back or ended the program."""
    try:
        if kind == "main":
            return duho.main(root, argv, setup_logging=False)
        if kind == "app":
            return duho.app(root, argv=argv, setup_logging=False)
        return duho.run(root, argv, exit=False, setup_logging=False)
    except SystemExit as exc:
        return ("exit", exc.code)


KINDS = ["main", "app", "run"]


def _expect(kind, status):
    return status if kind == "run" else ("exit", status)


@pytest.mark.parametrize("kind", KINDS)
def test_the_default_line_and_the_status(capsys, kind):
    got = _via(kind, _root(), ["validates", "--fmt", "toml"])
    assert got == _expect(kind, 2)
    assert capsys.readouterr().err == "building: error: unknown format toml\n"


@pytest.mark.parametrize("kind", KINDS)
def test_a_hook_reports_it_with_no_command(capsys, kind):
    got = _via(kind, _root(_four), ["validates", "--fmt", "toml"])
    assert got == _expect(kind, 2)
    ((message, code, error, command),) = CALLS
    assert (message, code, command) == ("unknown format toml", 2, None)
    assert isinstance(error, CommandError)
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("kind", KINDS)
def test_a_two_argument_hook_is_called_as_before(capsys, kind):
    assert _via(kind, _root(_two), ["validates", "--fmt", "toml"]) == _expect(kind, 2)
    assert CALLS == [("unknown format toml", 2)]


@pytest.mark.parametrize("kind", KINDS)
def test_an_errors_entry_maps_a_constructor_exception(capsys, kind):
    assert _via(kind, _root(), ["maps", "--path", "x"]) == _expect(kind, 66)
    assert capsys.readouterr().err == "building: error: no such x\n"


@pytest.mark.parametrize("kind", KINDS)
def test_an_unmapped_exception_propagates_as_before(kind):
    with pytest.raises(ValueError, match="not mapped"):
        _via(kind, _root(_four), ["breaks"])
    assert CALLS == []


@pytest.mark.parametrize("kind", KINDS)
def test_a_constructors_own_system_exit_is_untouched(kind):
    assert _via(kind, _root(_four), ["exits"]) == _expect(kind, 9)
    assert CALLS == []


@pytest.mark.parametrize("kind", KINDS)
def test_argparses_own_exits_are_unchanged(capsys, kind):
    root = _root(_four)
    assert _via(kind, root, ["validates", "--nonsense"]) == _expect(kind, 2)
    assert _via(kind, root, ["--help"]) == _expect(kind, 0)
    assert CALLS == []
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize("kind", KINDS)
def test_a_good_command_line_is_unaffected(kind):
    assert _via(kind, _root(_four), ["fine"]) == 0
    assert _via(kind, _root(), ["validates"]) == 0


@pytest.mark.parametrize("kind", KINDS)
def test_the_environment_variable_form_is_reported_too(capsys, monkeypatch, kind):
    class FromEnv(Cmd):
        _parsername_ = "from-env"
        fmt: Arg[str, Meta(env="BUILDING_TEST_FMT")] = "json"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if self.fmt != "json":
                raise CommandError("unknown format %s" % self.fmt, 2)

    root = type(
        "Envy",
        (Cli,),
        {"_parsername_": "envy", "_subcommands_": [FromEnv]},
    )
    monkeypatch.setenv("BUILDING_TEST_FMT", "toml")
    got = _via(kind, root, ["from-env"])
    assert got == _expect(kind, 2)
    assert capsys.readouterr().err == "envy: error: unknown format toml\n"


@pytest.mark.parametrize("kind", KINDS)
def test_traceback_variable_without_a_hook_propagates(monkeypatch, capsys, kind):
    monkeypatch.setenv("DUHO_TRACEBACK", "1")
    with pytest.raises(CommandError):
        _via(kind, _root(), ["validates", "--fmt", "toml"])
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("kind", KINDS)
def test_traceback_variable_with_a_hook_prints_then_reports(monkeypatch, capsys, kind):
    monkeypatch.setenv("DUHO_TRACEBACK", "1")
    got = _via(kind, _root(_four), ["validates", "--fmt", "toml"])
    assert got == _expect(kind, 2)
    assert len(CALLS) == 1
    assert "CommandError: unknown format toml" in capsys.readouterr().err


def test_kept_parser_exits_do_not_cover_a_reported_construction_error(capsys):
    got = duho.run(
        _root(),
        ["validates", "--fmt", "toml"],
        exit=False,
        keep_parser_exits=True,
        setup_logging=False,
    )
    assert got == 2
    assert capsys.readouterr().err == "building: error: unknown format toml\n"


def test_a_tool_call_reports_it_as_it_reports_a_dispatch_error():
    from duho.mcp import call_tool

    root = _root(_four)
    root._exit_codes_ = {2: "bad input"}
    result = call_tool(root, "building.validates", {"fmt": "toml"})
    assert result == {
        "content": [
            {"type": "text", "text": "unknown format toml\nexit code: 2 (bad input)"}
        ],
        "isError": True,
    }
    assert CALLS == []
