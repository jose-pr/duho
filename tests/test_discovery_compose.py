"""Composable discovery: several sources, an error policy, providers, the name rule."""

import pytest

import duho
from duho.discovery import (
    discover_commands,
    register_command_provider,
    unregister_command_provider,
)
from duho.parsers import command_name


def _write(directory, name, body):
    directory.mkdir(exist_ok=True)
    (directory / (name + ".py")).write_text(body)


def _names(commands):
    return [command_name(c) for c in commands]


def test_command_name_is_exported_from_the_root():
    assert duho.command_name is command_name
    assert "command_name" in duho.__all__


def test_a_sequence_of_sources_is_merged_and_sorted(tmp_path):
    _write(tmp_path / "a", "zeta", "def main(args):\n    return 1\n")
    _write(tmp_path / "b", "alpha", "def main(args):\n    return 2\n")
    commands = discover_commands([tmp_path / "a", str(tmp_path / "b")])
    assert _names(commands) == ["alpha", "zeta"]


def test_a_later_source_replaces_an_earlier_command_of_the_same_name(tmp_path):
    _write(tmp_path / "a", "dup", "def main(args):\n    return 1\n")
    _write(tmp_path / "a", "only_a", "def main(args):\n    return 1\n")
    _write(tmp_path / "b", "dup", "def main(args):\n    return 2\n")
    commands = discover_commands((tmp_path / "a", tmp_path / "b"))
    assert _names(commands) == ["dup", "only-a"]
    assert commands[0].main(object()) == 2


def test_an_empty_sequence_discovers_nothing():
    assert discover_commands([]) == []


def test_each_member_of_a_sequence_keeps_the_empty_source_guard(tmp_path):
    with pytest.raises(ValueError):
        discover_commands([tmp_path, ""])


def test_on_error_receives_the_file_and_exception_and_skipping_continues(tmp_path):
    _write(tmp_path, "good", "def main(args):\n    return 0\n")
    _write(tmp_path, "typo", "def main(args:\n")
    _write(tmp_path, "optional", "import not_a_real_module_zz\n")
    seen = []
    commands = discover_commands(
        tmp_path, on_error=lambda source, exc: seen.append((source, exc))
    )
    assert _names(commands) == ["good"]
    by_name = {source.name: exc for source, exc in seen}
    assert set(by_name) == {"typo.py", "optional.py"}
    assert isinstance(by_name["typo.py"], SyntaxError)
    assert isinstance(by_name["optional.py"], ImportError)


def test_on_error_raising_aborts_discovery(tmp_path):
    _write(tmp_path, "typo", "def main(args:\n")

    class Abort(Exception):
        pass

    def stop(source, exc):
        raise Abort() from exc

    with pytest.raises(Abort):
        discover_commands(tmp_path, on_error=stop)


def test_without_on_error_a_syntax_error_still_propagates(tmp_path):
    _write(tmp_path, "typo", "def main(args:\n")
    with pytest.raises(SyntaxError):
        discover_commands(tmp_path)


def test_on_error_covers_a_module_of_a_dotted_package(tmp_path, monkeypatch):
    import importlib
    import sys

    pkg = tmp_path / "compose_pkg_a"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "good.py").write_text("def main(args):\n    return 0\n")
    (pkg / "bad.py").write_text("raise RuntimeError('boom')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    seen = []
    try:
        commands = discover_commands(
            "compose_pkg_a", on_error=lambda source, exc: seen.append((source, exc))
        )
    finally:
        for key in [k for k in sys.modules if k.startswith("compose_pkg_a")]:
            sys.modules.pop(key, None)
    assert _names(commands) == ["good"]
    assert [s for s, _ in seen] == ["compose_pkg_a.bad"]
    assert isinstance(seen[0][1], RuntimeError)


class _Provided(duho.Cmd):
    """A command a provider built."""

    def __call__(self):
        return 0


def _provider():
    def predicate(path):
        return (path / "marker.txt").exists()

    def builder(path, qualname):
        return type("Provided", (_Provided,), {"_parsername_": qualname})

    return predicate, builder


def _provided_tree(tmp_path):
    (tmp_path / "step_dir").mkdir()
    (tmp_path / "step_dir" / "marker.txt").write_text("")
    (tmp_path / "plain_dir").mkdir()
    _write(tmp_path, "loose", "def main(args):\n    return 0\n")


def test_providers_are_not_consulted_by_default(tmp_path):
    _provided_tree(tmp_path)
    predicate, builder = _provider()
    register_command_provider(predicate, builder)
    try:
        assert _names(discover_commands(tmp_path)) == ["loose"]
    finally:
        unregister_command_provider(predicate, builder)


def test_providers_true_offers_each_child_directory(tmp_path):
    _provided_tree(tmp_path)
    predicate, builder = _provider()
    register_command_provider(predicate, builder)
    try:
        commands = discover_commands(tmp_path, providers=True)
    finally:
        unregister_command_provider(predicate, builder)
    assert _names(commands) == ["loose", "step-dir"]


def test_providers_true_offers_the_source_directory_itself(tmp_path):
    (tmp_path / "marker.txt").write_text("")
    _write(tmp_path, "loose", "def main(args):\n    return 0\n")
    predicate, builder = _provider()
    register_command_provider(predicate, builder)
    try:
        commands = discover_commands(tmp_path, providers=True)
    finally:
        unregister_command_provider(predicate, builder)
    assert _names(commands) == [tmp_path.name.replace("_", "-")]


def test_a_failing_provider_build_goes_to_on_error(tmp_path):
    _provided_tree(tmp_path)

    def predicate(path):
        return (path / "marker.txt").exists()

    def builder(path, qualname):
        raise RuntimeError("cannot build " + qualname)

    register_command_provider(predicate, builder)
    seen = []
    try:
        commands = discover_commands(
            tmp_path,
            providers=True,
            on_error=lambda source, exc: seen.append((source.name, str(exc))),
        )
    finally:
        unregister_command_provider(predicate, builder)
    assert _names(commands) == ["loose"]
    assert seen == [("step_dir", "cannot build step-dir")]


def test_app_accepts_a_sequence_of_sources(tmp_path):
    _write(tmp_path / "a", "hello", "def main(args):\n    return 3\n")
    _write(tmp_path / "b", "hello", "def main(args):\n    return 4\n")
    code = duho.app(
        source=[tmp_path / "a", tmp_path / "b"], argv=["hello"], setup_logging=False
    )
    assert code == 4


def test_app_on_error_skips_a_broken_file(tmp_path):
    _write(tmp_path, "hello", "def main(args):\n    return 5\n")
    _write(tmp_path, "typo", "def main(args:\n")
    seen = []
    code = duho.app(
        source=tmp_path,
        argv=["hello"],
        setup_logging=False,
        on_error=lambda source, exc: seen.append(source.name),
    )
    assert code == 5
    assert seen == ["typo.py"]


_BAD_REGISTER = (
    "def register(parser, args):\n    raise RuntimeError('no parser')\n"
    "def main(args):\n    return 0\n"
)


def test_app_on_error_covers_registering_a_command(tmp_path):
    _write(tmp_path, "hello", "def main(args):\n    return 6\n")
    _write(tmp_path, "badreg", _BAD_REGISTER)
    seen = []
    code = duho.app(
        source=tmp_path,
        argv=["hello"],
        setup_logging=False,
        on_error=lambda source, exc: seen.append((command_name(source), str(exc))),
    )
    assert code == 6
    assert seen == [("badreg", "no parser")]


def test_app_without_on_error_a_failing_register_still_raises(tmp_path):
    _write(tmp_path, "badreg", _BAD_REGISTER)
    with pytest.raises(RuntimeError):
        duho.app(source=tmp_path, argv=["badreg"], setup_logging=False)


def test_app_providers_not_forced_on(tmp_path):
    _provided_tree(tmp_path)
    predicate, builder = _provider()
    register_command_provider(predicate, builder)
    try:
        code = duho.app(source=tmp_path, argv=["loose"], setup_logging=False)
    finally:
        unregister_command_provider(predicate, builder)
    assert code == 0
