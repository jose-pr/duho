"""Agent-oriented help: a detailed, machine-readable description of a CLI.

Where the normal ``--help`` renders a compact, human-facing usage block, this
module emits a **complete, structured (JSON) description** of a duho CLI --
enough for an AI agent (or any tool) to understand the whole command surface in
one shot: every subcommand, each option with its type/default/required/
repeatable flags, positionals, per-field environment-variable bindings,
mutually-exclusive conflict groups, examples, and exit codes.

**Two triggers, both wired up by ``args.py``:**

* **The ``AGENT_HELP`` environment variable (always on, zero-config).** When it
  is set to a truthy value, the ordinary ``-h``/``--help`` action emits this
  agent description instead of the human help. Nothing changes for a normal
  ``--help`` unless that env var is deliberately set, so default human behavior
  is byte-identical. The variable name is overridable per-app via the
  ``_agent_help_env_`` class attribute.
* **An opt-in ``--help-agents`` flag.** Set ``_agent_help_ = True`` on the CLI
  root to add a discoverable flag that always emits the agent description,
  regardless of the environment.

**Built on duho's existing introspection.** The description is produced by
walking the *built* ``argparse`` parser tree (so injected flags -- ``--version``,
``--print-completion`` -- and the whole subcommand tree are included) and
enriching each command with duho's own field metadata (:func:`get_clsargs` /
:class:`ClsArgDeclaration` and the per-field :class:`ArgumentBuilder`) looked up
via the ``_duho_cls_`` handle ``_initparser_`` stashes on every parser. A
subparser with no duho class behind it (e.g. a ``duho.app`` module command) is
still described from its argparse actions alone -- just without the duho-only
metadata (env bindings, conflicts) that has no argparse equivalent.

This mirrors :mod:`duho.completion`: one parser-tree walk that produces plain
data, kept out of the ``import duho`` hot path (``args.py`` imports it lazily,
only when an agent-help trigger actually fires).
"""

import argparse as _argparse
import enum as _enum
import os as _os
import pathlib as _pathlib
import sys as _sys
import typing as _ty

from . import _introspect as _introspect

__all__ = [
    "SCHEMA",
    "DEFAULT_ENV",
    "agent_help_requested",
    "describe",
    "describe_parser",
    "render",
    "print_agent_help",
]

#: Version tag stamped at the top of every agent-help document so a consumer can
#: detect the format and pin to a shape.
SCHEMA = "duho/agent-help@1"

#: Default environment variable that switches ``--help`` into agent mode.
DEFAULT_ENV = "AGENT_HELP"

#: Values that count as "off" when read from the trigger env var (mirrors
#: ``ArgumentBuilder._BOOL_FALSE``). Any other set value counts as "on", so
#: ``AGENT_HELP=1``/``true``/``yes`` -- or any non-empty non-false token -- turns
#: agent help on, while ``AGENT_HELP=0``/``false`` leaves the human help.
_FALSEY = frozenset({"", "0", "false", "no", "off", "n", "f"})

_NOT_DEFINED = _introspect.NOT_DEFINED

_DEFAULT_EXIT_CODES = {
    "0": "Success -- the command returned None or 0.",
    "1": "Runtime error -- the command returned a non-zero code (commonly 1).",
    "2": "Usage error -- argparse rejected the command line "
    "(unknown/missing/invalid arguments).",
}


def agent_help_requested(env_name=None, environ=None):
    """True when the trigger env var (default ``AGENT_HELP``) is set truthy.

    ``env_name`` defaults to :data:`DEFAULT_ENV`; ``environ`` defaults to
    ``os.environ`` (injectable for tests). An unset variable is False; a set
    variable is True unless its stripped/lowercased value is one of
    :data:`_FALSEY`.
    """
    environ = _os.environ if environ is None else environ
    raw = environ.get(env_name or DEFAULT_ENV)
    if raw is None:
        return False
    return raw.strip().lower() not in _FALSEY


def _render_annotation(tp):
    """A readable type string for a declared annotation (never raises)."""
    if tp is _NOT_DEFINED or tp is None:
        return None
    if isinstance(tp, type):
        return tp.__name__
    # e.g. list[int], typing.Optional[str] -> "list[int]", "Optional[str]".
    return str(tp).replace("typing.", "")


def _jsonable(value):
    """Coerce an argparse default to a JSON-serialisable value.

    ``SUPPRESS`` (an inherited-and-suppressed root option on a child parser) and
    duho's ``NOT_DEFINED`` (a required field with no default) both map to
    ``None``. Enums render by member name, Paths by string, and
    list/set/tuple/dict recurse; anything else falls back to ``str()``.
    """
    if value is _argparse.SUPPRESS or value is _NOT_DEFINED:
        return None
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, _enum.Enum):
        return value.name
    if isinstance(value, _pathlib.PurePath):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def _metavar(action):
    metavar = getattr(action, "metavar", None)
    if metavar is None:
        return None
    if isinstance(metavar, (list, tuple)):
        return " ".join(str(m) for m in metavar)
    return str(metavar)


def _repeatable(action, builder):
    """Whether the option may be supplied more than once / accumulates values."""
    if builder is not None and getattr(builder, "collection", None) is not None:
        return True
    if isinstance(action, _argparse._AppendAction):
        return True
    return action.nargs in ("*", "+")


def _type_of(dest, clsargs, action):
    """Readable type string, preferring the declared annotation."""
    decl = clsargs.get(dest)
    if decl is not None:
        name = _render_annotation(decl.type)
        if name:
            return name
    if action.nargs == 0:
        return "bool"
    factory = getattr(action, "type", None)
    if isinstance(factory, type):
        return factory.__name__
    return "str"


def _enum_members(tp):
    """Member names of an Enum annotation (directly or inside a Union), else None.

    An ``Enum`` field validates by member NAME through a factory + metavar rather
    than argparse ``choices`` (unlike ``Literal``, which sets real choices), so
    its valid values must be recovered from the declared annotation.
    """
    if isinstance(tp, type) and issubclass(tp, _enum.Enum):
        return [member.name for member in tp]
    for arg in _ty.get_args(tp):
        if isinstance(arg, type) and issubclass(arg, _enum.Enum):
            return [member.name for member in arg]
    return None


def _choices(action, decl):
    choices = getattr(action, "choices", None)
    if choices:
        return [str(c) for c in choices]
    if decl is not None and decl.type is not _NOT_DEFINED:
        return _enum_members(decl.type)
    return None


def _describe_option(action, clsargs, builders):
    dest = action.dest
    builder = builders.get(dest)
    info = {
        "names": list(action.option_strings),
        "dest": dest,
        "help": action.help or "",
        "type": _type_of(dest, clsargs, action),
        "required": bool(getattr(action, "required", False)),
        "takes_value": action.nargs != 0,
        "repeatable": _repeatable(action, builder),
        "default": _jsonable(action.default),
        "choices": _choices(action, clsargs.get(dest)),
        "metavar": _metavar(action),
    }
    if builder is not None:
        if getattr(builder, "env", None):
            info["env"] = builder.env
        conflicts = getattr(builder, "conflicts", None)
        if conflicts:
            info["conflicts"] = conflicts
    return info


def _describe_positional(action, clsargs, builders):
    dest = action.dest
    return {
        "name": dest,
        "help": action.help or "",
        "type": _type_of(dest, clsargs, action),
        "nargs": action.nargs,
        "required": action.nargs not in ("?", "*"),
        "repeatable": action.nargs in ("*", "+"),
        "default": _jsonable(action.default),
        "choices": _choices(action, clsargs.get(dest)),
        "metavar": _metavar(action),
    }


def _conflict_groups(builders):
    """Mutually-exclusive groups declared via ``NS(conflicts=...)``."""
    groups = {}
    for name, builder in builders.items():
        conflicts = getattr(builder, "conflicts", None)
        if not conflicts:
            continue
        group = groups.setdefault(
            conflicts, {"group": conflicts, "members": [], "required": False}
        )
        group["members"].append(name)
        if getattr(builder, "conflicts_required", False):
            group["required"] = True
    return list(groups.values())


def _synthesized_example(spec):
    """A minimal invocation line built from a command's required arguments."""
    parts = [spec["prog"]]
    for option in spec["options"]:
        if not option["required"]:
            continue
        flag = option["names"][-1] if option["names"] else option["dest"]
        if option["takes_value"]:
            parts.append(f"{flag} {option['metavar'] or option['dest'].upper()}")
        else:
            parts.append(flag)
    for positional in spec["positionals"]:
        token = f"<{positional['name']}>"
        parts.append(token if positional["required"] else f"[{positional['name']}]")
    if spec["subcommands"] and not any(o["required"] for o in spec["options"]):
        parts.append("<command>")
    return " ".join(parts)


def _examples(root_cls, spec):
    """Author-declared ``_examples_`` if present, else one synthesized line.

    ``_examples_`` may be a sequence of strings, or of ``(command, description)``
    pairs.
    """
    declared = getattr(root_cls, "_examples_", None) if root_cls is not None else None
    if declared:
        out = []
        for example in declared:
            if isinstance(example, (list, tuple)) and len(example) == 2:
                out.append({"command": str(example[0]), "description": str(example[1])})
            else:
                out.append({"command": str(example), "description": ""})
        return out
    return [
        {
            "command": _synthesized_example(spec),
            "description": "Minimal invocation (required arguments only).",
        }
    ]


def _exit_codes(root_cls):
    codes = dict(_DEFAULT_EXIT_CODES)
    override = getattr(root_cls, "_exit_codes_", None) if root_cls is not None else None
    if override:
        codes.update({str(k): str(v) for k, v in dict(override).items()})
    return codes


def _cls_metadata(parser):
    """``({dest: ArgumentBuilder}, {name: ClsArgDeclaration})`` for a parser.

    Read from the duho class ``_initparser_`` stashed on the parser as
    ``_duho_cls_``. Returns empty maps when there is no duho class behind the
    parser (a bare argparse parser, or a ``duho.app`` module command).
    """
    cls = getattr(parser, "_duho_cls_", None)
    if cls is None:
        return {}, {}
    try:
        builders = {b.name: b for b in cls._getargs_()}
        clsargs = _introspect.get_clsargs(cls)
    except Exception:  # pragma: no cover - introspection is best-effort here
        return {}, {}
    return builders, clsargs


def describe_parser(parser, *, root=False, root_cls=None, name=None, aliases=None, _seen=None):
    """Describe one built ``ArgumentParser`` (and its subtree) as plain data.

    ``root`` adds the document-level keys (schema tag, version, exit codes,
    examples). ``name``/``aliases`` label a subcommand within its parent.
    ``_seen`` guards against re-describing a subparser reached under multiple
    (alias) names.
    """
    if _seen is None:
        _seen = set()
    builders, clsargs = _cls_metadata(parser)
    cls = getattr(parser, "_duho_cls_", None)

    spec = {}
    if root:
        spec["schema"] = SCHEMA
    if name is not None:
        spec["name"] = name
        spec["aliases"] = list(aliases or [])
    spec["prog"] = parser.prog
    # The stored description is ``%%``-escaped for argparse's own %-expansion
    # (see ``Args._parser_``); un-double it for the raw agent document.
    spec["description"] = (parser.description or "").replace("%%", "%").strip()
    if root:
        version = None
        if cls is not None:
            from .args import _resolve_version

            try:
                version = _resolve_version(cls)
            except Exception:  # pragma: no cover - version resolution is best-effort
                version = None
        spec["version"] = version
    spec["usage"] = parser.format_usage().strip()

    options = []
    positionals = []
    subparsers_action = None
    for action in parser._actions:
        if isinstance(action, _argparse._SubParsersAction):
            subparsers_action = action
            continue
        if action.option_strings:
            options.append(_describe_option(action, clsargs, builders))
        else:
            positionals.append(_describe_positional(action, clsargs, builders))
    spec["options"] = options
    spec["positionals"] = positionals
    spec["conflicts"] = _conflict_groups(builders)

    subcommands = []
    if subparsers_action is not None:
        # argparse registers alias names as extra keys pointing at the SAME
        # subparser object; group by identity so each command is described once.
        grouped = {}
        order = []
        for choice_name, subparser in (subparsers_action.choices or {}).items():
            key = id(subparser)
            if key not in grouped:
                grouped[key] = {"parser": subparser, "names": []}
                order.append(key)
            grouped[key]["names"].append(choice_name)
        for key in order:
            if key in _seen:
                continue
            _seen.add(key)
            subparser = grouped[key]["parser"]
            names = grouped[key]["names"]
            sub_cls = getattr(subparser, "_duho_cls_", None)
            canonical = getattr(sub_cls, "_parsername_", None) if sub_cls else None
            if canonical not in names:
                canonical = names[0]
            alias_names = [n for n in names if n != canonical]
            subcommands.append(
                describe_parser(
                    subparser,
                    root=False,
                    name=canonical,
                    aliases=alias_names,
                    _seen=_seen,
                )
            )
    spec["subcommands"] = subcommands

    if root:
        target_cls = root_cls if root_cls is not None else cls
        spec["exit_codes"] = _exit_codes(target_cls)
        spec["examples"] = _examples(target_cls, spec)
    return spec


def describe(cls, argv=None):
    """Build ``cls``'s parser and return its agent-help document (a dict).

    Standalone counterpart to the ``--help-agents`` flag / ``AGENT_HELP`` trigger:
    builds the parser tree fresh and describes it, independent of whether either
    trigger is wired up. ``argv`` is accepted for signature symmetry with the
    other entry points and currently unused (the description is static).
    """
    parser = cls._parser_()
    return describe_parser(parser, root=True, root_cls=cls)


def render(spec):
    """Serialise an agent-help document to a JSON string (trailing newline).

    ``json`` is imported lazily (not at module top) so ``import duho`` never pays
    its import cost -- only actually rendering an agent-help document does. This
    keeps the framework's zero-eager-``json`` contract (see
    ``tests/test_config_json.py::test_json_import_is_lazy``).
    """
    import json as _json

    return _json.dumps(spec, indent=2, ensure_ascii=False) + "\n"


def print_agent_help(cls, file=None):
    """Print ``cls``'s agent-help JSON document to ``file`` (default stdout)."""
    if file is None:
        file = _sys.stdout
    file.write(render(describe(cls)))
