"""Opt-in launcher generator: emit a run-from-checkout launcher pair for an app.

**Opt-in dev tool.** Core ``duho`` never imports this module; you activate it
explicitly with ``import duho.scaffold`` or ``python -m duho.scaffold``. Its
symbols are in this module's own ``__all__`` but deliberately **NOT** on the
top-level ``duho.__all__`` -- it is a project-scaffolding helper, not runtime
surface.

What it generates
-----------------

For a target application laid out as ``<root>/bin/`` + ``<root>/<libdir>/<app>``
(a package importable once ``<root>/<libdir>`` is on ``PYTHONPATH``),
:func:`generate_launchers` writes a matched **launcher pair** into ``<root>/bin/``:

* ``bin/<app>`` -- a POSIX ``sh`` wrapper that resolves its own directory,
  computes the app root (the parent of ``bin/``), prepends ``<root>/<libdir>`` to
  ``PYTHONPATH``, and ``exec``s ``"${PYTHON:-python3}" -m <app> "$@"``;
* ``bin/<app>.cmd`` -- the Windows cousin doing the same via ``%~dp0`` and
  ``%PYTHON%`` (default ``python``), running ``%PYTHON% -m <app> %*``.

Both launchers are small, dependency-free, and honor a ``PYTHON`` environment
override so a caller can pin the interpreter. The generator writes **plain
files** (it copies/emits text -- it never creates symlinks, which need privilege
on Windows and add failure modes); the POSIX file additionally gets its
executable bit set best-effort (skipped on platforms without ``chmod`` support).

By default an existing launcher is **not** clobbered -- pass ``overwrite=True``
(``--force`` on the CLI) to rewrite a launcher a user may have customized.

The CLI dogfoods duho itself: ``python -m duho.scaffold <app> [--root DIR]
[--libdir lib] [--python PY] [--force]`` is implemented as a :class:`duho.Cmd`.

All union annotations are quoted so the module imports cleanly on Python 3.9.
"""

import os as _os
import sys as _sys
import typing as _ty
from pathlib import Path as _Path

from . import _compat as _compat
from .args import AUTO as _AUTO, Cli as _Cli, main as _main

__all__ = ["generate_launchers", "ScaffoldCmd"]

#: Default interpreter tokens per launcher flavor when ``python`` is not pinned.
#: The POSIX launcher defaults to ``python3``; the Windows ``.cmd`` defaults to
#: ``python`` (the usual Windows launcher name). Both honor a ``PYTHON`` env
#: override at runtime.
_DEFAULT_POSIX_PYTHON = "python3"
_DEFAULT_WINDOWS_PYTHON = "python"

#: Characters that could let ``libdir``/``python`` break out of the double
#: quotes they are interpolated into (a stray quote), run a nested command (a
#: backtick or ``$(...)`` in POSIX ``sh``), expand an unintended variable
#: (``%...%`` in ``cmd.exe``, still expanded inside double quotes), or split
#: the generated file into more than one line.
_FORBIDDEN_INTERPOLATION_CHARS = frozenset("\"'`$%\n\r")


def _validate_app(app: str) -> None:
    """Raise :class:`ValueError` unless ``app`` is a safe, real module name.

    ``app`` is used BOTH as a filename under ``bin/`` and unquoted after ``-m``
    in the generated shell/batch text, so it must be a dotted ASCII identifier
    (the same grammar Python itself requires for ``python -m <app>``) -- never
    a hyphenated distribution name, a path (which could escape ``bin/`` via
    ``..``), or text containing shell/batch metacharacters (O038).
    """
    if (
        not app
        or not app.isascii()
        or not all(part.isidentifier() for part in app.split("."))
    ):
        raise ValueError(
            "duho.scaffold: app must be a dotted ASCII identifier (the "
            "importable module name for `python -m <app>`), got %r" % (app,)
        )


def _validate_interpolated(value: str, what: str, *, path_like: bool = False) -> None:
    """Raise :class:`ValueError` unless ``value`` is safe to bake into a launcher.

    Applies to ``libdir`` and ``python``, both of which are written verbatim
    inside double quotes in the generated POSIX/``.cmd`` text (O038, O040):
    non-ASCII is rejected outright (cmd.exe decodes a batch file with the
    console's OEM code page, not UTF-8, so a non-ASCII byte baked into the
    ``.cmd`` is mis-decoded there -- O040), as are quotes, backticks, ``$``,
    ``%`` and newlines. ``path_like`` additionally rejects an absolute path or
    one containing ``..`` (``libdir`` is joined under the app root).
    """
    if not value.isascii():
        raise ValueError(
            "duho.scaffold: %s must be ASCII (a non-ASCII byte in the "
            "generated .cmd is mis-decoded by cmd.exe's OEM code page): %r"
            % (what, value)
        )
    if any(ch in _FORBIDDEN_INTERPOLATION_CHARS for ch in value):
        raise ValueError(
            "duho.scaffold: %s must not contain quotes, backticks, '$', '%%' "
            "or newlines (it is interpolated into generated shell/batch "
            "text): %r" % (what, value)
        )
    if path_like:
        as_path = _Path(value)
        if as_path.is_absolute() or ".." in as_path.parts:
            raise ValueError(
                "duho.scaffold: %s must be a relative path without '..': %r"
                % (what, value)
            )


def _posix_launcher(app: str, libdir: str, python: str) -> str:
    """Return the POSIX ``sh`` launcher text for ``app``.

    Resolves the script's own directory (following symlinks via a small
    ``readlink`` loop so a launcher symlinked onto ``PATH`` still finds its app
    root), derives the app root as the parent of ``bin/``, prepends
    ``<root>/<libdir>`` to ``PYTHONPATH``, and ``exec``s the app module. The
    interpreter is ``${PYTHON:-<python>}`` so a ``PYTHON`` env var overrides the
    baked-in default. Generic -- no project-specific names are emitted.

    Both ``cd`` calls run with ``CDPATH=`` cleared: bash's ``cd`` PRINTS the
    resolved directory to stdout when ``CDPATH`` is exported and the target is
    relative, which would make the surrounding ``$(...)`` capture two lines
    instead of one and fail the next ``cd`` under ``set -e`` (O039) -- this
    breaks a developer's own shell rc, not just a hostile environment.
    """
    return (
        "#!/bin/sh\n"
        "# Launcher for '{app}' -- runs it from a source checkout without an\n"
        "# install by putting '{libdir}' on PYTHONPATH. Generated by duho.scaffold;\n"
        "# regenerate rather than hand-editing. Honors $PYTHON to pin the interpreter.\n"
        "set -e\n"
        "# Resolve this script's real directory, following symlinks.\n"
        'script="$0"\n'
        'while [ -h "$script" ]; do\n'
        '    link=$(readlink "$script")\n'
        '    case "$link" in\n'
        '        /*) script="$link" ;;\n'
        '        *) script="$(dirname "$script")/$link" ;;\n'
        "    esac\n"
        "done\n"
        'bindir=$(CDPATH= cd -- "$(dirname -- "$script")" && pwd)\n'
        'root=$(CDPATH= cd -- "$bindir/.." && pwd)\n'
        'libdir="$root/{libdir}"\n'
        'if [ -n "${{PYTHONPATH:-}}" ]; then\n'
        '    PYTHONPATH="$libdir:$PYTHONPATH"\n'
        "else\n"
        '    PYTHONPATH="$libdir"\n'
        "fi\n"
        "export PYTHONPATH\n"
        'exec "${{PYTHON:-{python}}}" -m {app} "$@"\n'
    ).format(app=app, libdir=libdir, python=python)


def _windows_launcher(app: str, libdir: str, python: str) -> str:
    """Return the Windows ``.cmd`` launcher text for ``app``.

    Uses ``%~dp0`` (the batch file's own directory) to derive the app root,
    prepends ``<root>\\<libdir>`` to ``PYTHONPATH``, and runs ``%PYTHON% -m
    <app> %*``. ``PYTHON`` defaults to ``<python>`` when unset so a caller can
    override the interpreter. Generic -- no project names. The text embeds
    literal CRLF line endings, and the caller writes it with ``newline=""`` so
    Python does not translate them again (O032 -- this used to say Python
    writes it with "the platform newline", which is wrong on POSIX).
    """
    return (
        "@echo off\r\n"
        "rem Launcher for '{app}' -- runs it from a source checkout without an\r\n"
        "rem install by putting '{libdir}' on PYTHONPATH. Generated by duho.scaffold;\r\n"
        "rem regenerate rather than hand-editing. Honors %PYTHON% to pin the interpreter.\r\n"
        "setlocal\r\n"
        'set "bindir=%~dp0"\r\n'
        'for %%I in ("%bindir%..") do set "root=%%~fI"\r\n'
        'set "libdir=%root%\\{libdir}"\r\n'
        "if defined PYTHONPATH (\r\n"
        '    set "PYTHONPATH=%libdir%;%PYTHONPATH%"\r\n'
        ") else (\r\n"
        '    set "PYTHONPATH=%libdir%"\r\n'
        ")\r\n"
        'if not defined PYTHON set "PYTHON={python}"\r\n'
        '"%PYTHON%" -m {app} %*\r\n'
    ).format(app=app, libdir=libdir, python=python)


def _make_executable(path: "_Path") -> None:
    """Best-effort ``chmod +x`` on ``path`` (add the execute bits mode allows).

    Mirrors the read bits into execute bits (``u+x`` where ``u+r``, etc.), which
    is the conventional "make it runnable for whoever can read it". Any OS error
    (e.g. a filesystem that doesn't support the bit, or Windows) is swallowed --
    the launcher text is still correct and callable as ``sh bin/<app>``.
    """
    try:
        mode = _os.stat(path).st_mode
        exec_bits = (mode & 0o444) >> 2  # r-bits -> x-bits
        _os.chmod(path, mode | exec_bits)
    except OSError:
        pass


def generate_launchers(
    app: str,
    root: "_ty.Union[str, _Path]",
    *,
    libdir: str = "lib",
    python: "_ty.Optional[str]" = None,
    overwrite: bool = False,
) -> "_ty.List[_Path]":
    """Write a POSIX + Windows launcher pair for ``app`` into ``<root>/bin/``.

    Emits ``<root>/bin/<app>`` (POSIX ``sh``) and ``<root>/bin/<app>.cmd``
    (Windows), each of which puts ``<root>/<libdir>`` on ``PYTHONPATH`` and runs
    ``python -m <app>`` -- so the app runs from a checkout without being
    installed. The ``bin/`` directory is created if missing. Returns the list of
    written paths (POSIX first, then ``.cmd``).

    Parameters
    ----------
    app:
        The app's importable module name (the ``<app>`` in ``python -m <app>``).
    root:
        The app root directory (the parent of ``bin/``); ``bin/`` is created
        under it.
    libdir:
        The subdirectory under ``root`` holding the app package, prepended to
        ``PYTHONPATH`` (default ``"lib"``; a ``src``-layout app passes
        ``libdir="src"``).
    python:
        Interpreter token baked in as the default (overridable at runtime via
        the ``PYTHON`` env var). ``None`` uses the per-flavor default
        (``python3`` for POSIX, ``python`` for Windows).
    overwrite:
        When ``False`` (default), refuse to clobber an existing launcher --
        raising :class:`FileExistsError` naming the first conflict, so a
        user-customized launcher is never silently overwritten. ``True`` rewrites.

    The generator writes **plain files** (never symlinks) and sets the POSIX
    launcher's executable bit best-effort (a no-op where the platform/filesystem
    doesn't support it).

    Raises :class:`ValueError` for an ``app`` that is not a dotted ASCII
    identifier, a ``libdir`` that is not a relative, ``..``-free ASCII path
    free of quote/backtick/``$``/``%``/newline characters, or a ``python`` with
    the same forbidden characters -- all three are interpolated into generated
    shell/batch text, so a hyphenated/path-like/hostile value would otherwise
    produce a launcher that cannot work, escapes ``bin/``, or executes
    unintended commands (O038, O040).
    """
    _validate_app(app)
    _validate_interpolated(libdir, "libdir", path_like=True)
    if python is not None:
        _validate_interpolated(python, "python")

    root_path = _Path(root)
    bindir = root_path / "bin"

    posix_path = bindir / app
    windows_path = bindir / (app + ".cmd")

    if not overwrite:
        for existing in (posix_path, windows_path):
            if existing.exists():
                raise FileExistsError(
                    "duho.scaffold: launcher %s already exists; pass "
                    "overwrite=True (or --force) to replace it" % existing
                )

    bindir.mkdir(parents=True, exist_ok=True)

    posix_python = python if python is not None else _DEFAULT_POSIX_PYTHON
    windows_python = python if python is not None else _DEFAULT_WINDOWS_PYTHON

    # POSIX launcher: written with "\n" newlines regardless of host (newline=""
    # disables translation, and the text embeds only "\n") so a shell script
    # generated on Windows still runs on a POSIX host. NOTE: use open() rather
    # than Path.write_text(newline=...) -- the latter's newline kwarg is 3.10+,
    # and duho's floor is 3.9.
    with posix_path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(_posix_launcher(app, libdir, posix_python))
    _make_executable(posix_path)

    # Windows launcher: written with "\r\n" (the text already embeds CRLF, and
    # newline="" keeps Python from translating it further).
    with windows_path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(_windows_launcher(app, libdir, windows_python))

    return [posix_path, windows_path]


class ScaffoldCmd(_Cli):
    """Generate a run-from-checkout launcher pair for an app.

    Writes bin/<app> (POSIX) and bin/<app>.cmd (Windows) under --root
    (default: the current directory) and prints each written path. Usage:
    python -m duho.scaffold <app> [--root DIR] [--libdir lib] [--python PY] [--force]
    """

    _parsername_ = "duho.scaffold"
    _version_ = _AUTO
    _distribution_ = "duho"

    app: str
    "Importable module name of the app to launch (the <app> in `python -m <app>`)."
    ("app",)  # type: ignore

    root: "_Path" = _Path(".")
    "App root directory (parent of bin/); defaults to the current directory."
    ("--root",)  # type: ignore

    libdir: str = "lib"
    "Subdir under root holding the app package, put on PYTHONPATH (default: lib)."
    ("--libdir",)  # type: ignore

    python: "_ty.Optional[str]" = None
    "Interpreter to bake in as the default (overridable at runtime via $PYTHON)."
    ("--python",)  # type: ignore

    force: bool = False
    "Overwrite existing launcher files instead of refusing."
    ("--force",)  # type: ignore

    def __call__(self) -> int:
        try:
            written = generate_launchers(
                self.app,
                self.root,
                libdir=self.libdir,
                python=self.python,
                overwrite=self.force,
            )
        except FileExistsError as exc:
            # generate_launchers documents this as the refusal-to-overwrite
            # signal; the CLI reports it as a one-line error, not a traceback
            # for an expected, documented condition. The library function
            # itself keeps raising -- only this CLI wrapper catches it.
            print(str(exc), file=_sys.stderr)
            print("duho.scaffold: pass --force to overwrite", file=_sys.stderr)
            return 1
        except OSError as exc:
            # Any other filesystem failure (permission denied, a read-only
            # target, ...) is also an expected, reportable condition for a
            # CLI -- not a 22-line traceback.
            print("duho.scaffold: %s" % (exc,), file=_sys.stderr)
            return 1
        for path in written:
            # O042: both launchers are already written by this point --
            # a `print(path)` that then raises `UnicodeEncodeError` (a
            # non-cp1252 path piped on Windows) reported a successful run as
            # a crash (exit 1), and the "obvious" re-run was then refused by
            # `generate_launchers`' own no-clobber check. `write_human` shows
            # each path using the stream's own encoding, escaping only a
            # character genuinely outside it instead of raising.
            _compat.write_human(str(path) + "\n", _sys.stdout)
        return 0


def main(argv: "_ty.Optional[_ty.Sequence[str]]" = None) -> int:
    """``python -m duho.scaffold`` entry point: dispatch :class:`ScaffoldCmd`."""
    return _main(ScaffoldCmd, argv)


if __name__ == "__main__":
    _sys.exit(main())
