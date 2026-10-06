import argparse as _argparse
import copy as _copy
import dataclasses as _dataclasses
import logging as _logging
import os as _os
import pathlib as _pathlib
import re as _re
import sys as _sys
import threading as _threading
import typing as _ty
import weakref as _weakref
from typing import Annotated as Arg

from .. import _compat as _compat
from .. import _introspect as _introspect
from .. import logging as _duho_logging
from .._fieldspec import Factory as Factory
from .._introspect import ClsArgDeclaration as ClsArgDeclaration
from .._fieldspec import UpdateAction as UpdateAction
from .._fieldspec import _AppendAction as _AppendAction
from .._fieldspec import _bool_from_text as _bool_from_text
from .._fieldspec import _choice_checked as _choice_checked
from .._fieldspec import _factory_for as _factory_for
from .._fieldspec import _ISOFORMAT_FACTORIES as _ISOFORMAT_FACTORIES
from .._fieldspec import _LayeredChoiceError as _LayeredChoiceError
from .._fieldspec import _NegatedBoolAction as _NegatedBoolAction
from .._layers import _apply_default_layers_one as _apply_default_layers_one
from .._layers import _apply_layers as _apply_layers
from .._layers import _finalize_layers as _finalize_layers
from .._layers import _merge_layers_upward as _merge_layers_upward
from .._layers import _raw_config_values as _raw_config_values
from .._layers import _raw_env_values as _raw_env_values
from .._layers import _resolve_config_dict as _resolve_config_dict
from .._layers import _stage_layers as _stage_layers
from .._layers import _stash_layer_state as _stash_layer_state
from .._layers import value_sources as value_sources
from ..text import kebabcase as _kebabcase

_LOGGER = _logging.getLogger(__package__)

from ._meta import (
    NOT_DEFINED,
    _NONETYPE,
    _type,
    _T,
    _A,
    _C,
    NS,
    _AutoVersion,
    AUTO,
    _MetaUnset,
    _META_UNSET,
    Meta,
    Extend,
    Count,
    Append,
    Const,
    Choice,
)
from ._naming import (
    _top_level_dist_name,
    _AUTO_VERSION_CACHE,
    _resolve_auto_version,
    _resolve_version,
    _command_name,
    _app_name,
    _default_long_flag,
    _expand_flag_shorthand,
)
from ._helptext import (
    _PERCENT_PLACEHOLDER,
    _escape_stray_percent,
    _escape_help,
    _escape_description,
    _write_machine_text,
)
from ._actions import (
    _COMPLETION_SHELLS,
    _Utf8SafeVersionAction,
    _PrintCompletionAction,
    _AgentHelpAction,
    _AgentHelpFlagAction,
    _install_agent_help,
    print_completion,
    print_agent_help,
)
from ._argument import (
    ArgumentMeta,
    Argument,
    _apply_argument_options,
    _TYPE_INCOMPATIBLE_ACTIONS,
    _CONST_REQUIRED_ACTIONS,
    _ZERO_ARG_ACTION_DEFAULTS,
    _is_positional,
    ArgumentBuilder,
)
from ._parserfix import (
    _VARIADIC_NARGS,
    _has_variadic_positional,
    _keep_attached_double_dash,
    _patch_parser_for_reorder,
    _reorder_argv_for_variadic_positional,
    _suppress_inherited_defaults,
)
from ._argsclass import (
    _duho_explicit_instance_fields,
    _duho_instance_last_parser_,
    _building_stack,
    _guard_recursive_build,
    _add_fields,
    Args,
)
from ._cmd import (
    Cmd,
    Cli,
    command,
    subcommand,
)
from ._mcptrigger import (
    _MCP_NAME_ALLOWED,
    _mcp_env_var_name,
    _maybe_serve_mcp_trigger,
)
from ._entry import (
    _logger_name_for,
    _setup_instance_logging,
    _maybe_await,
    main,
    parse,
    parse_globals,
    finish_parse,
)

if _ty.TYPE_CHECKING:
    from ._meta import _Parser, _Self

__all__ = [
    "Append",
    "Argument",
    "ArgumentBuilder",
    "ArgumentMeta",
    "Args",
    "Arg",
    "AUTO",
    "Choice",
    "ClsArgDeclaration",
    "Cli",
    "Cmd",
    "command",
    "Const",
    "Count",
    "Extend",
    "Factory",
    "finish_parse",
    "main",
    "Meta",
    "NS",
    "NOT_DEFINED",
    "parse",
    "parse_globals",
    "print_agent_help",
    "print_completion",
    "subcommand",
    "UpdateAction",
    "value_sources",
]
