"""The launcher overwrite guard holds when only one of the two files exists."""

import pytest

from duho.scaffold import generate_launchers


@pytest.mark.parametrize("existing", ["demo", "demo.cmd"])
def test_a_single_existing_launcher_is_not_overwritten(tmp_path, existing):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / existing).write_text("customised")
    with pytest.raises(FileExistsError):
        generate_launchers("demo", tmp_path)
    assert (bindir / existing).read_text() == "customised"
    other = "demo.cmd" if existing == "demo" else "demo"
    assert not (bindir / other).exists()


@pytest.mark.parametrize("existing", ["demo", "demo.cmd"])
def test_overwrite_replaces_a_single_existing_launcher(tmp_path, existing):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / existing).write_text("customised")
    generate_launchers("demo", tmp_path, overwrite=True)
    assert (bindir / existing).read_text() != "customised"
