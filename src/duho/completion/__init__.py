"""Shell completion script generation (bash/zsh/fish/powershell).

**Decision (do not revisit): STATIC script generation** -- these functions
emit a self-contained completion script the user installs once, NOT a
dynamic argcomplete-style hook that re-invokes the program on every Tab.
Zero runtime dependency, zero per-invocation cost: a core differentiator
vs. argcomplete.

All four emitters (`bash`, `zsh`, `fish`, `powershell`) share one parser-tree
walk (`_walk`) that turns a *built* `argparse.ArgumentParser` into a plain,
shell-agnostic `CompletionSpec`. Only the emitters know shell syntax.

Completion data is read off the built parser's private attrs
(`parser._actions`, `parser._subparsers`) -- the same internal contract
`parsers.py` already relies on elsewhere in this codebase.

**Two-level quoting.** A value (a choice, a subcommand name, a flag) can pass
through TWO parses: the STATIC parse when the shell first reads/sources the
generated script, and -- for zsh's `_arguments` action lists and fish's
`complete -a`/`-n` arguments -- a SECOND, dynamic evaluation every time the
user presses Tab. A quoter that only survives the first parse (bash's
`_bash_wordlist`, zsh/fish's old shared `_sq`) does not protect the second.
Every site that reaches a second evaluation therefore escapes each value for
that evaluation FIRST (`_zsh_word` / the fish-word escaper below), then wraps
the result for the static parse (`_sq` / `_fsq`).
"""

from __future__ import annotations

import argparse as _argparse
import dataclasses as _dc
import hashlib as _hashlib
import pathlib as _pathlib
import shlex as _shlex

from .. import parsers as _parsers

from ._quoting import (
    _bashq,
    _bash_wordlist,
    _sq,
    _ZSH_WORD_SAFE,
    _zsh_word,
    _FISH_WORD_SAFE,
    _fish_word,
    _fsq,
    _psq,
    _utf16_units,
    _validate_prog,
)
from ._spec import (
    CompletionOption,
    CompletionPositional,
    CompletionSpec,
    _is_path_type,
    _enum_choices,
    _drop_nul_choices,
    _choices_tuple,
    _takes_value,
    _walk,
    spec,
    _all_specs,
    _cmd_key,
    _value_flag_names,
    _flag_names,
    _func_name,
)
from ._bash import (
    _bash_func_name,
    bash,
)
from ._zsh import (
    _zsh_value_part,
    _zsh_optspec,
    _zsh_pos_spec,
    _zsh_seg,
    _zsh_root_func_name,
    _zsh_funcid,
    zsh,
)
from ._fish import (
    _fish_func_name,
    _fish_path_resolver,
    _fish_condition,
    fish,
)
from ._powershell import (
    powershell,
)

__all__ = [
    "CompletionOption",
    "CompletionPositional",
    "CompletionSpec",
    "spec",
    "bash",
    "zsh",
    "fish",
    "powershell",
]
