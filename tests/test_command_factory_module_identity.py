"""Regression tests:

* `duho.command(args_cls, func)` must set `__module__`/`__qualname__`
  on the class it builds to the CALLER's module, not `duho.args` (the module
  that happens to call `type()`) -- otherwise discovery's module-boundary
  filter silently drops it, and `_version_ = duho.AUTO` resolves duho's OWN
  installed version instead of the app's.
* Neither `duho.command()` nor a module command's synthesized `_Args`
  class (`runtime._module_args_cls`) should trigger an AST parse of duho's
  OWN source files looking for a class body that was never there.
"""

import duho
from duho import Args, Cmd


class _Greet(Args):
    name: str = "world"
    ("--name",)


def _do_greet(args):
    return f"hello {args.name}"


def test_command_built_class_has_the_callers_module_not_duhos():
    Greet = duho.command(_Greet, _do_greet, name="greet")
    assert Greet.__module__ == __name__
    assert Greet.__qualname__ != "Greet"  # never collides with a real ClassDef


def test_command_built_class_is_discovered_from_a_command_file(tmp_path):
    cmds = tmp_path / "cmds"
    cmds.mkdir()
    (cmds / "deploy.py").write_text(
        "import duho\n"
        "\n"
        "class DeployArgs(duho.Args):\n"
        "    target: str = 'staging'\n"
        "    ('--target',)\n"
        "\n"
        "def _run(args):\n"
        "    return args.target\n"
        "\n"
        "Deploy = duho.command(DeployArgs, _run, name='deploy')\n",
        encoding="utf-8",
    )

    commands = duho.discover_commands(cmds)
    names = [
        getattr(c, "_parsername_", None) or getattr(c, "__name__", None)
        for c in commands
    ]
    assert "deploy" in names


def test_command_built_root_with_auto_version_resolves_its_own_distribution(
    monkeypatch,
):
    """Before the fix: `_version_ = duho.AUTO`'s distribution lookup used
    `cls.__module__.split('.')[0]`, which was `duho` for a command()-built
    class -- reporting duho's OWN version instead of PackageNotFoundError."""
    calls = []

    def fake_version(dist):
        calls.append(dist)
        raise __import__("importlib.metadata", fromlist=["x"]).PackageNotFoundError(
            dist
        )

    monkeypatch.setattr("importlib.metadata.version", fake_version)

    class AutoArgs(Args):
        _version_ = duho.AUTO

    Built = duho.command(AutoArgs, lambda self: 0, name="built")
    Built._parser_()
    assert calls, "AUTO must attempt a lookup"
    assert calls[0] != "duho"
    assert calls[0] == __name__.split(".")[0]


def test_module_command_synthesized_args_class_does_not_ast_parse_duho(tmp_path):
    """A module command declaring its own `Args` (mixed with the app's
    shared root at registration time) must not trigger an AST scan of
    the `duho.runtime`/`duho.args` sources looking for a ClassDef that was
    never there."""
    import functools
    import unittest.mock as mock

    import duho._introspect as introspect

    calls = []
    original = introspect._module_index

    @functools.wraps(original)
    def spy(path, *a, **kw):
        calls.append(str(path))
        return original(path, *a, **kw)

    cmds = tmp_path / "cmds"
    cmds.mkdir()
    (cmds / "hello.py").write_text(
        "import duho\n"
        "\n"
        "class Args(duho.Args):\n"
        "    loud: bool = False\n"
        "    ('--loud',)\n"
        "\n"
        "def main(args):\n"
        "    return args.loud\n",
        encoding="utf-8",
    )

    class Root(Cmd):
        region: str = "us"
        ("--region",)

        def __call__(self):
            return 0

    with mock.patch.object(introspect, "_module_index", side_effect=spy):
        duho.app(Root, source=cmds, argv=["hello", "--loud"], setup_logging=False)

    for path in calls:
        for package in ("args", "runtime"):
            assert "duho" + "\\" + package + "\\" not in path
            assert "duho" + "/" + package + "/" not in path
