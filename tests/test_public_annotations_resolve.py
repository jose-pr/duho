"""Every callable a public module exports has annotations a consumer's tooling can use.

``typing.get_type_hints`` must resolve on the running interpreter (so a name used
in a hint exists at run time and no PEP 604 union is evaluated on 3.9), and every
parameter and the return are annotated.
"""

import importlib
import inspect
import pkgutil
import typing

import pytest

import duho

# Callable (``module.Qualified.name``) -> why it is not held to the rule.
EXEMPT: "dict[str, str]" = {}


def _public_modules():
    names = ["duho"]
    for info in pkgutil.walk_packages(duho.__path__, "duho."):
        if not any(part.startswith("_") for part in info.name.split(".")):
            names.append(info.name)
    mods = [importlib.import_module(n) for n in names]
    return [m for m in mods if hasattr(m, "__all__")]


def _unwrap(obj):
    if isinstance(obj, (staticmethod, classmethod)):
        return obj.__func__
    if isinstance(obj, property):
        return obj.fget
    return obj


def _is_public_dunder_or_sandwich(attr):
    """``__init__``, ``__call__`` and the ``_name_`` hooks a subclass overrides."""
    if attr in ("__init__", "__call__"):
        return True
    return attr.startswith("_") and attr.endswith("_") and not attr.startswith("__")


def _callables():
    """Yield ``(label, object)`` for each exported function and each own method."""
    seen = set()
    for mod in _public_modules():
        for name in mod.__all__:
            obj = getattr(mod, name)
            if id(obj) in seen:
                continue
            seen.add(id(obj))
            if inspect.isclass(obj):
                if not obj.__module__.startswith("duho"):
                    continue
                yield f"{mod.__name__}.{name}", obj
                for attr, raw in vars(obj).items():
                    if attr.startswith("_") and not _is_public_dunder_or_sandwich(attr):
                        continue
                    fn = _unwrap(raw)
                    if inspect.isfunction(fn) and fn.__module__.startswith("duho"):
                        yield f"{mod.__name__}.{name}.{attr}", fn
            elif inspect.isfunction(obj) and obj.__module__.startswith("duho"):
                yield f"{mod.__name__}.{name}", obj


CASES = sorted(_callables(), key=lambda c: c[0])


@pytest.mark.parametrize("label,obj", CASES, ids=[c[0] for c in CASES])
def test_annotations_resolve_and_are_complete(label, obj):
    if label in EXEMPT:
        pytest.skip(EXEMPT[label])
    typing.get_type_hints(obj, include_extras=True)
    if not inspect.isfunction(obj):
        return
    sig = inspect.signature(obj)
    missing = [
        p.name
        for p in sig.parameters.values()
        if p.annotation is inspect.Parameter.empty and p.name not in ("self", "cls")
    ]
    assert not missing, f"{label}: unannotated parameters {missing}"
    assert (
        sig.return_annotation is not inspect.Signature.empty
    ), f"{label}: no return annotation"
