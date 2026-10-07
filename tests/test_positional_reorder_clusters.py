"""Clustered short flags between positionals parse like the same flags apart."""

import pytest

import duho
from duho import Arg, Args, Meta


class Cluster(Args):
    first: str
    ("first",)

    files: "list[str]" = []
    ("files",)

    verbose: "Arg[int, Meta(action='count', flags=('-v',))]" = 0

    all_: bool = False
    ("-a",)

    out: str = ""
    ("-o",)


@pytest.mark.parametrize(
    "argv, verbose, all_, out, files",
    [
        (["x", "-vv", "a", "b"], 2, False, "", ["a", "b"]),
        (["x", "-va", "a", "b"], 1, True, "", ["a", "b"]),
        (["x", "-vao", "f", "a", "b"], 1, True, "f", ["a", "b"]),
        (["x", "-vofile", "a", "b"], 1, False, "file", ["a", "b"]),
        (["x", "-vv", "a"], 2, False, "", ["a"]),
    ],
)
def test_cluster_between_positionals(argv, verbose, all_, out, files):
    result = duho.parse(Cluster, argv)
    assert result.first == "x"
    assert result.verbose == verbose
    assert result.all_ is all_
    assert result.out == out
    assert result.files == files


def test_unknown_member_of_a_cluster_still_errors():
    with pytest.raises(SystemExit):
        duho.parse(Cluster, ["x", "-vz", "a", "b"])
