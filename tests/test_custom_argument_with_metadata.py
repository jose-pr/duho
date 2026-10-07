"""Test: a custom `Argument` type's own `_argbuilder_` must
still run when the field is wrapped in `Arg[CustomType, Meta(...)]`/
`Meta(...)`.

A field with Annotated metadata must not be routed through
`Argument.from_type(decl.type, **options)`, whose `super()._argbuilder_` is
the plain `Argument` protocol default -- not the custom type's own override.
The custom type would become a bare, unresolved `type=` factory instead of
using its own parsing logic, and adding `help=`/`env=`/... to a custom type
would silently break it.
"""

import duho
from duho import Arg, Args, Argument, ArgumentBuilder, Meta


class Reversed(Argument):
    """A custom Argument type: reverses the input text."""

    @classmethod
    def _argbuilder_(cls, name, decl, factory=None):
        def _factory(text: str) -> str:
            return text[::-1]

        return ArgumentBuilder(
            name=name,
            flags=(f"--{name}",),
            type=_factory,
            default=decl.default,
            help=decl.docstring or "",
            metavar="REVERSED",
        )


def test_bare_custom_argument_type_uses_its_own_builder():
    class Bare(Args):
        token: Reversed = "unused"
        "A reversed token"

    result = duho.parse(Bare, ["--token", "abc"])
    assert result.token == "cba"


def test_custom_argument_type_wrapped_in_meta_help_still_uses_its_own_builder():
    """Wrapping in Arg[..., Meta(help=...)] must not bypass Reversed's
    own `_argbuilder_` and use `str(text)` instead."""

    class WithHelp(Args):
        token: "Arg[Reversed, Meta(help='the token')]" = "unused"

    result = duho.parse(WithHelp, ["--token", "abc"])
    assert result.token == "cba"

    parser = WithHelp._parser_()
    text = parser.format_help()
    assert "the token" in text
    assert "REVERSED" in text


def test_custom_argument_type_wrapped_in_env_metadata_still_works(monkeypatch):
    class WithEnv(Args):
        token: "Arg[Reversed, Meta(env='PROBE_TOKEN')]" = "unused"

    monkeypatch.setenv("PROBE_TOKEN", "abc")
    result = duho.parse(WithEnv, [])
    assert result.token == "cba"
