"""`_config_validate_` decides what happens to a config-file key nothing applies:
the default and "ignore" skip it, "warn" logs, "strict" is a usage error, and a
function receives the loaded mapping and the dotted paths."""

import json
import logging

import pytest

import duho
from duho import Cli, Cmd, LoggingArgs

SECRET = "s3cr3t-value-7731"


class Install(Cmd):
    target: str = "default"
    ("--target",)

    def __call__(self):
        return 0


class Root(Cli):
    _parsername_ = "validate-root"
    _subcommands_ = [Install]
    port: int = 1
    ("--port",)


def _write(tmp_path, obj, name="app.json"):
    path = tmp_path / name
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def _root(policy, **extra):
    return type("Root", (Root,), {"_config_validate_": policy, **extra})


STATIC_FILE = {
    "port": 2,
    "bogus": SECRET,
    "install": {"target": "x", "targte": SECRET},
    "ghost": {"a": SECRET},
}
STATIC_UNKNOWN = ("bogus", "install.targte", "ghost")


def _names(unknown):
    return ", ".join(repr(p) for p in unknown)


# -- static tree under parse / main ----------------------------------------


def test_static_tree_reports_the_three_kinds_together(tmp_path):
    seen = []
    cls = _root(lambda config, unknown: seen.append((config, unknown)))
    duho.parse(cls, ["install"], config=_write(tmp_path, STATIC_FILE))
    ((config, unknown),) = seen
    assert unknown == STATIC_UNKNOWN
    assert config["port"] == 2 and config["install"]["target"] == "x"


def test_known_keys_are_not_reported(tmp_path):
    seen = []
    cls = _root(lambda config, unknown: seen.append(unknown))
    duho.parse(
        cls,
        ["install"],
        config=_write(tmp_path, {"port": 3, "install": {"target": "y"}}),
    )
    assert seen == [()]


def test_a_non_table_under_a_command_name_is_unknown(tmp_path):
    seen = []
    cls = _root(lambda config, unknown: seen.append(unknown))
    duho.parse(cls, ["install"], config=_write(tmp_path, {"install": 5}))
    assert seen == [("install",)]


@pytest.mark.parametrize("policy", [None, "ignore"])
def test_default_and_ignore_are_silent(tmp_path, caplog, capsys, policy):
    cls = _root(policy)
    with caplog.at_level(logging.DEBUG, logger="duho.config"):
        parsed = duho.parse(cls, ["install"], config=_write(tmp_path, STATIC_FILE))
    assert parsed.port == 2
    assert not [r for r in caplog.records if r.levelno >= logging.INFO]
    assert capsys.readouterr().err == ""


def test_warn_logs_once_and_runs(tmp_path, caplog):
    path = _write(tmp_path, STATIC_FILE)
    cls = _root("warn")
    with caplog.at_level(logging.WARNING, logger="duho.config"):
        assert duho.main(cls, ["install"], config=path, setup_logging=False) == 0
    records = [r for r in caplog.records if r.name == "duho.config"]
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert records[0].getMessage() == (
        f"duho: config file {path}: unknown keys: {_names(STATIC_UNKNOWN)}"
    )


def test_warn_singular_wording(tmp_path, caplog):
    path = _write(tmp_path, {"bogus": 1})
    with caplog.at_level(logging.WARNING, logger="duho.config"):
        duho.parse(_root("warn"), ["install"], config=path)
    assert caplog.records[-1].getMessage() == (
        f"duho: config file {path}: unknown key 'bogus'"
    )


def test_strict_is_a_usage_error_without_a_traceback(tmp_path, capsys):
    path = _write(tmp_path, STATIC_FILE)
    with pytest.raises(SystemExit) as info:
        duho.parse(_root("strict"), ["install"], config=path)
    assert info.value.code == 2
    err = capsys.readouterr().err
    assert f"duho: config file {path}: unknown keys: {_names(STATIC_UNKNOWN)}" in err
    assert "Traceback" not in err
    assert SECRET not in err


def test_strict_main_exits_2(tmp_path, capsys):
    path = _write(tmp_path, {"bogus": 1})
    with pytest.raises(SystemExit) as info:
        duho.main(_root("strict"), ["install"], config=path, setup_logging=False)
    assert info.value.code == 2
    assert "unknown key 'bogus'" in capsys.readouterr().err


def test_strict_help_and_version_still_work(tmp_path, capsys):
    path = _write(tmp_path, STATIC_FILE)
    cls = _root("strict", _version_="1.2.3")
    with pytest.raises(SystemExit) as info:
        duho.parse(cls, ["--help"], config=path)
    assert info.value.code == 0
    with pytest.raises(SystemExit) as info:
        duho.parse(cls, ["--version"], config=path)
    assert info.value.code == 0


def test_strict_accepts_a_file_with_only_known_keys(tmp_path):
    path = _write(tmp_path, {"port": 4, "install": {"target": "z"}})
    assert duho.parse(_root("strict"), ["install"], config=path).target == "z"


def test_no_value_reaches_any_message(tmp_path, caplog, capsys):
    path = _write(tmp_path, STATIC_FILE)
    with caplog.at_level(logging.DEBUG, logger="duho.config"):
        duho.parse(_root("warn"), ["install"], config=path)
    assert SECRET not in caplog.text
    with pytest.raises(SystemExit):
        duho.parse(_root("strict"), ["install"], config=path)
    assert SECRET not in capsys.readouterr().err


# -- the function form ------------------------------------------------------


def test_a_function_value_error_is_a_usage_error(tmp_path, capsys):
    def check(config, unknown):
        raise ValueError("port must be above 1024")

    path = _write(tmp_path, {"port": 2})
    with pytest.raises(SystemExit) as info:
        duho.parse(_root(check), ["install"], config=path)
    assert info.value.code == 2
    err = capsys.readouterr().err
    assert f"duho: config file {path}: port must be above 1024" in err
    assert "Traceback" not in err


def test_a_function_is_called_even_with_nothing_unknown(tmp_path):
    seen = []
    cls = _root(lambda config, unknown: seen.append(unknown))
    duho.parse(cls, ["install"], config=_write(tmp_path, {"port": 2}))
    assert seen == [()]


def test_a_function_other_exception_propagates(tmp_path):
    def check(config, unknown):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        duho.parse(_root(check), ["install"], config=_write(tmp_path, {}))


def test_a_function_empty_value_error_propagates(tmp_path):
    def check(config, unknown):
        raise ValueError()

    with pytest.raises(ValueError):
        duho.parse(_root(check), ["install"], config=_write(tmp_path, {}))


def test_nothing_runs_without_a_file(tmp_path):
    seen = []
    cls = _root(
        lambda config, unknown: seen.append(1),
        _config_=str(tmp_path / "missing.json"),
    )
    duho.parse(cls, ["install"])
    assert seen == []
    assert duho.parse(_root(lambda c, u: seen.append(1)), ["install"]).port == 1
    assert seen == []


def test_the_prepass_of_a_config_field_validates_nothing(tmp_path):
    seen = []

    class WithField(Root):
        _config_field_ = "settings"
        _config_validate_ = lambda config, unknown: seen.append(unknown)  # noqa: E731
        settings: str = ""
        ("--settings",)

    path = _write(tmp_path, {"port": 5, "bogus": 1})
    parsed = duho.parse(WithField, ["--settings", str(path), "install"])
    assert parsed.port == 5
    assert seen == [("bogus",)]


@pytest.mark.parametrize("value", ["loud", True, 3, ["strict"]])
def test_an_invalid_value_is_a_value_error_naming_the_class(value):
    class BadPolicy(Root):
        _config_validate_ = value

    with pytest.raises(ValueError, match="BadPolicy._config_validate_"):
        duho.parse(BadPolicy, ["install"])
    with pytest.raises(ValueError, match="BadPolicy._config_validate_"):
        duho.app(BadPolicy, argv=["install"], setup_logging=False)


# -- other readers ----------------------------------------------------------


def test_the_policy_applies_through_a_config_loader(tmp_path, capsys):
    path = tmp_path / "app.custom"
    path.write_text("port=2\nbogus=1\n", encoding="utf-8")

    def loader(p):
        return dict(line.split("=") for line in p.read_text(encoding="utf-8").split())

    cls = _root("strict", _config_loader_=loader, _config_=str(path))
    with pytest.raises(SystemExit) as info:
        duho.parse(cls, ["install"])
    assert info.value.code == 2
    assert "unknown key 'bogus'" in capsys.readouterr().err


def test_the_policy_applies_to_a_yaml_file(tmp_path, capsys):
    pytest.importorskip("yaml")
    path = tmp_path / "app.yaml"
    path.write_text("port: 2\nbogus: 1\ninstall:\n  targte: x\n", encoding="utf-8")
    with pytest.raises(SystemExit) as info:
        duho.parse(_root("strict"), ["install"], config=path)
    assert info.value.code == 2
    assert "unknown keys: 'bogus', 'install.targte'" in capsys.readouterr().err


# -- app(): class, module and source commands -------------------------------

_CLASS_SOURCE = """\
from duho import Cmd


class Sourced(Cmd):
    name: str = "w"
    ("--name",)

    def __call__(self):
        return 0
"""

_MODULE_WITH_ARGS = '''\
"""A module command with declared fields."""


class Args:
    level: int = 0


def main(args):
    return None
'''

_MODULE_PLAIN = '''\
"""A module command with no declared fields."""


def main(args):
    return None
'''


class _Klass(Cmd):
    _parsername_ = "klass"
    _parseraliases_ = ["k"]
    name: str = "n"
    ("--name",)

    def __call__(self):
        return 0


class _AppRoot(LoggingArgs, Cli):
    _parsername_ = "app-validate-root"
    port: int = 1
    ("--port",)


def _app_root(policy):
    return type("AppRoot", (_AppRoot,), {"_config_validate_": policy})


@pytest.fixture
def source_dir(tmp_path):
    src = tmp_path / "cmds"
    src.mkdir()
    (src / "withargs.py").write_text(_MODULE_WITH_ARGS, encoding="utf-8")
    (src / "plain.py").write_text(_MODULE_PLAIN, encoding="utf-8")
    (src / "sourced.py").write_text(_CLASS_SOURCE, encoding="utf-8")
    return src


APP_FILE = {
    "port": 3,
    "loglevels": {"a.b": "debug"},
    "nope": SECRET,
    "klass": {"name": "x", "bad": SECRET, "port": 1},
    "k": {"name": "alias table"},
    "withargs": {"level": 2, "port": 4, "bad": SECRET},
    "plain": {"x": SECRET},
    "sourced": {"name": "y", "zzz": SECRET},
    "ghost": {"a": SECRET},
}
APP_UNKNOWN = (
    "nope",
    "klass.bad",
    "klass.port",
    "k",
    "withargs.bad",
    "plain.x",
    "sourced.zzz",
    "ghost",
)


def _run_app(policy, source_dir, tmp_path, argv, obj=APP_FILE):
    return duho.app(
        _app_root(policy),
        commands=[_Klass, *duho.discover_commands(source_dir)],
        argv=argv,
        config=_write(tmp_path, obj),
        setup_logging=False,
    )


def test_app_reports_every_kind_of_command(tmp_path, source_dir):
    seen = []
    _run_app(
        lambda config, unknown: seen.append(unknown),
        source_dir,
        tmp_path,
        ["klass"],
    )
    (unknown,) = seen
    assert unknown == APP_UNKNOWN


def test_app_known_keys_alone_are_not_reported(tmp_path, source_dir):
    seen = []
    obj = {
        "port": 3,
        "loglevels": {"a.b": "debug"},
        "klass": {"name": "x"},
        "withargs": {"level": 2, "port": 4},
        "sourced": {"name": "y"},
    }
    _run_app(
        lambda config, unknown: seen.append(unknown),
        source_dir,
        tmp_path,
        ["klass"],
        obj,
    )
    assert seen == [()]


def test_app_strict_exits_2_and_help_works(tmp_path, source_dir, capsys):
    with pytest.raises(SystemExit) as info:
        _run_app("strict", source_dir, tmp_path, ["klass"])
    assert info.value.code == 2
    err = capsys.readouterr().err
    assert f"unknown keys: {_names(APP_UNKNOWN)}" in err
    assert "Traceback" not in err and SECRET not in err
    for argv in (["--help"], ["klass", "--help"], ["plain", "--help"]):
        with pytest.raises(SystemExit) as info:
            _run_app("strict", source_dir, tmp_path, argv)
        assert info.value.code == 0


def test_app_strict_runs_a_clean_file(tmp_path, source_dir):
    obj = {"port": 3, "klass": {"name": "x"}, "plain": {}}
    assert _run_app("strict", source_dir, tmp_path, ["klass"], obj) == 0


def test_app_warn_logs_once(tmp_path, source_dir, caplog):
    with caplog.at_level(logging.WARNING, logger="duho.config"):
        assert _run_app("warn", source_dir, tmp_path, ["klass"]) == 0
    records = [r for r in caplog.records if r.name == "duho.config"]
    assert len(records) == 1
    assert records[0].getMessage().endswith(f"unknown keys: {_names(APP_UNKNOWN)}")
    assert SECRET not in caplog.text


@pytest.mark.parametrize("policy", [None, "ignore"])
def test_app_default_and_ignore_are_silent(tmp_path, source_dir, caplog, policy):
    with caplog.at_level(logging.INFO, logger="duho.config"):
        assert _run_app(policy, source_dir, tmp_path, ["klass"]) == 0
    assert not caplog.records


def test_app_function_value_error_is_a_usage_error(tmp_path, source_dir, capsys):
    def check(config, unknown):
        raise ValueError("no good")

    with pytest.raises(SystemExit) as info:
        _run_app(check, source_dir, tmp_path, ["klass"])
    assert info.value.code == 2
    assert "no good" in capsys.readouterr().err


def test_app_function_is_not_called_without_a_file(tmp_path, source_dir):
    seen = []
    root = _app_root(lambda config, unknown: seen.append(1))
    assert duho.app(root, commands=[_Klass], argv=["klass"], setup_logging=False) == 0
    assert seen == []


def test_app_a_class_root_with_a_static_tree(tmp_path):
    seen = []
    cls = _root(lambda config, unknown: seen.append(unknown))
    duho.app(
        cls,
        argv=["install"],
        config=_write(tmp_path, STATIC_FILE),
        setup_logging=False,
    )
    assert seen == [STATIC_UNKNOWN]


def test_app_source_alone_registers_its_commands(tmp_path, source_dir):
    seen = []
    duho.app(
        _app_root(lambda config, unknown: seen.append(unknown)),
        source=source_dir,
        argv=["plain"],
        config=_write(tmp_path, {"sourced": {"zzz": 1}, "withargs": {"level": 1}}),
        setup_logging=False,
    )
    assert seen == [("sourced.zzz",)]


def test_parse_globals_does_not_judge_the_file(tmp_path):
    # The command that owns the table is registered by `app`, after this call.
    path = _write(tmp_path, {"port": 4, "klass": {"name": "x"}})
    seen = []
    cls = _app_root(lambda config, unknown: seen.append(unknown))
    assert duho.parse_globals(cls, [], config=path).port == 4
    assert seen == []
    assert duho.parse_globals(_app_root("strict"), [], config=path).port == 4
    assert (
        duho.app(
            _app_root("strict"),
            commands=[_Klass],
            argv=["klass"],
            config=path,
            setup_logging=False,
        )
        == 0
    )
