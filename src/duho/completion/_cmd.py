import typing as _ty

from .. import _compat
from ..args import Cmd as _Cmd


class CompletionCmd(_Cmd):
    """Print a shell completion script for this CLI to stdout."""

    shell: _ty.Literal["bash", "zsh", "fish", "powershell"]
    "Shell to print the completion script for"
    ("shell",)

    # Never an MCP tool: it only writes a script for a human's shell.
    _mcp_ = False

    #: The class whose parser tree the script describes, set by ``duho.main``
    #: when it registers this command; ``None`` under ``duho.app``, which
    #: reads the parser it already built.
    _completion_tree_: "_ty.Optional[type]" = None

    def __call__(self) -> int:
        from . import bash, fish, powershell, zsh

        emitters = {"bash": bash, "zsh": zsh, "fish": fish, "powershell": powershell}
        tree = type(self)._completion_tree_
        if tree is not None:
            parser = tree._parser_()
        else:
            context = _compat._MCP_CONTEXT.get()
            if context is None or context[0] != "app":
                raise RuntimeError(
                    "the completion command needs a running duho.main or "
                    "duho.app tree to describe"
                )
            parser = context[1]
        _compat.write_machine(emitters[self.shell](parser, prog=parser.prog))
        return 0
