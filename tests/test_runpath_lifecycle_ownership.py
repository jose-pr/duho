"""RunPath reads the ``__main__.py`` hooks only from names defined in that file."""

from duho.runpath._lifecycle import _load_lifecycle


def _load(tmp_path, body, qualname):
    (tmp_path / "__main__.py").write_text(body)
    return _load_lifecycle(tmp_path, qualname)


def test_imported_names_are_not_lifecycle_hooks(tmp_path):
    body = (
        "from shutil import copy as init\n"
        "from shutil import move as success\n"
        "from shutil import rmtree as finally_\n"
    )
    life = _load(tmp_path, body, "ownership_imported")
    assert (life.init, life.success, life.finally_) == (None, None, None)


def test_names_defined_in_the_file_are_hooks(tmp_path):
    body = (
        "def init(cmd, logger):\n    return 1\n"
        "def success(cmd, ctx):\n    pass\n"
        "def finally_(cmd, ctx):\n    pass\n"
    )
    life = _load(tmp_path, body, "ownership_defined")
    assert life.init is not None and life.init(None, None) == 1
    assert life.success is not None and life.finally_ is not None


def test_all_lists_a_deliberate_reexport(tmp_path):
    body = "from shutil import copy as init\n__all__ = ['init']\n"
    life = _load(tmp_path, body, "ownership_all")
    import shutil

    assert life.init is shutil.copy
