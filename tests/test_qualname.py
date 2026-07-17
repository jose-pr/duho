"""Tests for duho.qualname (QualName algebra, PythonName)."""

import pathlib

import pytest

from duho.qualname import PythonName


class TestParts:
    def test_parts(self):
        assert list(PythonName("a.b.c").parts) == ["a", "b", "c"]

    def test_empty_name_has_no_parts(self):
        assert list(PythonName("").parts) == []

    def test_name(self):
        assert PythonName("a.b.c").name == "c"

    def test_parent(self):
        assert PythonName("a.b.c").parent == "a.b"

    def test_single_part_parent_is_empty(self):
        assert PythonName("a").parent == ""


class TestTruediv:
    def test_append_str(self):
        assert PythonName("a.b") / "c" == "a.b.c"

    def test_parent_of_joined(self):
        assert (PythonName("a.b") / "c").parent == "a.b"


class TestRelativeTo:
    def test_relative_to_prefix(self):
        assert PythonName("a.b.c").relative_to(PythonName("a.b")) == "c"

    def test_relative_to_deep_prefix(self):
        assert PythonName("a.b.c.d").relative_to(PythonName("a.b")) == "c.d"

    def test_relative_to_non_prefix_raises(self):
        with pytest.raises(ValueError):
            PythonName("a.b.c").relative_to(PythonName("x.y"))

    def test_relative_to_longer_raises(self):
        with pytest.raises(ValueError):
            PythonName("a.b").relative_to(PythonName("a.b.c"))


class TestAsPath:
    def test_as_path_is_pure_posix(self):
        p = PythonName("a.b.c").as_path()
        assert isinstance(p, pathlib.PurePosixPath)
        assert p == pathlib.PurePosixPath("/a/b/c")

    def test_as_path_custom_root(self):
        p = PythonName("a.b").as_path("root")
        assert p == pathlib.PurePosixPath("root/a/b")


class TestWithName:
    def test_with_name(self):
        assert PythonName("a.b.c").with_name("z") == "a.b.z"


class TestNew:
    def test_new_sanitizes(self):
        assert PythonName.new("a-b", "class") == "a_b.class_"

    def test_new_no_sanitize(self):
        assert PythonName.new("a.b", "c", sanitize=False) == "a.b.c"

    def test_new_joins_qualnames(self):
        assert PythonName.new(PythonName("a.b"), "c") == "a.b.c"


class TestCamelCase:
    def test_camelcase_full(self):
        assert PythonName("some.name.here").camelcase() == "SomeNameHere"

    def test_camelcase_slice(self):
        assert PythonName("a.b.c").camelcase(start=1) == "BC"
