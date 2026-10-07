from __future__ import annotations

import argparse as _argparse
import copy as _copy
import sys as _sys
import threading as _threading
import typing as _ty
import weakref as _weakref

from .. import _introspect as _introspect
from .._layers import _finalize_layers as _finalize_layers
from .._layers import _merge_layers_upward as _merge_layers_upward
from .._layers import _stage_layers as _stage_layers

from ._actions import (
    _COMPLETION_SHELLS,
    _PrintCompletionAction,
    _Utf8SafeVersionAction,
    _install_agent_help,
)
from ._argument import Argument, ArgumentBuilder, _apply_argument_options
from ._guards import _warn_misspelled_attrs, _warn_unknown_dict_keys
from ._helptext import _escape_description, _escape_stray_percent
from ._meta import Meta, NOT_DEFINED
from ._naming import _app_name, _command_name, _resolve_version
from ._parserfix import (
    _has_variadic_positional,
    _insert_default_subcommand,
    _join_literal_values,
    _literal_value_flags,
    _keep_attached_double_dash,
    _reorder_argv_for_variadic_positional,
    _suppress_inherited_defaults,
)

from ._meta import _Parser, _Self

#: {id(instance): frozenset(field names passed)} for every `Args` built via
#: `__init__`. Seeded defaults must not count as caller-supplied when
#: `duho.parse(instance)` decides what outranks env/config. Keyed by `id()`
#: because `Namespace` defines `__eq__` without `__hash__`; `weakref.finalize`
#: drops the entry. Kept out of `vars(instance)`, which would leak into the
#: clone pattern, equality and repr.
_duho_explicit_instance_fields: dict[int, frozenset] = {}

#: {id(instance): {field: value}} that `Args.__init__` seeded for fields the
#: caller did not pass; `duho.parse(instance)` counts a field as set once its
#: value differs. Kept and cleaned up like the dict above.
_duho_seeded_instance_values: dict[int, dict[str, object]] = {}

#: {id(instance): parser} for `duho.value_sources`. Per instance because the
#: class-level `_duho_last_parser_` is overwritten by the next parse of the
#: same class; `value_sources` falls back to it only for an untracked instance.
_duho_instance_last_parser_: dict[int, object] = {}


#: Ids of the classes currently being built in this thread's `_parser_()` call
#: tree; each build removes its own id when it finishes.
_building_stack = _threading.local()


def _guard_recursive_build(cls):
    """Raise ``TypeError`` if `cls` is already being built higher up in this
    `_parser_()` call, else record it and return the set to remove it from.

    A subcommand that subclasses its own root would otherwise recurse until a
    bare ``RecursionError``.
    """
    ids = getattr(_building_stack, "ids", None)
    if ids is None:
        ids = set()
        _building_stack.ids = ids
    if id(cls) in ids:
        raise TypeError(
            f"{cls.__name__!r} appears in its own _subcommands_ tree "
            f"(directly, or via a shared/inherited _subcommands_ list that "
            f"was not meant to include it); declare _subcommands_ only on "
            f"the class that should own it, or exclude {cls.__name__!r} "
            f"explicitly"
        )
    ids.add(id(cls))
    return ids


def _drop_sidecars(ns: dict) -> None:
    """Remove the collection-action sidecars and the command marker from ``ns``.

    They would otherwise reach ``vars(instance)`` and the clone pattern.
    """
    for key in [
        k
        for k in ns
        if k.startswith("_duho_items_") or k.startswith("_duho_dict_seen_")
    ]:
        del ns[key]
    ns.pop("_duho_command_", None)


def _add_fields(
    parser: _argparse.ArgumentParser,
    cls: type,
    exclusive_groups: dict | None = None,
    *,
    parent_dests: _ty.FrozenSet[str] | None = None,
    strict: bool = True,
    config_hint: bool = False,
) -> dict:
    """Add ``cls``'s declared fields to ``parser``, titled and exclusive groups included.

    Shared by :meth:`Args._initparser_` and
    ``duho.runtime._add_module_declared_fields``. Group requiredness is computed
    first because argparse fixes ``required`` at group creation. ``strict=True``
    raises on a dest collision unless it is a ``parents=`` share (``arg.name in
    parent_dests``); ``strict=False`` skips a colliding dest silently. Returns
    ``exclusive_groups``, merged onto ``parser.exclusive_groups``.
    """
    parent_dests = parent_dests if parent_dests is not None else frozenset()
    exclusive_groups = exclusive_groups or {}

    # A mutex group is required when ANY member sets Meta(conflicts_required=True);
    # groups are created on their first member, so compute it up front.
    required_by_conflicts: dict[str, bool] = {}
    # A `conflicts=` key must use the same `group=` title everywhere, or its
    # members land in two mutex groups and silently stop excluding each other.
    conflicts_titles: dict[str, object] = {}
    for arg in cls._getargs_():
        conflicts = arg.conflicts
        if conflicts:
            required_by_conflicts[conflicts] = required_by_conflicts.get(
                conflicts, False
            ) or bool(arg.conflicts_required)
            group_title = arg.group
            if conflicts in conflicts_titles:
                if conflicts_titles[conflicts] != group_title:
                    raise ValueError(
                        f"argument {arg.name!r}: conflicts={conflicts!r} is "
                        f"declared with group={group_title!r} here but "
                        f"group={conflicts_titles[conflicts]!r} elsewhere; "
                        f"every field sharing a conflicts= key must use the "
                        f"same group= (or none)"
                    )
            else:
                conflicts_titles[conflicts] = group_title

    # A bool that a layer other than the CLI can set True needs a way back off on
    # the command line (`ArgumentBuilder._kwargs`'s `layered`). `env=` is per
    # field; a config source is per class: `cls._config_`, or `config_hint` (a
    # root's `_config_`, or a `config=` kwarg known only at the call site).
    _has_config_source = getattr(cls, "_config_", None) is not None or config_hint

    # Titled argument groups (Meta(group="...")), created lazily per title.
    # Persisted on the parser so a parents=[...] merge / subclass override can
    # reuse them, mirroring `exclusive_groups`.
    titled_groups: dict[str, object] = (
        getattr(parser, "_duho_titled_groups_", None) or {}
    )

    actions_by_dest = {action.dest: action for action in parser._actions}
    for arg in cls._getargs_():
        _existing_action = actions_by_dest.get(arg.name)
        if _existing_action:
            if strict:
                if arg.name in parent_dests:
                    # A genuine `parents=[...]` merge: the CALLER deliberately
                    # shares this global option with the parent (e.g. a
                    # subcommand inheriting `-v`/`-q`) -- reuse it silently.
                    continue
                # Anything else sharing this dest was added by duho for this
                # class (`-h`, `--version`, `--print-completion`); dropping the
                # user's field would lose its value and flags, so fail loud.
                raise ValueError(
                    f"field {arg.name!r} on {cls.__name__} collides with a "
                    f"dest that {cls.__name__}'s own parser already uses "
                    f"(-h/--help, --version, or --print-completion each "
                    f"reserve their field name); rename the field (a "
                    f"field's dest is always its name -- there is no "
                    f"dest= override; Meta(kwargs={{'dest': ...}}) is the raw "
                    f"add_argument escape hatch if you truly need one)"
                )
            # Not strict (a module command's own declared fields): a dest
            # already present -- whether inherited from the root or added by
            # duho itself -- is skipped rather than re-added or conflicted.
            continue

        conflicts = arg.conflicts
        group_title = arg.group

        # The container the field's argument is added to: a titled group when
        # Meta(group=...) is set, else the parser itself.
        if group_title is not None:
            if group_title not in titled_groups:
                titled_groups[group_title] = parser.add_argument_group(group_title)
            container = titled_groups[group_title]
        else:
            container = parser

        if conflicts:
            # A conflicts= member goes in a mutually-exclusive group, nested in
            # its titled group when group= is also set (key `(group, conflicts)`),
            # else top-level keyed by `conflicts` (`parser.exclusive_groups["type"]`).
            key = (group_title, conflicts) if group_title is not None else conflicts
            if key not in exclusive_groups:
                exclusive_groups[key] = container.add_mutually_exclusive_group(
                    required=required_by_conflicts.get(conflicts, False)
                )
            group = exclusive_groups[key]
        else:
            group = container

        layered = _has_config_source or bool(getattr(arg, "env", None))
        arg.add_to_parser(group, layered=layered)

    # Expose the groups so a `_parser_` override can add options to a
    # `conflicts=` group; merge so a `parents=` parser keeps its own.
    existing = getattr(parser, "exclusive_groups", None)
    if existing:
        existing.update(exclusive_groups)
    else:
        parser.exclusive_groups = exclusive_groups
    parser._duho_titled_groups_ = titled_groups  # type: ignore[attr-defined]
    return exclusive_groups


class Args(_argparse.Namespace):
    """Base class for a duho command's declared fields.

    Subclass this and declare CLI fields as annotated, non-underscore class
    attributes. Everything duho reads or sets is sandwich-named (``_x_``) or a
    dunder, so the field namespace stays user-owned; only ``help``,
    ``version`` and ``print_completion``, which would clash with a flag duho
    adds, are rejected as field names (see ``docs/guide/arguments.md``). An
    optional flags tuple and a trailing docstring string customize each
    field's ``add_argument`` call. ``Args`` itself is data-only -- it defines
    no ``__call__`` (:func:`duho.main`/:func:`duho.run_command` raise a clear
    ``NotImplementedError`` for a bare ``Args``); subclass :class:`Cmd`
    instead for a RUNNABLE command, or :class:`Cli` for an app root.

    ``Args`` subclasses ``argparse.Namespace``, whose stub declares
    ``__getattr__(self, name) -> Any`` -- so a type checker does NOT catch a
    typo'd field read (``self.regoin`` on a class with a ``region`` field);
    only a real run surfaces it, as an ``AttributeError``.
    """

    #: Annotation-only: lets a type checker see any `Cmd` subclass as a
    #: `duho.discovery.Command` (whose Protocol needs this member). A `ClassVar`
    #: with no value never reaches `vars(cls)`.
    _parsername_: _ty.ClassVar[str]

    #: Pre-seeded empty cache, so building a user parser never AST-parses duho's
    #: own field-less base classes. A subclass gets its own entry from the normal
    #: scan; `presets.LoggingArgs` declares fields and is deliberately not seeded.
    _duho_constants_: dict = {}

    def __init__(self, /, **kwargs: object) -> None:
        # Seed each declared field's effective default when absent, so a direct
        # instance has the attribute surface of a parsed one (e.g. a `store_true`
        # bool). Passed kwargs win; a required field (NOT_DEFINED) stays unset.
        # Not `super().__init__(**kwargs)`: a field named `self` would collide.
        super().__init__()
        for key, value in kwargs.items():
            setattr(self, key, value)
        if "_passthrough_" not in vars(self):
            self._passthrough_ = []
        seeded: dict[str, object] = {}
        for builder in type(self)._getargs_():
            name = builder.name
            # `vars(self)`, not `hasattr`: a class-level default is `hasattr`-true
            # by inheritance, so the instance would share, and mutations would
            # leak through, the class attribute.
            if name in kwargs or name in vars(self):
                continue
            default = builder._effective_default_()
            if default is not NOT_DEFINED:
                setattr(self, name, default)
                seeded[name] = _copy.copy(default)
        # Record which fields THIS CALL passed, outside vars(self). A class that
        # is not weak-referenceable is untracked, and `duho.parse(instance)`
        # then treats every attribute as explicit.
        try:
            _key = id(self)
            _duho_explicit_instance_fields[_key] = frozenset(kwargs)
            _duho_seeded_instance_values[_key] = seeded
            _weakref.finalize(self, _duho_explicit_instance_fields.pop, _key, None)
            _weakref.finalize(self, _duho_seeded_instance_values.pop, _key, None)
        except TypeError:
            pass

    @classmethod
    def _getargs_(cls) -> list[ArgumentBuilder]:
        """This class's own declared fields, as one :class:`ArgumentBuilder`
        per field, in declaration order.

        Built once from ``_introspect.get_clsargs(cls)`` (each field's type
        routed through :meth:`Argument.from_type`/its own ``_argbuilder_``,
        then any ``Meta(...)`` metadata applied) and cached on
        ``cls`` (``_duho_builders_``) -- override to add/hide/reorder fields
        programmatically (call ``super()._getargs_()`` and adjust the list).
        """
        if "_duho_builders_" in vars(cls):
            return cls._duho_builders_

        clsargs = _introspect.get_clsargs(cls)
        args: list[ArgumentBuilder] = []
        for name, decl in clsargs.items():
            if decl.annotations:
                # SUPPRESS anywhere in the metadata hides the field.
                if _argparse.SUPPRESS in decl.annotations:
                    continue
                options: dict = {}
                dict_keys: list[str] = []
                for opts in decl.annotations:
                    # Consumed: a Meta, a Mapping, a PEP 727 `.documentation` str
                    # as help, or an object with a __dict__ read as options.
                    # Anything else (a bare `Annotated[int, "doc"]` string) is ignored.
                    if isinstance(opts, Meta):
                        # Typed metadata: merge only the explicitly-set fields so
                        # an unset (sentinel) field never overrides a type-derived
                        # kwarg.
                        options.update(opts._duho_options_())
                    elif isinstance(opts, _ty.Mapping):
                        options.update(opts)
                        dict_keys.extend(opts)
                    elif isinstance(getattr(opts, "documentation", None), str):
                        options.setdefault("help", opts.documentation)
                    elif hasattr(opts, "__dict__"):
                        options.update(vars(opts))
                        if isinstance(opts, _argparse.Namespace):
                            dict_keys.extend(vars(opts))
                if isinstance(decl.type, Argument):
                    # A custom Argument type in Arg[...]/Meta(...) must be built by
                    # its own `_argbuilder_` (`from_type` would use the plain
                    # protocol default), then get the same options applied.
                    built = decl.type._argbuilder_(name, decl)
                    _apply_argument_options(built, options)
                else:
                    built = Argument.from_type(decl.type, **options)._argbuilder_(
                        name, decl
                    )
                if dict_keys:
                    _warn_unknown_dict_keys(cls, name, dict_keys, built)
            elif isinstance(decl.type, Argument):
                built = decl.type._argbuilder_(name, decl)
            else:
                built = Argument.from_type(decl.type)._argbuilder_(name, decl)
            args.append(built)

        setattr(cls, "_duho_builders_", args)
        return args

    @classmethod
    def _parser_(
        cls,
        subparser: _ty.Optional[_argparse._SubParsersAction] = None,
        name: _ty.Optional[str] = None,  # type: ignore
        parents: _ty.Sequence[_argparse.ArgumentParser] = (),
        _inherited_formatter_class_: _ty.Optional[type] = None,
        _inherited_agent_root_cls_: _ty.Optional[type] = None,
        _inherited_config_hint_: _ty.Optional[bool] = None,
        _skip_subcommands_: bool = False,
        **kwargs: _ty.Any,
    ) -> _Parser[_Self]:
        """Build (or attach) this class's ``argparse.ArgumentParser``.

        With ``subparser`` (a ``_SubParsersAction``) it attaches a sub-parser via
        ``subparser.add_parser(name or _command_name(cls), parents=parents,
        **kwargs)``; omitted, it builds a top-level ``ArgumentParser``. Then
        :meth:`_initparser_` adds the fields and the ``_subcommands_`` tree.
        Override to customize parser construction (e.g. ``formatter_class``);
        override :meth:`_initparser_` for parser-level configuration.
        """
        _warn_misspelled_attrs(cls)
        if subparser:
            method = subparser.add_parser
        else:
            method = _argparse.ArgumentParser

        # Do not write the name back onto the class: `getattr` follows the MRO,
        # so subclasses would inherit it (siblings collapse; 3.11+ raises
        # "conflicting subparser"). `_command_name` reads only a `_parsername_`
        # in `vars(cls)`.
        name_given = name is not None
        name: str = name or (_command_name(cls) if subparser else _app_name(cls))
        # Passed on to `_initparser_`, which accepts it for compatibility with
        # subclasses that override it; nothing here depends on its value.
        explicit_prog = name_given or bool(vars(cls).get("_parsername_"))
        if not subparser and "prog" in kwargs:
            # `ArgumentParser(name, ...)` would fill `prog` positionally and
            # collide with a caller's `prog=`, which names the real program and
            # wins. (A subparser's `name` is a different parameter.)
            name = kwargs.pop("prog")
            explicit_prog = True
        # `help=` is always %-expanded, so `%` must be escaped; `description=`
        # only with a real `%(prog)` placeholder, so it is escaped selectively.
        _doc = cls.__doc__ or ""
        kwargs.setdefault("description", _escape_description(_doc))
        # Opt-in `_help_formatter_`; a caller's `formatter_class=` wins. A class
        # with none inherits the nearest ancestor's, so the tree is styled alike.
        own_formatter = getattr(cls, "_help_formatter_", None)
        effective_formatter = (
            own_formatter if own_formatter is not None else _inherited_formatter_class_
        )
        if effective_formatter is not None:
            kwargs.setdefault("formatter_class", effective_formatter)
        # The app's root class, threaded down the `_subcommands_` tree; `None`
        # means this call is the top, so `cls` is the root. Used by
        # `_install_agent_help`.
        agent_root_cls = (
            _inherited_agent_root_cls_
            if _inherited_agent_root_cls_ is not None
            else cls
        )
        # Threaded down like `agent_root_cls`: a caller that will pass `config=`
        # records it so every node can react (`_initparser_`'s `external_config`).
        external_config = bool(_inherited_config_hint_)
        if subparser:
            kwargs.setdefault(
                "help",
                (
                    _escape_stray_percent(_doc.strip().splitlines()[0])
                    if _doc.strip()
                    else ""
                ),
            )
            # Subcommand aliases (argparse's add_parser accepts `aliases`; the
            # top-level ArgumentParser does not, so only apply when nested).
            aliases = getattr(cls, "_parseraliases_", None)
            if aliases:
                kwargs.setdefault(
                    "aliases", [a for a in dict.fromkeys(aliases) if a != name]
                )

        # Guard against `cls` already being built higher up in this call tree.
        _build_ids = _guard_recursive_build(cls)
        try:
            # Dests the framework adds to this parser, so a same-named user
            # field collides loudly; dests from `parents=` are deliberate reuse.
            parent_dests = frozenset(
                a.dest for p in parents for a in getattr(p, "_actions", ())
            )
            parser = _ty.cast(
                "_Parser[_Self]",
                method(name, parents=list(parents), **kwargs),
            )

            cls._initparser_(
                parser,
                is_subcommand=bool(subparser),
                parent_dests=parent_dests,
                explicit_prog=explicit_prog,
                agent_root_cls=agent_root_cls,
                external_config=external_config,
            )

            # The subcommand dest is private, so a root field named `command`
            # is never clobbered.
            subcommands = getattr(cls, "_subcommands_", None)
            if subparser is not None and "_subcommands_" not in vars(cls):
                # An inherited list may name a class being built right now (a
                # subcommand that subclasses its own root); leave that one out.
                subcommands = [s for s in subcommands or () if id(s) not in _build_ids]
            if (
                not subcommands
                and not _skip_subcommands_
                and vars(cls).get("_default_subcommand_") is not None
            ):
                raise ValueError(
                    f"{cls.__name__}._default_subcommand_ = "
                    f"{cls._default_subcommand_!r} but the class has no subcommands"
                )
            if subcommands and not _skip_subcommands_:
                subparsers = parser.add_subparsers(dest="_duho_command_", required=True)
                # Kebab-cased names (`FooBar`, `Foo_Bar` -> `foo-bar`) and
                # aliases can collide with a sibling's. Catch it here: argparse
                # would misroute (3.9/3.10) or raise an error naming no duho
                # class (3.11+). A class listed twice registers once.
                subcommands = list(dict.fromkeys(subcommands))
                sibling_names = [_command_name(sub) for sub in subcommands]
                seen_names: dict[str, object] = {}
                for sibling, sibling_name in zip(subcommands, sibling_names):
                    own_names = [sibling_name] + list(
                        getattr(sibling, "_parseraliases_", None) or ()
                    )
                    for own_name in own_names:
                        prior = seen_names.get(own_name)
                        if prior is not None and prior is not sibling:
                            raise ValueError(
                                f"subcommand name {own_name!r} is used by both "
                                f"{prior.__name__!r} and {sibling.__name__!r} -- "
                                "declare an explicit _parsername_ on one of them "
                                "to disambiguate"
                            )
                        seen_names[own_name] = sibling
                # argparse's required/invalid-choice errors fall back to the
                # action's dest when `metavar` is unset; set it so the private
                # `_duho_command_` dest never shows.
                subparsers.metavar = "{" + ",".join(sibling_names) + "}"
                default_subcommand = getattr(cls, "_default_subcommand_", None)
                if (
                    default_subcommand is not None
                    and default_subcommand not in seen_names
                ):
                    raise ValueError(
                        f"{cls.__name__}._default_subcommand_ = "
                        f"{default_subcommand!r} is not one of its subcommands "
                        f"({', '.join(seen_names)})"
                    )
                # Dests this root declares: an option given BEFORE the subcommand
                # parses into them, and a child re-declaring the field would
                # clobber that with its default. Suppress the child's default
                # for those shared optional dests.
                root_builders = {b.name: b for b in cls._getargs_()}
                root_dests = set(root_builders)
                root_defaults = {
                    n: b._effective_default_() for n, b in root_builders.items()
                }
                # A config table reaches every subcommand's slice, so propagate
                # the hint: from `config=`, or this class's own `_config_`.
                propagated_config_hint = external_config or (
                    getattr(cls, "_config_", None) is not None
                )
                for sub in subcommands:
                    child = sub._parser_(
                        subparsers,
                        _inherited_formatter_class_=effective_formatter,
                        _inherited_agent_root_cls_=agent_root_cls,
                        _inherited_config_hint_=propagated_config_hint,
                    )
                    # Link child -> parent so `_merge_layers_upward` folds
                    # provenance up the selected chain only.
                    child._duho_parent_parser_ = parser  # type: ignore[attr-defined]
                    _suppress_inherited_defaults(child, root_dests, root_defaults)
        finally:
            _build_ids.discard(id(cls))

        return parser

    @classmethod
    def _initparser_(
        cls,
        parser: _argparse.ArgumentParser,
        is_subcommand: bool = False,
        parent_dests: _ty.Optional[_ty.FrozenSet[str]] = None,
        explicit_prog: bool = False,
        agent_root_cls: _ty.Optional[type] = None,
        external_config: bool = False,
    ) -> _argparse.ArgumentParser:
        """Populate an already-created ``parser`` with this class's own fields.

        Called by :meth:`_parser_`: adds each field (with its groups), patches
        ``parse_known_args`` to build the final ``Args``/``Cmd`` instance, and on
        a root with ``_completion_`` injects ``--print-completion``. Override
        for parser-level configuration (call ``super()._initparser_`` first,
        accept ``**kwargs`` and forward them: ``_parser_`` may pass more than
        the signature lists). ``explicit_prog`` is accepted and unused.
        ``external_config`` says a config table will reach this class although
        it declares no ``_config_`` (see :func:`_add_fields`).
        """
        parent_dests = parent_dests if parent_dests is not None else frozenset()

        def parse_known_args(
            args: _ty.Sequence[str] | None = None,
            namespace: _argparse.Namespace | None = None,
        ):
            if namespace is None:
                namespace = _argparse.Namespace()

            setattr(namespace, "#cls", cls)

            # `_passthrough_`: argv after the FIRST `--`. Only the top-level
            # parse splits: a subparser gets an already-sliced list and
            # namespace=None.
            passthrough: list[str] | None = None
            if not is_subcommand:
                if args is None:
                    argv = _sys.argv[1:]
                else:
                    argv = list(args)
                literal_flags = _literal_value_flags(parser)
                if literal_flags:
                    argv = _join_literal_values(argv, literal_flags)
                if "--" in argv:
                    idx = argv.index("--")
                    passthrough = argv[idx + 1 :]
                    argv = argv[:idx]
                args = argv

            default_subcommand = getattr(cls, "_default_subcommand_", None)
            if default_subcommand and args is not None:
                args = _insert_default_subcommand(
                    parser, list(args), default_subcommand
                )

            # A flag between two positional groups, one variable-arity, breaks
            # argparse's greedy positional matching (bpo-15112): move recognized
            # flags to the front. Bails unreordered when unsure, so a typo still
            # gets argparse's own error.
            if args is not None and _has_variadic_positional(parser):
                args = _reorder_argv_for_variadic_positional(parser, list(args))

            # Install env/config/instance placeholders as unconverted defaults,
            # lazily, so a subcommand this run never reaches is never resolved.
            _stage_layers(parser, cls)

            parsed, unk = _argparse.ArgumentParser.parse_known_args(
                parser, args, namespace
            )

            # Convert untouched placeholders, report a bad one through
            # `parser.error`, and merge provenance up into the parent's.
            _finalize_layers(parser, cls, parsed)
            _merge_layers_upward(parser)

            # Per-parse copy of mutable defaults, for THIS parser's actions only.
            for _pa in parser._actions:
                _dest = _pa.dest
                if _dest is None or _dest is _argparse.SUPPRESS:
                    continue
                _default = _pa.default
                if isinstance(_default, (list, set, dict)) and (
                    getattr(parsed, _dest, None) is _default
                ):
                    # argparse puts the SAME default object on every namespace a
                    # reused parser produces; copy it per parse so parses never
                    # share a list/set/dict.
                    setattr(parsed, _dest, _copy.copy(_default))

            if is_subcommand:
                # argparse's _SubParsersAction copies vars(result) onto the
                # parent namespace, so keep "#cls" in the dict: the deepest
                # selection is parsed last and wins. Only the top-level call pops
                # it and builds the instance.
                return parsed, unk

            _cls: type[_Self] = parsed.__dict__.pop("#cls")
            _drop_sidecars(parsed.__dict__)
            if passthrough and not getattr(_cls, "_allow_passthrough_", True):
                parser.error(
                    f"{_command_name(_cls)}: arguments after '--' are not accepted"
                )
            instance = _cls(**parsed.__dict__)
            # Attach captured passthrough (empty list when no `--` was seen).
            instance._passthrough_ = passthrough if passthrough is not None else []
            # For duho.value_sources(): `parser` is still the ROOT's and holds
            # the chain merged by `_merge_layers_upward`, though `_cls` is now
            # the deepest selection. Per class, so Args instances stay free of
            # bookkeeping in vars().
            _cls._duho_last_parser_ = parser  # type: ignore[attr-defined]
            # Also record it per-INSTANCE (see `_duho_instance_last_parser_`)
            # so parsing this same class again later doesn't retroactively
            # change what an EARLIER instance's `value_sources()` reports.
            try:
                _pkey = id(instance)
                _duho_instance_last_parser_[_pkey] = parser
                _weakref.finalize(
                    instance, _duho_instance_last_parser_.pop, _pkey, None
                )
            except TypeError:
                pass
            return instance, unk

        parser.parse_known_args = parse_known_args  # type: ignore
        _keep_attached_double_dash(parser)

        def _unsupported_intermixed_args(*_a, **_kw):
            # 3.9 re-enters our patched `parse_known_args` and crashes on the
            # built instance; 3.14 calls `_parse_known_args2` and returns a bare
            # Namespace. Neither is supported: fail loud instead.
            raise NotImplementedError(
                "duho parsers do not support parse_intermixed_args()/"
                "parse_known_intermixed_args() (argparse bypasses duho's own "
                "instance-construction hook for interleaved positionals on "
                "at least one supported Python version); use duho.parse(cls, "
                "argv) instead"
            )

        parser.parse_known_intermixed_args = _unsupported_intermixed_args  # type: ignore

        version = _resolve_version(cls)
        actions_by_dest_pre = {action.dest: action for action in parser._actions}
        if version and "version" not in actions_by_dest_pre:
            parser.add_argument(
                "--version",
                # `%` must be escaped: argparse %-formats `version=`.
                # `_Utf8SafeVersionAction` survives a non-UTF-8 stdout.
                action=_Utf8SafeVersionAction,
                version="%(prog)s " + version.replace("%", "%%"),
            )

        # Opt-in via `_completion_`; top-level only, since a subcommand's parser
        # sees only its own subtree. Skipped if the dest exists (`parents=`).
        if not is_subcommand and getattr(cls, "_completion_", False):
            actions_by_dest_pre2 = {action.dest: action for action in parser._actions}
            if "print_completion" not in actions_by_dest_pre2:
                parser.add_argument(
                    "--print-completion",
                    # Shared with `print_completion()`'s validation.
                    choices=_COMPLETION_SHELLS,
                    action=_PrintCompletionAction,
                    root_parser=parser,
                    dest="print_completion",
                    help="Print a shell completion script for the given shell and exit.",
                )

        # Shared with `runtime._add_module_declared_fields`, so a module command
        # gets the same group support.
        _add_fields(
            parser,
            cls,
            parent_dests=parent_dests,
            strict=True,
            config_hint=external_config,
        )

        # Agent help: stash the class for the emitter, make --help env-aware, and
        # add the opt-in --help-agents flag. See `_install_agent_help`.
        _install_agent_help(parser, cls, is_subcommand, agent_root_cls=agent_root_cls)

        return parser
