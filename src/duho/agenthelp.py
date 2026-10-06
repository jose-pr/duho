"""Agent-oriented help: a detailed, machine-readable description of a CLI.

Where the normal ``--help`` renders a compact, human-facing usage block, this
module emits a **complete, structured (JSON) description** of a duho CLI --
enough for an AI agent (or any tool) to understand the whole command surface in
one shot: every subcommand, each option with its type/default/required/
repeatable flags, positionals, per-field environment-variable bindings,
mutually-exclusive conflict groups, examples, and exit codes.

**Two triggers, both wired up by ``duho.args``:**

* **The ``AGENT_HELP``/``AGENTS_HELP`` environment variables (always on,
  zero-config; either truthy triggers).** When one is set to a truthy value,
  the ordinary ``-h``/``--help`` action emits this agent description instead
  of the human help. Nothing changes for a normal ``--help`` unless one of
  these env vars is deliberately set, so default human behavior is
  byte-identical. Both defaults are replaced (no aliasing) by an explicit
  ``_agent_help_env_`` class attribute naming a single variable.
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
data, kept out of the ``import duho`` hot path (``duho.args`` imports it lazily,
only when an agent-help trigger actually fires).
"""

from __future__ import annotations

import argparse as _argparse
import contextlib as _contextlib
import enum as _enum
import os as _os
import pathlib as _pathlib
import sys as _sys
import typing as _ty

from . import _compat as _compat
from . import _introspect as _introspect
from . import parsers as _parsers

from .args import Args as _Args

__all__ = [
    "SCHEMA",
    "DEFAULT_ENV",
    "DEFAULT_ENVS",
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

#: Both env var names checked when no ``_agent_help_env_`` override is set
#: (either truthy triggers agent help). An explicit ``_agent_help_env_``
#: replaces both -- no aliasing once a caller names their own variable.
DEFAULT_ENVS = (DEFAULT_ENV, "AGENTS_HELP")

_NOT_DEFINED = _introspect.NOT_DEFINED

_DEFAULT_EXIT_CODES = {
    "0": "Success -- the command returned None or 0.",
    "1": "Runtime error -- the command returned a non-zero code (commonly 1).",
    "2": "Usage error -- argparse rejected the command line "
    "(unknown/missing/invalid arguments).",
}


def _truthy(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() not in _compat.BOOL_FALSE


def agent_help_requested(
    env_name: _ty.Optional[str] = None,
    environ: _ty.Optional[_ty.Mapping[str, str]] = None,
) -> bool:
    """True when a trigger env var is set truthy.

    ``env_name`` explicit (e.g. an app's ``_agent_help_env_`` override) checks
    only that one variable -- no aliasing once a caller names their own.
    ``env_name`` left ``None`` checks every name in :data:`DEFAULT_ENVS`
    (``AGENT_HELP`` and ``AGENTS_HELP``); either truthy triggers agent help.
    ``environ`` defaults to ``os.environ`` (injectable for tests). An unset
    variable is False; a set variable is True unless its stripped/lowercased
    value is one of :data:`duho.text.BOOL_FALSE`.
    """
    environ = _os.environ if environ is None else environ
    if env_name is not None:
        return _truthy(environ.get(env_name))
    return any(_truthy(environ.get(name)) for name in DEFAULT_ENVS)


def _render_type(tp) -> str:
    """One canonical, version-independent spelling for a type, recursively.

    Plain classes render by ``__name__`` (``Color``, not
    ``list[__main__.Color]``) and a union always as ``X | Y``, so a field reads
    the same on every interpreter (3.9 spells a union ``Optional[int]``, 3.14
    ``int | None``; 3.9/3.10 render a bare ``list[str]`` as ``list``).
    """
    if tp is Ellipsis:
        # The variadic marker in `tuple[int, ...]` is the `Ellipsis` singleton, not
        # a type; without this it would stringify as `Ellipsis`.
        return "..."
    origin = _ty.get_origin(tp)
    if origin is None and isinstance(tp, type):
        return tp.__name__
    if origin is None:
        # A bare typing special form with no origin (rare here) -- fall back
        # to its own name, else its str() with the "typing." prefix dropped.
        return getattr(tp, "__name__", None) or str(tp).replace("typing.", "")
    args = _ty.get_args(tp)
    if origin in _compat.UNION_ORIGINS:
        members = [a for a in args if a is not type(None)]
        rendered = " | ".join(_render_type(m) for m in members)
        if len(members) != len(args):
            rendered += " | None"
        return rendered
    if origin is _ty.Literal:
        # A Literal arg is a VALUE, not a type: `repr()` keeps a string member
        # quoted and unambiguous on every interpreter.
        return f"Literal[{', '.join(repr(a) for a in args)}]"
    origin_name = getattr(origin, "__name__", None) or str(origin).replace(
        "typing.", ""
    )
    if not args:
        return origin_name
    return f"{origin_name}[{', '.join(_render_type(a) for a in args)}]"


def _render_annotation(tp):
    """A readable, version-independent type string for a declared annotation
    (never raises) -- e.g. always ``list[int]``, ``int | None``.
    """
    if tp is _NOT_DEFINED or tp is None:
        return None
    return _render_type(tp)


def _jsonable(value, enum_by="name"):
    """Coerce an argparse default to a JSON-serialisable value.

    ``SUPPRESS`` (an inherited-and-suppressed root option on a child parser) and
    duho's ``NOT_DEFINED`` (a required field with no default) both map to
    ``None``. Enums render by member name, Paths by string, and
    list/set/tuple/dict recurse; anything else falls back to ``str()``.
    ``enum_by="value"`` renders an Enum by ``str(member.value)`` instead.
    """
    if value is _argparse.SUPPRESS or value is _NOT_DEFINED:
        return None
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, _enum.Enum):
        return str(value.value) if enum_by == "value" else value.name
    if isinstance(value, _pathlib.PurePath):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v, enum_by) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v, enum_by) for k, v in value.items()}
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


def _enum_members(tp, enum_by="name"):
    """Member names of an Enum annotation (directly or inside a Union), else None.

    ``enum_by="value"`` lists ``str(member.value)`` instead of the names.

    An ``Enum`` field validates by member NAME through a factory + metavar rather
    than argparse ``choices`` (unlike ``Literal``, which sets real choices), so
    its valid values must be recovered from the declared annotation.
    """
    if isinstance(tp, type) and issubclass(tp, _enum.Enum):
        return [_member_text(member, enum_by) for member in tp]
    for arg in _ty.get_args(tp):
        if isinstance(arg, type) and issubclass(arg, _enum.Enum):
            return [_member_text(member, enum_by) for member in arg]
    return None


def _member_text(member, enum_by):
    return str(member.value) if enum_by == "value" else member.name


def _choices(action, decl, enum_by="name"):
    choices = getattr(action, "choices", None)
    if choices:
        return [str(c) for c in choices]
    if decl is not None and decl.type is not _NOT_DEFINED:
        return _enum_members(decl.type, enum_by)
    return None


def _expand_action_help(action, prog: str, *, default=_NOT_DEFINED) -> str:
    """Expand ``%(default)s``-style placeholders in ``action.help`` as argparse
    does in ``--help``, with the ``%%``-unescaped text as the fallback if
    expansion fails (best-effort output; it never raises).

    ``default``, when given, is the already-redacted value from
    :func:`_default_and_source` and overrides ``params["default"]``: otherwise
    ``action.default`` may hold the live env/config value, a secret that would
    leak into help text. ``_NOT_DEFINED`` means "no override".
    """
    text = action.help
    if not text:
        return ""
    params = dict(vars(action), prog=prog)
    if default is not _NOT_DEFINED:
        params["default"] = default
    for key in list(params):
        if params[key] is _argparse.SUPPRESS:
            del params[key]
    for key in list(params):
        if hasattr(params[key], "__name__"):
            params[key] = params[key].__name__
    if params.get("choices") is not None:
        params["choices"] = ", ".join(str(c) for c in params["choices"])
    try:
        return text % params
    except (KeyError, ValueError, TypeError):
        return text.replace("%%", "%")


def _default_and_source(dest, builder, action, sources):
    """``(default, default_source)`` for one field.

    ``action.default`` may hold the live env or config value (a secret), so this
    reports the class-declared default (``builder._effective_default_()``), a
    code-level constant that is never redacted, plus a value-free provenance
    note when the value came from env or config; an ``instance=`` override gets
    no note. A builder-less action (a ``duho.app`` module command) uses what
    :func:`_stash_default_provenance` stashed on it.
    """
    if builder is None:
        if hasattr(action, "_duho_class_default_"):
            return (
                action._duho_class_default_,
                getattr(action, "_duho_default_source_", None),
            )
        # No declaration behind this action: its default may have been
        # computed from the environment, so none is published.
        return None, None
    class_default = _jsonable(
        builder._effective_default_(), getattr(builder, "enum_by", "name")
    )
    source = (sources or {}).get(dest)
    if source == "env":
        env_var = getattr(builder, "env", None)
        return class_default, f"env {env_var}" if env_var else "env"
    if source == "config":
        return class_default, "config"
    return class_default, None


def _describe_option(action, clsargs, builders, prog: str, sources=None):
    dest = action.dest
    builder = builders.get(dest)
    default, default_source = _default_and_source(dest, builder, action, sources)
    info = {
        "names": list(action.option_strings),
        "dest": dest,
        "help": _expand_action_help(action, prog, default=default),
        "type": _type_of(dest, clsargs, action),
        "required": bool(getattr(action, "required", False)),
        "takes_value": action.nargs != 0,
        "repeatable": _repeatable(action, builder),
        "default": default,
        "choices": _choices(
            action, clsargs.get(dest), getattr(builder, "enum_by", "name")
        ),
        "metavar": _metavar(action),
    }
    if default_source is not None:
        info["default_source"] = default_source
    if builder is not None:
        if getattr(builder, "env", None):
            info["env"] = builder.env
        conflicts = getattr(builder, "conflicts", None)
        if conflicts:
            info["conflicts"] = conflicts
    return info


def _describe_positional(action, clsargs, builders, prog: str, sources=None):
    dest = action.dest
    builder = builders.get(dest)
    default, default_source = _default_and_source(dest, builder, action, sources)
    info = {
        "name": dest,
        "help": _expand_action_help(action, prog, default=default),
        "type": _type_of(dest, clsargs, action),
        "nargs": action.nargs,
        "required": action.nargs not in ("?", "*"),
        "repeatable": action.nargs in ("*", "+"),
        "default": default,
        "choices": _choices(
            action, clsargs.get(dest), getattr(builder, "enum_by", "name")
        ),
        "metavar": _metavar(action),
    }
    if default_source is not None:
        info["default_source"] = default_source
    return info


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
    """A minimal invocation line built from a command's required arguments.

    Appends ``<command>`` whenever the spec has subcommands (duho's subparsers
    are always ``required=True``, whatever other options are required), and uses
    the first ``--long`` option string rather than the last-declared one.
    """
    parts = [spec["prog"]]
    for option in spec["options"]:
        if not option["required"]:
            continue
        flag = next((n for n in option["names"] if n.startswith("--")), None)
        if flag is None:
            flag = option["names"][0] if option["names"] else option["dest"]
        if option["takes_value"]:
            parts.append(f"{flag} {option['metavar'] or option['dest'].upper()}")
        else:
            parts.append(flag)
    for positional in spec["positionals"]:
        token = f"<{positional['name']}>"
        parts.append(token if positional["required"] else f"[{positional['name']}]")
    if spec["subcommands"]:
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


@_contextlib.contextmanager
def _muted_color(parser):
    """Temporarily force ``parser.color = False`` while formatting.

    Argparse's native color (3.14+) would otherwise color
    ``parser.format_usage()``, and the agent-help ``usage`` field is parsed by a
    tool. A no-op before 3.14, where ``ArgumentParser`` has no ``color``.
    """
    has_color = hasattr(parser, "color")
    old = parser.color if has_color else None
    if has_color:
        parser.color = False
    try:
        yield
    finally:
        if has_color:
            parser.color = old


def _stash_default_provenance(parser, cls=None) -> None:
    """Snapshot each action's CLASS default (and env/config provenance) onto the
    action, for :class:`duho.formatters.DefaultsFormatter` and
    :func:`describe_parser`'s builder-less fallback.

    ``DefaultsFormatter._get_help_string`` receives only ``action``, so
    ``_AgentHelpAction`` calls this before rendering human help. ``cls``
    defaults to ``parser._duho_cls_``; ``duho.runtime`` passes it for a module
    command's bare subparser, which must not get ``_duho_cls_`` (``duho.mcp``
    reads it). A no-op without a ``cls`` or when the parser was never layered.
    """
    cls = cls if cls is not None else getattr(parser, "_duho_cls_", None)
    if cls is None:
        return
    try:
        builders = {b.name: b for b in cls._getargs_()}
    except Exception:  # pragma: no cover - introspection is best-effort here
        return
    sources = getattr(parser, "_duho_value_sources_", None) or {}
    for action in parser._actions:
        builder = builders.get(action.dest)
        if builder is None:
            continue
        default, source = _default_and_source(action.dest, builder, action, sources)
        action._duho_class_default_ = default  # type: ignore[attr-defined]
        action._duho_default_source_ = source  # type: ignore[attr-defined]


@_contextlib.contextmanager
def _redact_action_defaults(parser, cls=None):
    """Temporarily replace each redacted action's ``.default`` with its class
    default while formatting help, restoring the original on exit.

    argparse's ``%(default)s`` expansion reads ``action.default`` directly,
    bypassing ``_get_help_string`` and every other redaction, so swapping the
    attribute is the only way to keep it from reading the live value. Calls
    :func:`_stash_default_provenance` first (idempotent) and touches only
    actions it stashed onto; ``-h`` and ``--version`` are left alone.
    """
    _stash_default_provenance(parser, cls=cls)
    originals = []
    for action in parser._actions:
        if not hasattr(action, "_duho_class_default_"):
            continue
        originals.append((action, action.default))
        action.default = action._duho_class_default_
    try:
        yield
    finally:
        for action, original in originals:
            action.default = original


class _RedactedHelpAction(_argparse._HelpAction):
    """Plain ``-h``/``--help`` with :func:`_redact_action_defaults` applied
    around the render.

    Installed on a module command's bare stdlib subparser
    (:func:`_install_help_redaction`), which has no ``_AgentHelpAction``. Unlike
    :class:`_AgentHelpAction` it never emits the agent-help JSON; it only keeps a
    live env/config value out of a literal ``%(default)s`` in plain help text.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        with _redact_action_defaults(parser):
            _compat.write_human(parser.format_help(), _sys.stdout)
        parser.exit()


def _install_help_redaction(parser) -> None:
    """Swap every ``_HelpAction`` on ``parser`` to :class:`_RedactedHelpAction`.

    For a module command's subparser (called by ``duho.runtime``'s
    ``_apply_app_config_layers``), whose plain ``-h`` would render a literal
    ``%(default)s`` from the live ``action.default``. Idempotent.
    """
    for action in parser._actions:
        if isinstance(action, _argparse._HelpAction) and not isinstance(
            action, _RedactedHelpAction
        ):
            action.__class__ = _RedactedHelpAction


def describe_parser(
    parser: _argparse.ArgumentParser,
    *,
    root: bool = False,
    root_cls: _ty.Optional[type[_Args]] = None,
    name: _ty.Optional[str] = None,
    aliases: _ty.Optional[_ty.Sequence[str]] = None,
) -> dict:
    """Describe one built ``ArgumentParser`` (and its subtree) as plain data.

    ``root`` adds the document-level keys (schema tag, version, exit codes,
    examples). ``name``/``aliases`` label a subcommand within its parent.
    """
    return _describe_parser(
        parser, root=root, root_cls=root_cls, name=name, aliases=aliases, seen=set()
    )


def _describe_parser(
    parser: _argparse.ArgumentParser,
    *,
    root: bool,
    root_cls: type[_Args] | None,
    name: str | None,
    aliases: _ty.Sequence[str] | None,
    seen: set,
) -> dict:
    """:func:`describe_parser`'s walk; ``seen`` guards against re-describing a
    subparser reached under several (alias) names."""
    builders, clsargs = _cls_metadata(parser)
    cls = getattr(parser, "_duho_cls_", None)
    # A field showing a live env/config value on `action.default` is known only
    # once a parse is underway; ``None`` for a never-parsed parser, whose
    # defaults are already the class defaults.
    sources = getattr(parser, "_duho_value_sources_", None)

    spec = {}
    if root:
        spec["schema"] = SCHEMA
    if name is not None:
        spec["name"] = name
        spec["aliases"] = list(aliases or [])
    spec["prog"] = parser.prog
    # `parser.description` is the raw text, ``%%``-escaped only when it contains
    # a `%(prog)` placeholder (`Args._escape_description`); read it as stored, as
    # unescaping would corrupt a genuine literal `%%`.
    spec["description"] = (parser.description or "").strip()
    if root:
        # Version comes from the app's ROOT class: a subcommand-scoped document
        # would otherwise report `version: null`. `root_cls` is None only for a
        # raw, never-rooted parser, where `cls` is the fallback.
        version = None
        version_cls = root_cls if root_cls is not None else cls
        if version_cls is not None:
            from .args._naming import _resolve_version

            try:
                version = _resolve_version(version_cls)
            except Exception:  # pragma: no cover - version resolution is best-effort
                version = None
        spec["version"] = version
    with _muted_color(parser):
        spec["usage"] = parser.format_usage().strip()

    options = []
    positionals = []
    subparsers_action = _parsers.find_subparsers(parser)
    for action in parser._actions:
        if action is subparsers_action:
            continue
        if action.option_strings:
            options.append(
                _describe_option(action, clsargs, builders, parser.prog, sources)
            )
        else:
            positionals.append(
                _describe_positional(action, clsargs, builders, parser.prog, sources)
            )
    spec["options"] = options
    spec["positionals"] = positionals
    spec["conflicts"] = _conflict_groups(builders)

    subcommands = []
    if subparsers_action is not None:
        # argparse registers aliases as extra keys for the same subparser;
        # `unique_subcommands` groups by identity so each command is described
        # once (`duho.mcp` shares this grouping).
        for canonical, alias_names, subparser in _parsers.unique_subcommands(
            parser, seen=seen
        ):
            subcommands.append(
                _describe_parser(
                    subparser,
                    root=False,
                    root_cls=None,
                    name=canonical,
                    aliases=list(alias_names),
                    seen=seen,
                )
            )
    spec["subcommands"] = subcommands

    if root:
        # Exit codes come from the app's ROOT class, as a subcommand can return
        # them without declaring any; examples stay scoped to `cls`, with a
        # synthesized line when it has no `_examples_`.
        exit_cls = root_cls if root_cls is not None else cls
        spec["exit_codes"] = _exit_codes(exit_cls)
        spec["examples"] = _examples(cls, spec)
    return spec


def describe(cls: type[_Args], argv: _ty.Optional[_ty.Sequence[str]] = None) -> dict:
    """Build ``cls``'s parser and return its agent-help document (a dict).

    Standalone counterpart to the ``--help-agents`` flag / ``AGENT_HELP`` trigger:
    builds the parser tree fresh and describes it, independent of whether either
    trigger is wired up. ``argv`` is accepted for signature symmetry with the
    other entry points and currently unused (the description is static).
    """
    parser = cls._parser_()
    return describe_parser(parser, root=True, root_cls=cls)


def render(spec: dict) -> str:
    """Serialise an agent-help document to a JSON string (trailing newline).

    ``json`` is imported lazily (not at module top) so ``import duho`` never pays
    its import cost -- only actually rendering an agent-help document does. This
    keeps the framework's zero-eager-``json`` contract (see
    ``tests/test_config_json.py::test_json_import_is_lazy``).

    ``ensure_ascii=True``: a non-ASCII character (an arrow in a
    docstring, a Latin-1 accent) written through a piped Windows stdout's
    text layer raises ``UnicodeEncodeError`` (empty output, exit 1) or comes
    out console-code-page-encoded instead of UTF-8. ASCII-escaping every
    non-ASCII character survives any stream encoding and decodes back to the
    identical string, at the cost of a few extra bytes and less readable raw
    JSON -- irrelevant for a document meant to be parsed, not read.
    """
    import json as _json

    return _json.dumps(spec, indent=2, ensure_ascii=True) + "\n"


def print_agent_help(cls: type[_Args], file: _ty.Optional[_ty.TextIO] = None) -> None:
    """Print ``cls``'s agent-help JSON document to ``file`` (default stdout).

    Written via :func:`duho._compat.write_machine`: raw UTF-8
    bytes with LF-only newlines, bypassing ``file``'s text-mode encoding and
    newline translation, the same writer ``duho.args``'s agent-help actions use
    -- so this standalone entry point and the ``-h``/``--help-agents``
    triggers behave identically on any host encoding.
    """
    _compat.write_machine(render(describe(cls)), file)
