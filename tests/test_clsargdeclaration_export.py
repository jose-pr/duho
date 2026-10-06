"""`ClsArgDeclaration`, named by the public `Argument._argbuilder_` and
`mcp.json_schema_for_field` signatures, is exported from `duho.args`."""

import duho.args
from duho import _introspect


def test_exported_from_duho_args():
    assert "ClsArgDeclaration" in duho.args.__all__
    assert duho.args.ClsArgDeclaration is _introspect.ClsArgDeclaration


def test_a_custom_argbuilder_can_annotate_its_decl():
    from duho import Argument, ArgumentBuilder
    from duho.args import ClsArgDeclaration

    class Tagged:
        @classmethod
        def _argbuilder_(cls, name: str, decl: ClsArgDeclaration, factory=None):
            return ArgumentBuilder(
                name=name,
                flags=(f"--{name}",),
                type=str,
                default=decl.default,
                help=decl.docstring,
            )

    assert isinstance(Tagged, Argument)
