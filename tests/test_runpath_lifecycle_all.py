"""``__all__`` in a RunPath ``__main__.py`` limits which names are lifecycle hooks."""

import textwrap

from duho.discovery import CmdBuilder
from duho.runpath import register


def _write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body))


def _run(tmp_path, name, main_body):
    """Run a step directory whose ``__main__.py`` imports ``init`` from a library.

    The library records ``imported-init`` in the results file when it is called as
    a hook, the ``__main__.py`` defines ``success`` itself, and one step records
    ``step``. Returns the recorded lines in order.
    """
    register()
    results = tmp_path / "results.txt"
    _write(
        tmp_path / "libs" / f"{name}.py",
        f"""\
        def init(cmd, logger):
            with open(r"{results}", "a", encoding="utf-8") as fh:
                fh.write("imported-init\\n")
        """,
    )
    steps = tmp_path / "steps"
    _write(
        steps / "__main__.py",
        f"""\
        import sys

        sys.path.insert(0, r"{tmp_path / "libs"}")
        from {name} import init

        def success(ctx, cmd, logger):
            with open(r"{results}", "a", encoding="utf-8") as fh:
                fh.write("success\\n")

        {main_body}
        """,
    )
    _write(
        steps / "10-one.py",
        f"""\
        def main(cmd):
            with open(r"{results}", "a", encoding="utf-8") as fh:
                fh.write("step\\n")
        """,
    )
    instance = CmdBuilder(steps.name, steps).command()
    instance.rcopts = []
    instance()
    return results.read_text(encoding="utf-8").split() if results.exists() else []


def test_without_all_an_imported_init_is_the_hook(tmp_path):
    assert _run(tmp_path, "lib_plain", "") == ["imported-init", "step", "success"]


def test_all_without_init_leaves_an_imported_init_uncalled(tmp_path):
    ran = _run(tmp_path, "lib_listed_success", '__all__ = ["success"]')
    assert ran == ["step", "success"]


def test_all_listing_an_imported_name_makes_it_the_hook(tmp_path):
    ran = _run(tmp_path, "lib_listed_init", '__all__ = ["init", "success"]')
    assert ran == ["imported-init", "step", "success"]


def test_all_without_success_leaves_it_uncalled(tmp_path):
    ran = _run(tmp_path, "lib_listed_init_only", '__all__ = ["init"]')
    assert ran == ["imported-init", "step"]
