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
from ._guards import _warn_misspelled_attrs, _warn_unknown_ns_keys
from ._helptext import _escape_description, _escape_help
from ._meta import Meta, NOT_DEFINED, NS
from ._naming import _app_name, _command_name, _resolve_version
from ._parserfix import (
    _has_variadic_positional,
    _keep_attached_double_dash,
    _reorder_argv_for_variadic_positional,
    _suppress_inherited_defaults,
)

if _ty.TYPE_CHECKING:
    from ._meta import _Parser, _Self


#: {id(instance): frozenset(explicitly-passed field names)} for every `Args`
#: instance built via `__init__`. `Args.__init__` seeds a class-body
#: default for every declared field the caller did NOT pass (so a directly
#: built instance has the same attribute surface as a parsed one, e.g. a bare
#: `bool` field materializes to `False`) -- those seeded placeholders must
#: never be mistaken for a value the caller actually supplied when
#: `duho.parse(instance)` decides what outranks env/config.
#:
#: Keyed by `id()`, not the instance itself: `argparse.Namespace` defines
#: `__eq__` (value equality over `vars()`) without a matching `__hash__`, so
#: Python makes every `Namespace`/`Args` instance UNHASHABLE by default --
#: a plain `WeakKeyDictionary` (which hashes its keys) cannot hold one.
#: `id()` is always hashable; `weakref.finalize` below drops the entry once
#: the instance is actually collected, so this never outlives it. Storing the
#: set here (rather than as a real attribute) also keeps it OUT of
#: `vars(instance)`, which would otherwise leak into the documented
#: `type(self)(**self._get_kwargs())` clone pattern and instance equality/repr.
_duho_explicit_instance_fields: "dict[int, frozenset]" = {}

#: {id(instance): {field: value}} -- what `Args.__init__` seeded for each field
#: the caller did not pass. `duho.parse(instance)` counts a seeded field as set
#: once its current value differs from this record. Kept out of `vars(instance)`
#: and cleaned up like `_duho_explicit_instance_fields`.
_duho_seeded_instance_values: "dict[int, dict[str, object]]" = {}

#: {id(instance): parser} recording which parser produced THIS specific
#: instance, for `duho.value_sources`. Kept OUT of `vars(instance)` for the
#: same reason as `_duho_explicit_instance_fields` above (id()-keyed,
#: `weakref.finalize`-cleaned -- see that constant's docstring). This is the
#: per-INSTANCE complement to `Args._duho_last_parser_` (set alongside it,
#: below): the class-level attribute alone means parsing the SAME class
#: twice (two different config files, say) makes the OLDER instance's
#: `value_sources()` silently report the NEWER parse's provenance once the
#: class attribute is overwritten. `value_sources` prefers this dict and
#: only falls back to the class attribute for an instance that predates this
#: fix, or one whose class isn't weak-referenceable.
_duho_instance_last_parser_: "dict[int, object]" = {}


#: Thread-local guard against a class appearing in its own (possibly
#: inherited) ``_subcommands_`` tree -- directly, or via a subcommand that
#: subclasses its own root. Keyed by `id(cls)` so it works for any
#: class regardless of hashability; cleared as each class finishes building
#: (`finally`), so it only ever reflects classes CURRENTLY being built in
#: this thread's current `_parser_()` call tree, never a class built by an
#: earlier, already-finished call.
_building_stack = _threading.local()


def _guard_recursive_build(cls):
    """Raise a clear ``TypeError`` if `cls` is already being built higher up
    in the current `_parser_()` call, else record it and return the
    set to remove it from when this build finishes.

    Without this, a subcommand that subclasses its own root (directly, or by
    inheriting a shared `_subcommands_` list that was never meant to include
    it) recurses into building itself as its own child forever, failing with
    a bare, unhelpful ``RecursionError``.
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


def _add_fields(
    parser: "_argparse.ArgumentParser",
    cls: type,
    exclusive_groups: "dict | None" = None,
    *,
    parent_dests: "_ty.FrozenSet[str] | None" = None,
    strict: bool = True,
    config_hint: bool = False,
) -> "dict":
    """Add ``cls``'s own declared fields to ``parser`` (titled/mutually-
    exclusive groups included).

    The one field/group wiring routine shared by :meth:`Args._initparser_`
    (class commands) and ``duho.runtime._add_module_declared_fields`` (module
    commands) -- previously duplicated by hand, which is why a module command
    could not use ``NS(conflicts=...)``/``NS(group=...)``: that support lived
    only in ``_initparser_``'s own copy. Iterates ``cls._getargs_()``,
    resolves each field's titled group (``NS(group=...)``) and
    mutually-exclusive group (``NS(conflicts=...)``, precomputing group
    requiredness first so argparse -- which fixes ``required`` at
    group-creation time -- sees the right value on the first member), then
    calls the field's own ``ArgumentBuilder.add_to_parser``.

    ``strict=True`` (the ``_initparser_`` case) raises on an unexpected dest
    collision unless it is an explicit ``parents=[...]`` share (``arg.name in
    parent_dests``). ``strict=False`` (the module-command case) silently
    skips any colliding dest instead, matching a module command's existing
    "never re-adds/conflicts with an inherited global" contract.

    Returns the ``exclusive_groups`` dict (created fresh when not given),
    already merged onto ``parser.exclusive_groups``; ``parser``'s titled-group
    state (``_duho_titled_groups_``) is updated in place too.
    """
    parent_dests = parent_dests if parent_dests is not None else frozenset()
    exclusive_groups = exclusive_groups or {}

    # A mutually-exclusive group is *required* when ANY of its members
    # declares NS(conflicts_required=True). Groups are created lazily on the
    # first member, so pre-compute requiredness across all members here and
    # pass it at creation.
    required_by_conflicts: "dict[str, bool]" = {}
    # A `conflicts=` key must use the SAME `group=` title (or no title)
    # everywhere it appears -- otherwise members that share a conflicts=
    # string land in TWO separate mutex groups (one per title) and are no
    # longer mutually exclusive at all, silently.
    conflicts_titles: "dict[str, object]" = {}
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

    # A bool field that can receive True from a layer OTHER than the
    # CLI needs a way to turn it back off from the command line (see
    # `ArgumentBuilder._kwargs`'s `layered` parameter). `env=` is a
    # per-field signal; a config source is a per-CLASS one -- either `cls`'s
    # own declared `_config_`, or `config_hint` (threaded down from
    # `_parser_`/`_initparser_`): a class built as part of a `_subcommands_`
    # tree whose ROOT has `_config_` (its own table cascades to every
    # subcommand's slice, see `_stash_layer_state`), or one `duho.main`/
    # `duho.parse`/`duho.parse_globals` is about to call with an explicit
    # `config=` kwarg -- something no class attribute alone could reveal,
    # since that kwarg is only known at the CALL site, after the parser is
    # already built.
    _has_config_source = getattr(cls, "_config_", None) is not None or config_hint

    # Titled argument groups (NS(group="...")), created lazily per title.
    # Persisted on the parser so a parents=[...] merge / subclass override can
    # reuse them, mirroring `exclusive_groups`.
    titled_groups: "dict[str, object]" = (
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
                    # subcommand inheriting `-v`/`-q`) -- reuse it silently,
                    # same as before.
                    continue
                # Anything else sharing this dest was added by duho ITSELF,
                # moments ago, for this same class (`-h`/`--help`,
                # `--version`, `--print-completion`) -- silently dropping the
                # user's field here (the previous behavior) lost both its
                # value and its declared flags with no error at all,
                # contradicting the documented "no reserved field names"
                # policy. Fail loud at build time instead.
                raise ValueError(
                    f"field {arg.name!r} on {cls.__name__} collides with a "
                    f"dest that {cls.__name__}'s own parser already uses "
                    f"(-h/--help, --version, or --print-completion each "
                    f"reserve their field name); rename the field (a "
                    f"field's dest is always its name -- there is no "
                    f"dest= override; NS(kwargs={{'dest': ...}}) is the raw "
                    f"add_argument escape hatch if you truly need one)"
                )
            # Not strict (a module command's own declared fields): a dest
            # already present -- whether inherited from the root or added by
            # duho itself -- is skipped rather than re-added or conflicted.
            continue

        conflicts = arg.conflicts
        group_title = arg.group

        # The container the field's argument is added to: a titled group when
        # NS(group=...) is set, else the parser itself.
        if group_title is not None:
            if group_title not in titled_groups:
                titled_groups[group_title] = parser.add_argument_group(group_title)
            container = titled_groups[group_title]
        else:
            container = parser

        if conflicts:
            # A conflicts= member lives in a mutually-exclusive group. When it
            # ALSO declares group=, nest the exclusive group inside the titled
            # group (argparse supports it), keyed by (group, conflicts);
            # otherwise it's a top-level group keyed by conflicts (so
            # `parser.exclusive_groups["type"]` keeps working).
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

    # Expose the built mutually-exclusive groups on the parser so a subclass
    # `_parser_` override can add extra options into a `conflicts=`-built
    # group (e.g. a short-flag alias that must stay mutually exclusive with a
    # declared field). Merge rather than overwrite so a parents=[...] parser
    # that already carries groups keeps them.
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
    attributes (see the "Naming & Collision Policy" -- every OTHER member is
    sandwich-named or a dunder, so the field namespace stays user-owned); an
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

    #: Annotation-only: declares the shape (a class command's subcommand name)
    #: WITHOUT creating a runtime class/instance attribute -- ``_parsername_``
    #: is set per-subclass, dynamically (``duho.command(...)``'s generated
    #: class body, or a user's own ``_parsername_ = "..."``), never here. A
    #: ``ClassVar`` annotation with no assigned value never appears in
    #: ``vars(cls)``, so it changes no reader; it exists purely so a type
    #: checker considers ANY ``Cmd`` subclass a structural match for
    #: ``duho.discovery.Command`` (whose ``Protocol`` requires this member) --
    #: without it, the documented ``app(commands=[SomeCmd])``/
    #: ``run_command(SomeCmd, ...)`` failed type-checking even though they run
    #: correctly. Already excluded from CLI-field discovery like every
    #: other leading-underscore name, ``ClassVar`` or not.
    _parsername_: "_ty.ClassVar[str]"

    #: Pre-seeded empty class-body-constants cache. ``_class_constants``
    #: (``_introspect``) short-circuits on ``"_duho_constants_" in vars(cls)``,
    #: so seeding it here means building ANY user parser never AST-parses
    #: duho's own ``args.py`` to scan these framework base classes for
    #: class-body flag/env/docstring metadata -- they declare none. This is
    #: safe ONLY because ``Args``/``Cmd``/``Cli`` carry no real CLI-field
    #: declarations in their bodies. A subclass that DOES declare fields gets
    #: its OWN ``vars(cls)`` entry populated by the normal scan (the seed is
    #: per-class in ``vars``, never inherited into the MRO walk), and
    #: ``presets.LoggingArgs`` deliberately is NOT seeded because its class body
    #: declares real fields whose flags-tuples must still be scanned.
    _duho_constants_: dict = {}

    def __init__(self, /, **kwargs: object) -> None:
        # Namespace.__init__ only setattrs what's passed, so a directly-built
        # instance (or the self-cloning `type(self)(**self._get_kwargs())`
        # pattern) would be missing any declared field not supplied -- notably
        # `store_true` bools, whose default only materializes via argparse.
        # Seed each declared field to its effective default when absent, so a
        # direct instance has the same attribute surface as a parsed one. Only
        # GAPS are filled: passed kwargs (incl. parsed values) always win, and a
        # required field with no default (NOT_DEFINED) is left unset.
        #
        # `name not in vars(self)` (rather than `hasattr`) is deliberate: a
        # field with an explicit CLASS-level default (`files: list = []`) is
        # already `hasattr`-true via inheritance, which used to skip seeding
        # entirely -- so a direct instance read the CLASS ATTRIBUTE itself,
        # and mutating a mutable one (`instance.files.append(...)`) mutated
        # every other instance and every later parse's default too.
        # `vars(self)` only sees THIS instance's own attributes, so the gap
        # still gets filled with `_effective_default_()`'s fresh copy.
        # Not `super().__init__(**kwargs)`: Namespace's receiver is not
        # positional-only, so a field named `self` would collide with it.
        super().__init__()
        for key, value in kwargs.items():
            setattr(self, key, value)
        if "_passthrough_" not in vars(self):
            self._passthrough_ = []
        seeded: "dict[str, object]" = {}
        for builder in type(self)._getargs_():
            name = builder.name
            if name in kwargs or name in vars(self):
                continue
            default = builder._effective_default_()
            if default is not NOT_DEFINED:
                setattr(self, name, default)
                seeded[name] = _copy.copy(default)
        # Remember exactly which fields THIS CALL passed, outside
        # vars(self) -- see `_duho_explicit_instance_fields`. A subclass that
        # isn't weak-referenceable (e.g. declares `__slots__` without
        # `__weakref__`) simply isn't tracked; `duho.parse(instance)` then
        # falls back to treating every attribute as explicit (today's
        # behavior), same as before this fix.
        try:
            _key = id(self)
            _duho_explicit_instance_fields[_key] = frozenset(kwargs)
            _duho_seeded_instance_values[_key] = seeded
            _weakref.finalize(self, _duho_explicit_instance_fields.pop, _key, None)
            _weakref.finalize(self, _duho_seeded_instance_values.pop, _key, None)
        except TypeError:
            pass

    @classmethod
    def _getargs_(cls) -> "list[ArgumentBuilder]":
        """This class's own declared fields, as one :class:`ArgumentBuilder`
        per field, in declaration order.

        Built once from ``_introspect.get_clsargs(cls)`` (each field's type
        routed through :meth:`Argument.from_type`/its own ``_argbuilder_``,
        then any ``NS(...)``/``Meta(...)`` metadata applied) and cached on
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
                ns_keys: "list[str]" = []
                for opts in decl.annotations:
                    # Only configuration-shaped metadata is consumed: a Mapping
                    # or a namespace-like object (has __dict__, e.g. NS(...)). A
                    # PEP-727-style object with a str `.documentation` contributes
                    # help text. Any other metadata (a bare `Annotated[int, "doc"]`
                    # string, an int, ...) is ignored silently, as Annotated's
                    # own semantics require.
                    if isinstance(opts, Meta):
                        # Typed metadata: merge only the explicitly-set fields so
                        # an unset (sentinel) field never overrides a type-derived
                        # kwarg.
                        options.update(opts._duho_options_())
                    elif isinstance(opts, _ty.Mapping):
                        options.update(opts)
                    elif isinstance(getattr(opts, "documentation", None), str):
                        options.setdefault("help", opts.documentation)
                    elif hasattr(opts, "__dict__"):
                        options.update(vars(opts))
                        if isinstance(opts, _argparse.Namespace):
                            ns_keys.extend(vars(opts))
                if isinstance(decl.type, Argument):
                    # A CUSTOM Argument type wrapped in Arg[...]/NS(...)
                    # (e.g. `Arg[Port, NS(env="PORT")]`) previously routed
                    # through `Argument.from_type(decl.type, **options)`,
                    # whose `super()._argbuilder_` is the PLAIN protocol
                    # default -- `decl.type`'s own `_argbuilder_` override
                    # never ran, so the custom type became a bare, unresolved
                    # `type=` factory instead of using its own parsing logic.
                    # Build via the type's own override, then apply the same
                    # NS(...)/Meta(...) post-processing `from_type` gives a
                    # plain type (help=/env=/Extend()/... all still work).
                    built = decl.type._argbuilder_(name, decl)
                    _apply_argument_options(built, options)
                else:
                    built = Argument.from_type(decl.type, **options)._argbuilder_(
                        name, decl
                    )
                if ns_keys:
                    _warn_unknown_ns_keys(cls, name, ns_keys, built)
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
        subparser: "_argparse._SubParsersAction | None" = None,
        name: "str | None" = None,  # type: ignore
        parents: _ty.Sequence[_argparse.ArgumentParser] = (),
        _inherited_formatter_class_=None,
        _inherited_agent_root_cls_=None,
        _inherited_config_hint_=None,
        _skip_subcommands_=False,
        **kwargs,
    ) -> "_Parser[_Self]":
        """Build (or attach) this class's ``argparse.ArgumentParser``.

        ``subparser`` given -- a ``_SubParsersAction`` -- attaches a new
        sub-parser via ``subparser.add_parser(name or _command_name(cls),
        parents=parents, **kwargs)``; omitted, builds a fresh top-level
        ``ArgumentParser`` the same way. Registers every declared field
        (:meth:`_getargs_`) plus ``-h``/``--version``/``--print-completion``
        and, if any, the ``_subcommands_`` tree, then calls
        :meth:`_initparser_` to add them. Override to customize parser
        construction itself (e.g. a shared ``formatter_class``); override
        :meth:`_initparser_` instead to add parser-level configuration that
        doesn't change how the parser object is created.
        """
        _warn_misspelled_attrs(cls)
        if subparser:
            method = subparser.add_parser
        else:
            method = _argparse.ArgumentParser

        # Resolve the name WITHOUT ever writing it back onto
        # the class. A derived name used to be persisted here (`setattr(cls,
        # "_parsername_", name)`) so a later `getattr` could reuse it -- but
        # `getattr` also follows the MRO, so the derived name leaked to every
        # SUBCLASS built afterwards (a subcommand and a subclass of it,
        # registered as siblings, collapsed onto one name; on 3.11+ the build
        # itself raised "conflicting subparser"). `_command_name` resolves
        # only a `_parsername_` declared ON `cls` ITSELF (`vars(cls)`, not
        # `getattr`) -- a subclass that wants to deliberately share its
        # base's declared name has to say so itself; a bare subclass always
        # gets its own class name, never one inherited from a base's build.
        name_given = name is not None
        name: str = name or (_command_name(cls) if subparser else _app_name(cls))
        # Passed on to `_initparser_`, which accepts it for compatibility with
        # subclasses that override it; nothing here depends on its value.
        explicit_prog = name_given or bool(vars(cls).get("_parsername_"))
        if not subparser and "prog" in kwargs:
            # `ArgumentParser(name, parents=..., **kwargs)` fills the
            # positional `prog` with `name`, so a caller-supplied `prog=`
            # kwarg (`duho.parser(cls, prog=...)`, `duho.parse(cls, argv,
            # parser_kwargs={"prog": ...})`) collided with it outright
            # (`TypeError: got multiple values for argument 'prog'`). An
            # explicit `prog=` names the actual program the user wants and
            # wins outright (a subparser's own `add_parser(name, ...)` has no
            # such collision -- `name` there is the subcommand's registration
            # key, a different argparse parameter from `prog`).
            name = kwargs.pop("prog")
            explicit_prog = True
        # Escape a literal `%` in docstring-derived text for
        # argparse's `%`-expansion. `help=` (this class's own one-line
        # subcommand summary) is ALWAYS expanded by argparse and must always
        # be escaped; `description=` is only formatted when it contains a
        # real `%(prog)` placeholder, so escaping it unconditionally (an
        # earlier fix did) showed a literal `%` DOUBLED in `--help`.
        _doc = cls.__doc__ or ""
        kwargs.setdefault("description", _escape_description(_doc))
        # Opt-in help formatter (``_help_formatter_`` class attr, e.g.
        # ``duho.DefaultsFormatter``/``duho.ColorHelpFormatter``) plumbed into
        # argparse's ``formatter_class``. ``setdefault`` so a caller-supplied
        # ``formatter_class=`` still wins; unset means argparse's plain default.
        # A class that declares none of its own inherits the nearest
        # ANCESTOR's effective formatter (`_inherited_formatter_class_`,
        # threaded down from the parent's own build below) so the whole
        # subcommand tree is styled consistently, not just direct children.
        own_formatter = getattr(cls, "_help_formatter_", None)
        effective_formatter = (
            own_formatter if own_formatter is not None else _inherited_formatter_class_
        )
        if effective_formatter is not None:
            kwargs.setdefault("formatter_class", effective_formatter)
        # The APP's true root class, threaded down through the whole
        # `_subcommands_` tree the same way `_inherited_formatter_class_` is
        # (see the recursive `sub._parser_(...)` call below) -- `None` here
        # means THIS call is the actual top level, so `cls` itself is the
        # root; a deeper node always keeps propagating the same value it
        # received, never resetting to its own `cls`. Consumed by
        # `_install_agent_help` (via `_initparser_`) so a subcommand-scoped
        # agent-help document still reports the app's own version/exit codes.
        agent_root_cls = (
            _inherited_agent_root_cls_
            if _inherited_agent_root_cls_ is not None
            else cls
        )
        # Threaded down through the whole `_subcommands_` tree the same way
        # `agent_root_cls` is (see the recursive `sub._parser_(...)` call
        # below): a caller that already knows it will pass `config=` to
        # `duho.main`/`duho.parse`/`duho.parse_globals` records that here so
        # every node -- root and every nested subcommand -- can react to it
        # (see `_initparser_`'s `external_config`), not just the root.
        external_config = bool(_inherited_config_hint_)
        if subparser:
            kwargs.setdefault(
                "help",
                _escape_help(_doc.strip().splitlines()[0]) if _doc.strip() else "",
            )
            # Subcommand aliases (argparse's add_parser accepts `aliases`; the
            # top-level ArgumentParser does not, so only apply when nested).
            aliases = getattr(cls, "_parseraliases_", None)
            if aliases:
                kwargs.setdefault(
                    "aliases", [a for a in dict.fromkeys(aliases) if a != name]
                )

        # Guard against `cls` already being built higher up in this same
        # call tree (a subcommand that subclasses its own root, directly or
        # via a shared/inherited `_subcommands_` list) -- otherwise building
        # it as its own child recurses without end.
        _build_ids = _guard_recursive_build(cls)
        try:
            # Dests the framework will add to THIS parser (below,
            # and via `_subcommands_`) so a same-named user field collides
            # loudly instead of vanishing silently -- except a dest that came
            # from a `parents=` parser, which is a deliberate, silent reuse
            # (a subcommand inheriting a shared global option).
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

            # A private, sandwich-named dest -- never a name a
            # user field could plausibly declare -- so a root field literally
            # named `command` (or a nested `_subcommands_` tree reusing the
            # same dest) is never silently clobbered by subcommand selection.
            subcommands = getattr(cls, "_subcommands_", None)
            if subparser is not None and "_subcommands_" not in vars(cls):
                # An inherited list may name a class being built right now (a
                # subcommand that subclasses its own root); leave that one out.
                subcommands = [s for s in subcommands or () if id(s) not in _build_ids]
            if subcommands and not _skip_subcommands_:
                subparsers = parser.add_subparsers(dest="_duho_command_", required=True)
                # A kebab-cased class-derived name can collide with
                # a SIBLING's -- `FooBar` and `Foo_Bar` both resolve to
                # `foo-bar` -- exactly the same way two siblings sharing an
                # explicit `_parsername_` already could. Either shape is
                # caught here, once, before it reaches argparse: a silent
                # `add_parser` name clash would otherwise either misroute
                # dispatch (3.9/3.10) or raise argparse's own unrelated
                # "conflicting subparser" error (3.11+) -- neither names the
                # two duho classes actually responsible.
                # A class listed twice is registered once; an alias is checked
                # against every sibling's name and aliases the same way.
                subcommands = list(dict.fromkeys(subcommands))
                sibling_names = [_command_name(sub) for sub in subcommands]
                seen_names: "dict[str, object]" = {}
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
                # argparse falls back to the ACTION'S DEST (never `choices`)
                # for its "required" / "invalid choice" ERROR text when no
                # `metavar` is set -- only the usage SYNOPSIS defaults to a
                # `{...}` built from `choices`. Set it explicitly so the
                # private `_duho_command_` dest never leaks into either
                # message (it used to show "the following arguments are
                # required: _duho_command_"); `instance.command` stays gone
                # regardless (a documented [minor] break).
                subparsers.metavar = "{" + ",".join(sibling_names) + "}"
                # Dests this (root) class declares itself: an option given BEFORE the
                # subcommand parses into these on the root namespace. A child that
                # inherits the same field (via MRO) re-declares it with its own
                # default and would clobber that value back when the flag is absent
                # from the child's argv. Suppress the child's default for those
                # shared, optional dests so the parent's parsed value survives; the
                # child still accepts the flag AFTER the subcommand (overwrites) and
                # its own unique args are untouched.
                root_builders = {b.name: b for b in cls._getargs_()}
                root_dests = set(root_builders)
                root_defaults = {
                    n: b._effective_default_() for n, b in root_builders.items()
                }
                # A config table reaches every subcommand's own slice
                # (`_stash_layer_state` recurses the same table down the
                # tree), so propagate "a config could apply here" too --
                # either inherited from a caller's own `config=` hint, or
                # because THIS class (root, or an intermediate node in a
                # multi-level tree) declares `_config_` itself.
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
                    # Link child -> parent so `_merge_layers_upward`
                    # (run from the child's own `_initparser_`-patched
                    # `parse_known_args`, only when it actually executes) folds
                    # this child's provenance into the parent's, one level at a
                    # time, only for the parser chain actually selected.
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
        parent_dests: "_ty.FrozenSet[str] | None" = None,
        explicit_prog: bool = False,
        agent_root_cls: "type | None" = None,
        external_config: bool = False,
    ):
        """Populate an already-created ``parser`` with this class's own fields.

        Called by :meth:`_parser_` right after creating/attaching the parser:
        adds each declared field's argument (respecting
        ``NS(group=...)``/``NS(conflicts=...)``
        titled/mutually-exclusive groups), patches ``parse_known_args`` to
        build the final ``Args``/``Cmd`` instance from the raw ``Namespace``,
        and -- for a non-subcommand root -- injects ``--print-completion``
        when ``_completion_`` is set. Override to add parser-level
        configuration ``_parser_`` doesn't itself expose (call
        ``super()._initparser_(parser, ...)`` first to keep this behavior).

        An override must accept ``**kwargs`` and forward them to ``super()``:
        ``_parser_`` passes build context by keyword and may pass more than
        the signature below lists. ``explicit_prog`` is accepted and unused.

        ``external_config`` -- threaded down from :meth:`_parser_` -- tells
        :func:`_add_fields` that a config table WILL reach this class even
        though `cls` itself declares no `_config_` (an explicit `config=`
        kwarg to `duho.main`/`duho.parse`/`duho.parse_globals`, or a root
        ancestor's own `_config_` cascading down the `_subcommands_` tree).
        """
        parent_dests = parent_dests if parent_dests is not None else frozenset()

        def parse_known_args(
            args: "_ty.Sequence[str] | None" = None, namespace: "NS | None" = None
        ):
            if namespace is None:
                namespace = _argparse.Namespace()

            setattr(namespace, "#cls", cls)

            # `_passthrough_`: capture argv after the FIRST literal `--`
            # separator. Only the
            # top-level parse owns the split -- a subparser is invoked by
            # argparse._SubParsersAction with an already-sliced arg list and
            # namespace=None, so splitting there would double-consume. The
            # left side is parsed normally; the right side is stashed on the
            # constructed instance as `_passthrough_` (empty list when absent
            # or when multiple `--` appear, only the first splits).
            passthrough: "list[str] | None" = None
            if not is_subcommand:
                if args is None:
                    argv = _sys.argv[1:]
                else:
                    argv = list(args)
                if "--" in argv:
                    idx = argv.index("--")
                    passthrough = argv[idx + 1 :]
                    argv = argv[:idx]
                args = argv

            # A flag placed BETWEEN two positional groups (one of them
            # variable-arity) breaks under argparse's own greedy
            # positional-run matching (bpo-15112) -- reorder recognized
            # flags to the front of the slice THIS parser will actually see
            # so the positional run stays contiguous. Scoped to parsers with
            # the risky shape only (cheap check, no cost/behavior change
            # otherwise); never touches anything after the `--` split above
            # (that's `passthrough`, already carved out); bails unreordered
            # on anything it isn't certain about, so a genuine typo still
            # surfaces argparse's own honest error through the real,
            # UNMODIFIED parse below.
            if args is not None and _has_variadic_positional(parser):
                args = _reorder_argv_for_variadic_positional(parser, list(args))

            # Install this class's own env/config/instance placeholders as
            # not-yet-converted defaults (Design Q2/Q4) -- lazily, right here,
            # so a value belonging to a subcommand this invocation never
            # reaches is never even resolved.
            _stage_layers(parser, cls)

            parsed, unk = _argparse.ArgumentParser.parse_known_args(
                parser, args, namespace
            )

            # Convert whichever placeholders the CLI left untouched, report a
            # bad one through `parser.error`, and merge this parser's
            # own provenance up into its parent's -- see
            # `_finalize_layers`/`_merge_layers_upward`.
            _finalize_layers(parser, cls, parsed)
            _merge_layers_upward(parser)

            # Per-PARSE mutable-default copy, scoped to THIS parser's
            # own actions (a subcommand's redeclared fields are a different
            # action set, handled the same way when ITS OWN wrapped
            # parse_known_args runs).
            for _pa in parser._actions:
                _dest = _pa.dest
                if _dest is None or _dest is _argparse.SUPPRESS:
                    continue
                _default = _pa.default
                if isinstance(_default, (list, set, dict)) and (
                    getattr(parsed, _dest, None) is _default
                ):
                    # `_kwargs` already gives each parser BUILD its own
                    # copy of a mutable default, but argparse puts that SAME
                    # object onto every namespace a REUSED parser produces
                    # when the field is never touched by this parse -- so two
                    # `parse_args()` calls on one cached parser would share
                    # (and, on mutation, leak into each other's) that list/
                    # set/dict. Break the aliasing once more, per parse.
                    setattr(parsed, _dest, _copy.copy(_default))

            if is_subcommand:
                # Invoked via argparse._SubParsersAction.__call__, which
                # calls us with namespace=None and then copies vars(result)
                # back onto the *parent* namespace. Keep "#cls" in the
                # returned dict (rather than popping/constructing here) so
                # it propagates upward and overwrites the parent's own
                # "#cls" -- subparsers are parsed after the parent's own
                # actions, so the deepest selection always lands last and
                # wins. Only the true top-level call (is_subcommand=False)
                # pops "#cls" and constructs the final instance.
                return parsed, unk

            _cls: "type[_Self]" = parsed.__dict__.pop("#cls")
            # Drop the `_CollectionAction`/`UpdateAction` sidecars
            # (`_duho_items_<dest>` / `_duho_dict_seen_<dest>`) before
            # constructing the instance so this internal bookkeeping never leaks
            # into vars(instance) or the documented clone pattern.
            for _sidecar in [
                k
                for k in parsed.__dict__
                if k.startswith("_duho_items_") or k.startswith("_duho_dict_seen_")
            ]:
                del parsed.__dict__[_sidecar]
            parsed.__dict__.pop("_duho_command_", None)
            instance = _cls(**parsed.__dict__)
            # Attach captured passthrough (empty list when no `--` was seen).
            instance._passthrough_ = passthrough if passthrough is not None else []
            # Debug-aid linkage for duho.value_sources(): remember the parser
            # (which carries _duho_value_sources_/_duho_merged_defaults_,
            # merged up the selected chain by `_merge_layers_upward` as each
            # level's own `parse_known_args` finished) that produced instances
            # of this class. `parser` here is THIS closure's own variable --
            # for a nested selection `_cls` has already been rebound to the
            # DEEPEST selected class by the `pop("#cls")` above, while
            # `parser` is still the ROOT's, which by now holds the merged
            # chain. Per-class, not per-instance -- keeps Args
            # instances themselves free of framework bookkeeping in
            # vars()/__dict__.
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
            # argparse's own `parse_intermixed_args` (both the public
            # method and `parse_known_intermixed_args` it calls) route
            # differently across versions -- 3.9 calls back into
            # `self.parse_known_args` (our patch above) and then crashes
            # trying to `delattr` positional dests off the constructed `Args`
            # instance it gets back instead of a plain `Namespace`; 3.14 calls
            # a private `_parse_known_args2` directly, bypassing the patch
            # entirely and returning a bare `Namespace` with no `"#cls"`
            # selection, no constructed instance, and no `_passthrough_`.
            # Neither is a supported duho path; fail loud with a pointer to
            # the real one instead of a version-dependent crash or a
            # half-built result.
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
                # Escape a literal `%` in the resolved version string --
                # argparse `%`-formats `version=` the same as any other help
                # text, so `_version_ = "2.0 (100% rewrite)"` crashed
                # `--version` with a bare `TypeError`.
                # `_Utf8SafeVersionAction`, not the stock `action="version"`
                # (`argparse._VersionAction`): same text/exit code, but
                # crash-proof on a non-UTF-8 stdout -- see its docstring.
                action=_Utf8SafeVersionAction,
                version="%(prog)s " + version.replace("%", "%%"),
            )

        # --print-completion: opt-in via _completion_ = True, only injected
        # on the top-level parser (a subcommand's own parser only sees its
        # own subtree, not the whole app) -- skipped if a "print_completion"
        # dest already exists (e.g. from a parents=[...] parser).
        if not is_subcommand and getattr(cls, "_completion_", False):
            actions_by_dest_pre2 = {action.dest: action for action in parser._actions}
            if "print_completion" not in actions_by_dest_pre2:
                parser.add_argument(
                    "--print-completion",
                    # One shared tuple, also used by the standalone
                    # `print_completion()` function's own validation, instead
                    # of a second hardcoded copy that could silently drift.
                    choices=_COMPLETION_SHELLS,
                    action=_PrintCompletionAction,
                    root_parser=parser,
                    dest="print_completion",
                    help="Print a shell completion script for the given shell and exit.",
                )

        # Wire this class's own declared fields (and their titled/mutually-
        # exclusive groups) onto the parser -- shared with
        # `runtime._add_module_declared_fields` so a module command
        # gets the exact same `NS(group=...)`/`NS(conflicts=...)` support a
        # class command does.
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
