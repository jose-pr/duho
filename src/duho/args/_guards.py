from __future__ import annotations

import logging as _logging
import typing as _ty
import weakref as _weakref

_LOGGER = _logging.getLogger("duho.args")

#: Every sandwich-named (``_x_``) class attribute duho reads or defines on a
#: command class. A name that resembles one of these without matching it is
#: reported when the class's parser is first built.
_KNOWN_ATTRS: _ty.FrozenSet[str] = frozenset(
    (
        "_agent_help_",
        "_agent_help_env_",
        "_allow_passthrough_",
        "_argbuilder_",
        "_base_loglevel_",
        "_collection_",
        "_completion_",
        "_completion_command_",
        "_completion_tree_",
        "_config_",
        "_config_env_",
        "_config_field_",
        "_config_loader_",
        "_default_subcommand_",
        "_distribution_",
        "_effective_default_",
        "_env_",
        "_errors_",
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
        "_parser_",
        "_parseraliases_",
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

_CHECKED: _weakref.WeakSet[type] = _weakref.WeakSet()

#: Similarity an unknown attribute needs to be reported: a typo scores above it,
#: a known name with a word added (``_config_dir_``) below.
_ATTR_CUTOFF = 0.85


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
        # Imported here: only a class that has such an attribute pays for it.
        import difflib as _difflib

        close = _difflib.get_close_matches(attr, _KNOWN_ATTRS, n=2, cutoff=_ATTR_CUTOFF)
        if len(close) == 1:
            _LOGGER.warning(
                "%s declares %r, which duho does not read; did you mean %r?",
                cls.__name__,
                attr,
                close[0],
            )


def _claimed_keys(built: object) -> _ty.Set[str]:
    """Metadata keys a built argument accepts: every attribute its class declares."""
    keys: _ty.Set[str] = set()
    for klass in type(built).__mro__:
        try:
            keys.update(getattr(klass, "__annotations__", None) or ())
        except Exception:  # an annotation that cannot be evaluated declares nothing
            continue
    return keys


def _warn_unknown_ns_keys(
    cls: type, field: str, keys: _ty.Iterable[str], built: object
) -> None:
    """Log each ``NS(...)`` key that is neither a ``Meta`` field nor claimed by ``built``."""
    from ._meta import Meta

    known = set(Meta.__dataclass_fields__)
    known.add("split")
    known |= _claimed_keys(built)
    for key in dict.fromkeys(keys):
        if key in known:
            continue
        import difflib as _difflib

        close = _difflib.get_close_matches(key, sorted(Meta.__dataclass_fields__), n=1)
        hint = f"; closest Meta field: {close[0]!r}" if close else ""
        _LOGGER.warning(
            "%s.%s: NS(%s=...) is not a Meta field and is ignored%s",
            cls.__name__,
            field,
            key,
            hint,
        )
