"""``_allow_passthrough_ = False`` makes a ``--`` tail a usage error."""

import pytest

import duho


class Plain(duho.Cmd):
    name: str = "x"

    def __call__(self):
        return 0


class Strict(duho.Cmd):
    _allow_passthrough_ = False
    name: str = "x"

    def __call__(self):
        return 0


class Child(duho.Cmd):
    _allow_passthrough_ = False

    def __call__(self):
        return 0


class Other(duho.Cmd):
    def __call__(self):
        return 0


class Root(duho.Cli):
    _subcommands_ = [Child, Other]


def test_default_still_accepts_a_tail():
    inst = duho.parse(Plain, ["--name", "a", "--", "x", "-y"])
    assert inst._passthrough_ == ["x", "-y"]
    assert duho.parse(Plain, ["--name", "a"])._passthrough_ == []


def test_false_accepts_input_without_a_tail():
    assert duho.parse(Strict, ["--name", "a"])._passthrough_ == []


def test_false_rejects_a_tail_with_exit_2(capsys):
    with pytest.raises(SystemExit) as exc:
        duho.parse(Strict, ["--name", "a", "--", "x"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "Strict" in err or "strict" in err
    assert "--" in err


def test_subcommand_attribute_applies_only_to_that_subcommand(capsys):
    assert duho.parse(Root, ["other", "--", "a"])._passthrough_ == ["a"]
    with pytest.raises(SystemExit) as exc:
        duho.parse(Root, ["child", "--", "a"])
    assert exc.value.code == 2
    assert "child" in capsys.readouterr().err
