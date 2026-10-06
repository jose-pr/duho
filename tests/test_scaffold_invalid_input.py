"""``python -m duho.scaffold`` reports invalid input as a usage error (exit 2)."""

import pytest

from duho import scaffold


@pytest.mark.parametrize(
    "extra",
    [
        ["my-app"],
        ["demo", "--libdir", "../lib"],
        ["demo", "--python", "C:\Python314\python.exe"],
    ],
)
def test_invalid_value_is_a_usage_error_not_a_traceback(tmp_path, capsys, extra):
    with pytest.raises(SystemExit) as exc:
        scaffold.main(extra + ["--root", str(tmp_path)])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "usage:" in err
    assert "Traceback" not in err
    assert not (tmp_path / "bin").exists()


def test_valid_input_still_succeeds(tmp_path):
    assert scaffold.main(["demo", "--root", str(tmp_path)]) == 0
