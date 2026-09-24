"""Tests for ``duho.mcp.describe_tools``.

Walks a fixture app's built parser tree the same way ``duho.agenthelp`` does
(alias-dedup-by-identity) and turns every node -- root included -- into one
MCP tool spec, namespaced ``parent.child`` when nested.

Fixtures at module level: AST-based flags/docstring introspection needs a real
source file (same convention as ``test_agenthelp.py``).
"""

from duho import Cli, Cmd, LoggingArgs
from duho.mcp import describe_tools


class Deploy(Cmd):
    """Deploy the app to a target."""

    _parseraliases_ = ["d", "dep"]

    environment: str
    "Target environment"
    ("--env",)

    def __call__(self):  # pragma: no cover
        return 0


class Rollback(Cmd):
    """Roll back the last deploy."""

    def __call__(self):  # pragma: no cover
        return 0


class App(LoggingArgs, Cli):
    """My multi-command app."""

    _version_ = "1.2.3"
    _subcommands_ = [Deploy, Rollback]


class Flat(Cmd):
    """A single command with no subcommands."""

    name: str = "world"
    "Who to greet"
    ("--name",)

    def __call__(self):  # pragma: no cover
        return 0


def _by_name(tools):
    return {t["name"]: t for t in tools}


# --------------------------------------------------------------------------
# Names / namespacing
# --------------------------------------------------------------------------


def test_every_node_gets_a_tool_namespaced_parent_child():
    tools = describe_tools(App)
    names = {t["name"] for t in tools}
    # "App" itself is a namespace: it always requires a subcommand, so it can
    # never be dispatched and is not listed as a callable tool.
    assert names == {"App.Deploy", "App.Rollback"}


def test_flat_app_with_no_subcommands_has_one_tool():
    tools = describe_tools(Flat)
    assert [t["name"] for t in tools] == ["Flat"]


def test_aliases_do_not_produce_duplicate_tools():
    tools = describe_tools(App)
    # Deploy has aliases "d"/"dep" registered on the same subparser object;
    # alias-dedup-by-identity must yield exactly one "App.Deploy" tool, never
    # "App.d"/"App.dep" as separate entries.
    names = [t["name"] for t in tools]
    assert names.count("App.Deploy") == 1
    assert "App.d" not in names
    assert "App.dep" not in names


# --------------------------------------------------------------------------
# Spec shape
# --------------------------------------------------------------------------


def test_tool_spec_has_name_description_input_schema():
    tools = _by_name(describe_tools(App))
    deploy = tools["App.Deploy"]
    assert deploy["description"] == "Deploy the app to a target."
    assert deploy["inputSchema"]["type"] == "object"
    assert "environment" in deploy["inputSchema"]["properties"]
    assert "environment" in deploy["inputSchema"]["required"]


def test_namespace_root_is_not_listed_but_its_fields_reach_children():
    tools = _by_name(describe_tools(App))
    assert "App" not in tools
    # A namespace root (its own subcommand is mandatory) is not itself a
    # callable tool, but its own fields -- here LoggingArgs' verbose/quiet/
    # loglevels -- must still be reachable: they are merged into every
    # descendant's own schema, since MCP has no separate way to call the
    # root first and supply them.
    deploy = tools["App.Deploy"]
    assert "verbose" in deploy["inputSchema"]["properties"]
    assert "environment" in deploy["inputSchema"]["properties"]


def test_leaf_tool_with_no_fields_of_its_own_inherits_ancestor_fields():
    tools = _by_name(describe_tools(App))
    rollback = tools["App.Rollback"]
    # Rollback declares no fields of its own, but its namespace ancestor
    # App's own (LoggingArgs) fields are merged in.
    assert set(rollback["inputSchema"]["properties"]) == {
        "verbose",
        "quiet",
        "loglevels",
    }
    assert rollback["inputSchema"]["required"] == []


# --------------------------------------------------------------------------
# Conflict groups -> description note (Decision 6)
# --------------------------------------------------------------------------


def test_conflict_groups_noted_in_description():
    from duho import Arg, NS

    class Compressed(Cmd):
        """Compress output."""

        gzip: Arg[bool, NS(conflicts="compression")] = False
        "gzip"
        ("--gzip",)

        zstd: Arg[bool, NS(conflicts="compression")] = False
        "zstd"
        ("--zstd",)

        def __call__(self):  # pragma: no cover
            return 0

    class Root(Cli):
        """Root."""

        _subcommands_ = [Compressed]

    tools = _by_name(describe_tools(Root))
    assert "Mutually exclusive" in tools["Root.Compressed"]["description"]
    assert "gzip" in tools["Root.Compressed"]["description"]
