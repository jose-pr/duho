import difflib as _difflib
import logging as _logging
import typing as _ty
import weakref as _weakref

_LOGGER = _logging.getLogger("duho.args")

#: Every sandwich-named (``_x_``) class attribute duho reads or defines on a
#: command class. A name that resembles one of these without matching it is
#: reported when the class's parser is first built.
_KNOWN_ATTRS: "_ty.FrozenSet[str]" = frozenset(
    (
        "_agent_help_",
        "_agent_help_env_",
        "_allow_passthrough_",
        "_argbuilder_",
        "_collection_",
        "_completion_",
        "_config_",
        "_config_loader_",
        "_default_subcommand_",
        "_distribution_",
        "_effective_default_",
        "_env_",
        "_examples_",
        "_exit_codes_",
        "_getargs_",
        "_help_formatter_",
        "_implicit_nargs_",
        "_initparser_",
        "_logger_",
        "_logger_name_",
        "_mcp_",
        "_mcp_command_",
        "_parseraliases_",
        "_parser_",
        "_parsername_",
        "_passthrough_",
        "_register_subcmd_",
        "_runpath_dir_",
        "_runpath_logger_",
        "_set_loglevels_",
        "_subcommands_",
        "_utf8_stdio_",
        "_verbose_loglevel_",
        "_version_",
    )
)

_CHECKED: "_weakref.WeakSet[type]" = _weakref.WeakSet()


def _warn_misspelled_attrs(cls: type) -> None:
    """Log, once per class, each sandwich attribute that nearly matches a known one."""
    if cls in _CHECKED:
        return
    _CHECKED.add(cls)
    for attr in list(vars(cls)):
        if (
            len(attr) < 3
            or attr.startswith("__")
            or attr.endswith("__")
            or not (attr.startswith("_") and attr.endswith("_"))
            or attr.startswith("_duho_")
            or attr in _KNOWN_ATTRS
        ):
            continue
        close = _difflib.get_close_matches(attr, _KNOWN_ATTRS, n=2, cutoff=0.8)
        if len(close) == 1:
            _LOGGER.warning(
                "%s declares %r, which duho does not read; did you mean %r?",
                cls.__name__,
                attr,
                close[0],
            )
