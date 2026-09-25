"""Tests for duho.env.Env (prefixed environment accessor)."""

import os as _os
import pathlib
import sys

import pytest

from duho.env import Env


class TestPrefixNormalization:
    def test_uppercased_hyphen_to_underscore_trailing(self):
        assert Env("my-app").prefix == "MY_APP_"

    def test_already_normalized_stable(self):
        assert Env("MYAPP_").prefix == "MYAPP_"

    def test_empty_prefix_stays_empty(self):
        assert Env("").prefix == ""

    def test_reads_prefixed_env_key(self, monkeypatch):
        monkeypatch.setenv("MY_APP_X", "hello")
        assert Env("my-app")["X"] == "hello"


class TestGetItem:
    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("MA_HOST", "example.com")
        assert Env("ma")["HOST"] == "example.com"

    def test_stored_value_wins_over_environ(self, monkeypatch):
        monkeypatch.setenv("MA_HOST", "from-environ")
        e = Env("ma", HOST="from-kwarg")
        assert e["HOST"] == "from-kwarg"

    def test_kwarg_override_precedence(self):
        # **env kwargs override anything (here, nothing else set them).
        e = Env("ma", TOKEN="abc")
        assert e["TOKEN"] == "abc"

    def test_missing_key_raises_keyerror(self):
        with pytest.raises(KeyError):
            Env("ma")["NOPE"]


class TestBool:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "Y", "t", "True"])
    def test_truthy(self, monkeypatch, value):
        monkeypatch.setenv("MA_DEBUG", value)
        assert Env("ma").bool("DEBUG") is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "n", "f", "", "maybe"])
    def test_falsey(self, monkeypatch, value):
        monkeypatch.setenv("MA_DEBUG", value)
        assert Env("ma").bool("DEBUG") is False

    def test_missing_defaults_false(self):
        assert Env("ma").bool("DEBUG") is False

    @pytest.mark.parametrize("value", ["on", "ON", "On"])
    def test_on_is_truthy(self, monkeypatch, value):
        # The layered converter (ArgumentBuilder._BOOL_TRUE) has always taken
        # "on"; Env.bool did not, so a var spelled ON read silently as False.
        monkeypatch.setenv("MA_DEBUG", value)
        assert Env("ma").bool("DEBUG") is True

    @pytest.mark.parametrize("value", ["off", "OFF"])
    def test_off_is_falsey(self, monkeypatch, value):
        monkeypatch.setenv("MA_DEBUG", value)
        assert Env("ma").bool("DEBUG") is False

    def test_truthy_set_matches_the_layered_converter(self):
        """Env.bool and the layered converter share one truthy table now
        (`duho._compat.BOOL_TRUE`), so there is nothing left to drift --
        they differ only in strictness (Env.bool is lenient about unknown
        values, the layered converter raises), never in which words are true.
        """
        from duho import _compat

        for value in _compat.BOOL_TRUE:
            assert Env("ma", DEBUG=value).bool("DEBUG") is True

    def test_strips_whitespace(self, monkeypatch):
        # The cmd.exe `set VAR=1 && ...` trailing-space pitfall: a layered
        # NS(env=...) bool field already stripped before matching; Env.bool
        # didn't.
        monkeypatch.setenv("MA_DEBUG", " 1 ")
        assert Env("ma").bool("DEBUG") is True


class TestList:
    def test_default_separator(self, monkeypatch):
        monkeypatch.setenv("MA_HOSTS", "a:b:c")
        assert Env("ma").list("HOSTS") == ["a", "b", "c"]

    def test_custom_separator(self, monkeypatch):
        monkeypatch.setenv("MA_HOSTS", "a,b,c")
        assert Env("ma").list("HOSTS", sep=",") == ["a", "b", "c"]

    def test_custom_type_int(self, monkeypatch):
        monkeypatch.setenv("MA_PORTS", "1:2:3")
        assert Env("ma").list("PORTS", ty=int) == [1, 2, 3]

    def test_missing_key_yields_empty_list(self):
        # A missing var yields [], not [ty("")]. The old
        # [""] contract turned a missing CMDS_PATH into [Path(".")] and imported
        # the whole CWD.
        assert Env("ma").list("MISSING") == []

    def test_empty_value_yields_empty_list(self, monkeypatch):
        monkeypatch.setenv("MA_EMPTY", "")
        assert Env("ma").list("EMPTY") == []


class TestPaths:
    """``Env.paths`` splits on the OS path separator (``os.pathsep``), overridable
    by ``PATHSEP`` -- so a Windows ``C:\\...`` drive letter is never mis-split."""

    def test_splits_on_os_pathsep(self, monkeypatch):
        import os

        monkeypatch.delenv("PATHSEP", raising=False)
        monkeypatch.setenv("MA_CMDS_PATH", os.pathsep.join(["/a/b", "/c/d"]))
        assert Env("ma").paths("CMDS_PATH") == ["/a/b", "/c/d"]

    @pytest.mark.skipif(
        _os.pathsep != ";",
        reason="a drive-letter colon only collides with os.pathsep on Windows",
    )
    def test_windows_drive_letter_not_split(self, monkeypatch):
        # A single absolute path with a drive-letter colon must come back as
        # ONE entry, never split into a bogus "C" -- only reproduces on
        # Windows, where os.pathsep is ";" (on POSIX the colon IS the real
        # separator, so this scenario doesn't arise there).
        monkeypatch.delenv("PATHSEP", raising=False)
        drive_path = "C:\\Users\\me\\cmds"
        monkeypatch.setenv("MA_CMDS_PATH", drive_path)
        result = Env("ma").paths("CMDS_PATH")
        assert result == [drive_path]

    def test_pathsep_override(self, monkeypatch):
        # This app's OWN prefixed <PREFIX>PATHSEP forces the separator
        # regardless of platform -- a bare, global PATHSEP no longer does
        # (see test_bare_unprefixed_pathsep_does_not_override below: a
        # security fix, since an unprefixed PATHSEP is set for every duho
        # app on the machine, not just this one).
        monkeypatch.delenv("PATHSEP", raising=False)
        monkeypatch.setenv("MA_PATHSEP", "|")
        monkeypatch.setenv("MA_CMDS_PATH", "x|y|z")
        assert Env("ma").paths("CMDS_PATH") == ["x", "y", "z"]

    def test_bare_unprefixed_pathsep_does_not_override(self, monkeypatch):
        # A security fix: a bare, unprefixed PATHSEP (set for some wholly
        # unrelated program on the same machine) must NOT affect this app's
        # separator -- only its OWN <PREFIX>PATHSEP does (see above).
        monkeypatch.setenv("PATHSEP", "|")
        monkeypatch.delenv("MA_PATHSEP", raising=False)
        monkeypatch.setenv("MA_CMDS_PATH", "x|y|z")
        # Falls back to the real os.pathsep, so "x|y|z" stays ONE entry.
        assert Env("ma").paths("CMDS_PATH") == ["x|y|z"]

    def test_custom_type(self, monkeypatch):
        import os

        monkeypatch.delenv("PATHSEP", raising=False)
        monkeypatch.setenv("MA_CMDS_PATH", os.pathsep.join(["/a", "/b"]))
        assert Env("ma").paths("CMDS_PATH", ty=pathlib.Path) == [
            pathlib.Path("/a"),
            pathlib.Path("/b"),
        ]

    def test_missing_yields_empty(self, monkeypatch):
        monkeypatch.delenv("MA_CMDS_PATH", raising=False)
        assert Env("ma").paths("CMDS_PATH") == []

    def test_empty_segments_are_dropped_leading_trailing_doubled(self, monkeypatch):
        """Security: a leading, trailing, or doubled separator must NOT
        produce an empty path segment. An empty segment used to become
        ``ty("")`` -- ``Path("")`` is ``Path(".")`` -- silently meaning the
        current directory (unlike here; unlike POSIX ``$PATH`` too). On this
        box ``os.pathsep`` is ``;``; the assertions don't hard-code it.
        """
        import os

        monkeypatch.delenv("PATHSEP", raising=False)
        sep = os.pathsep
        real = "/real/cmds"
        cases = {
            "leading": f"{sep}{real}",
            "trailing": f"{real}{sep}",
            "doubled": f"{real}{sep}{sep}{real}",
            "only_sep": sep,
            "only_sep_x2": sep + sep,
        }
        for label, value in cases.items():
            monkeypatch.setenv("MA_CMDS_PATH", value)
            result = Env("ma").paths("CMDS_PATH", ty=pathlib.Path)
            assert pathlib.Path("") not in result, f"{label}: {result!r}"
            assert pathlib.Path(".") not in result, f"{label}: {result!r}"
        # The real path is still recovered from the leading/trailing/doubled
        # cases (only the empty segments are dropped, not the real one).
        monkeypatch.setenv("MA_CMDS_PATH", f"{sep}{real}{sep}")
        assert Env("ma").paths("CMDS_PATH") == [real]
        # The separator-only cases yield nothing at all.
        monkeypatch.setenv("MA_CMDS_PATH", sep)
        assert Env("ma").paths("CMDS_PATH") == []

    def test_explicit_dot_segment_is_still_honored(self, monkeypatch):
        """Unlike an EMPTY segment, an explicit '.' segment is real content
        and must still be returned -- dropping empties must not overreach."""
        monkeypatch.setenv("MA_CMDS_PATH", ".")
        assert Env("ma").paths("CMDS_PATH", ty=pathlib.Path) == [pathlib.Path(".")]

    @pytest.mark.skipif(
        _os.name != "nt", reason="a bare drive segment only arises on Windows"
    )
    def test_bare_drive_segment_rejected(self, monkeypatch):
        """A misconfigured separator that splits an absolute Windows path on
        its own drive-letter colon (``PATHSEP=\\`` on ``C:\\...\\cmds``)
        produces a bare ``"C:"`` segment -- Windows resolves that to "the
        current directory on drive C", an ambient lookup that must be
        rejected outright rather than silently importing whatever that
        happens to be (a security-relevant fix)."""
        monkeypatch.delenv("PATHSEP", raising=False)
        monkeypatch.setenv("MA_PATHSEP", "\\")
        monkeypatch.setenv("MA_CMDS_PATH", "C:\\Users\\someone\\cmds")
        with pytest.raises(ValueError, match="bare drive segment"):
            Env("ma").paths("CMDS_PATH")

    def test_segment_resolving_to_cwd_rejected(self, monkeypatch, tmp_path):
        """A segment that resolves to the CURRENT WORKING DIRECTORY -- by
        whatever means -- is rejected the same way, unless it is spelled
        exactly '.' (see test_explicit_dot_segment_is_still_honored)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PATHSEP", raising=False)
        monkeypatch.setenv("MA_CMDS_PATH", str(tmp_path))
        with pytest.raises(ValueError, match="current working directory"):
            Env("ma").paths("CMDS_PATH")

    def test_dot_segment_allowed_even_though_it_resolves_to_cwd(
        self, monkeypatch, tmp_path
    ):
        """The one exception to the rule above: '.' is the explicit,
        documented way to mean the CWD, and must not be rejected."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PATHSEP", raising=False)
        monkeypatch.setenv("MA_CMDS_PATH", ".")
        assert Env("ma").paths("CMDS_PATH") == ["."]


class TestIterAndLen:
    def test_iter_dedupes_env_over_environ(self, monkeypatch):
        monkeypatch.setenv("MA_ONE", "x")
        monkeypatch.setenv("MA_TWO", "y")
        e = Env("ma", TWO="override", THREE="z")
        keys = set(e)
        # _env keys (TWO, THREE) + prefix-matching environ keys (ONE, TWO),
        # TWO de-duped so it appears once.
        assert keys == {"ONE", "TWO", "THREE"}

    def test_len_counts_deduped_keys(self, monkeypatch):
        monkeypatch.setenv("MA_ONE", "x")
        monkeypatch.setenv("MA_TWO", "y")
        e = Env("ma", TWO="override", THREE="z")
        assert len(e) == 3

    def test_len_empty(self, monkeypatch):
        # Ensure no ZZ_-prefixed vars leak in from the outer environment.
        for key in list(__import__("os").environ):
            if key.startswith("ZZ_"):
                monkeypatch.delenv(key, raising=False)
        assert len(Env("zz")) == 0

    def test_ignores_non_prefixed_environ(self, monkeypatch):
        monkeypatch.setenv("OTHER_KEY", "nope")
        monkeypatch.setenv("MA_MINE", "yep")
        assert set(Env("ma")) == {"MINE"}


class TestSetDelItem:
    def test_setitem_stringifies(self):
        e = Env("ma")
        e["PORT"] = 8080
        assert e["PORT"] == "8080"

    def test_delitem(self):
        e = Env("ma", KEY="v")
        del e["KEY"]
        assert "KEY" not in e._env


class TestMappingProtocol:
    """MutableMapping surface: pop / popitem / iteration after seeding."""

    def test_pop_stored_key(self):
        e = Env("ma", KEY="v")
        assert e.pop("KEY") == "v"
        assert "KEY" not in e

    def test_pop_missing_returns_default(self):
        e = Env("ma")
        assert e.pop("NOPE", "fallback") == "fallback"

    def test_pop_environ_backed_key_tombstones_without_touching_os_environ(
        self, monkeypatch
    ):
        """``pop()`` on an environ-backed key succeeds via an in-object tombstone.

        The real process environment is never mutated: ``os.environ`` still has
        the key afterward, but THIS ``Env`` no longer reports it -- until a fresh
        explicit write clears the tombstone again.
        """
        monkeypatch.setenv("MA_HOST", "example.com")
        e = Env("ma")
        assert e["HOST"] == "example.com"  # readable
        assert e.pop("HOST", "dflt") == "example.com"
        assert "HOST" not in e
        assert _os.environ["MA_HOST"] == "example.com"  # os.environ untouched
        with pytest.raises(KeyError):
            e["HOST"]
        # An explicit write after the tombstone makes the key visible again.
        e["HOST"] = "again"
        assert e["HOST"] == "again"

    def test_pop_missing_entirely_raises(self):
        """A key absent from every layer still raises, tombstone or not."""
        e = Env("ma", autoload=False)
        with pytest.raises(KeyError):
            e.pop("NOPE")

    def test_popitem_removes_a_seeded_pair(self):
        e = Env("ma", ONLY="one")
        key, value = e.popitem()
        assert (key, value) == ("ONLY", "one")
        assert "ONLY" not in e

    def test_clear_empties_an_environ_backed_env(self, monkeypatch):
        """``clear()`` must actually empty the mapping, environ-backed keys included."""
        monkeypatch.setenv("MA_HOST", "example.com")
        monkeypatch.setenv("MA_PORT", "8080")
        e = Env("ma", LOCAL="1")
        assert len(e) == 3
        e.clear()
        assert len(e) == 0
        assert list(e) == []
        # os.environ itself is untouched.
        assert _os.environ["MA_HOST"] == "example.com"

    def test_iteration_after_seeding_dedupes(self, monkeypatch):
        monkeypatch.setenv("MA_FROM_ENVIRON", "1")
        e = Env("ma", FROM_KWARG="2", FROM_ENVIRON="override")
        keys = set(e)
        assert "FROM_KWARG" in keys
        assert "FROM_ENVIRON" in keys
        # The seeded value wins and the key is not yielded twice.
        assert list(e).count("FROM_ENVIRON") == 1
        assert e["FROM_ENVIRON"] == "override"

    def test_update_then_iterate(self):
        e = Env("ma")
        e.update({"A": 1, "B": 2})
        assert dict(e) == {"A": "1", "B": "2"}  # values str-coerced via __setitem__


class TestCompanionModuleAutoload:
    def test_autoload_from_companion_module(self, monkeypatch, tmp_path):
        # An app can ship "<prefix-lower>env.py" of defaults; prove it loads.
        # The companion module name is f"{prefix.lower()}env": for Env("app")
        # the normalized prefix is "APP_", so the module is "app_env".
        module = tmp_path / "app_env.py"
        module.write_text("DEBUG = 'yes'\nHOSTS = 'a:b'\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        e = Env("app")
        assert e["DEBUG"] == "yes"
        assert e.list("HOSTS") == ["a", "b"]

    def test_kwargs_override_companion_module(self, monkeypatch, tmp_path):
        module = tmp_path / "app_env.py"
        module.write_text("TOKEN = 'from-module'\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        e = Env("app", TOKEN="from-kwarg")
        assert e["TOKEN"] == "from-kwarg"

    def test_missing_companion_module_does_not_raise(self):
        # The common case: no companion module. Must not error.
        e = Env("definitely_no_such_prefix_zzz")
        assert e._env == {}

    def test_real_environ_wins_over_companion_module(self, monkeypatch, tmp_path):
        # Regression: the companion module seeds DEFAULTS -- lowest
        # precedence. A real exported PRECA_CMDS_PATH must win over a module
        # that ships CMDS_PATH = "from-module", not be silently shadowed by
        # it (the bug: __getitem__ checked the module-seeded value first).
        # Unique prefix/module name per test -- a companion module is cached
        # in sys.modules once imported, so a shared name across tests would
        # silently reuse an earlier test's module content.
        module = tmp_path / "preca_env.py"
        module.write_text("CMDS_PATH = 'from-module'\nDEBUG = '0'\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.setenv("PRECA_CMDS_PATH", "from-environ")
        monkeypatch.setenv("PRECA_DEBUG", "1")
        e = Env("preca")
        assert e["CMDS_PATH"] == "from-environ"
        assert e.paths("CMDS_PATH") == ["from-environ"]
        assert e.bool("DEBUG") is True

    def test_companion_module_used_only_when_environ_unset(self, monkeypatch, tmp_path):
        module = tmp_path / "precb_env.py"
        module.write_text("TOKEN = 'from-module'\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.delenv("PRECB_TOKEN", raising=False)
        e = Env("precb")
        assert e["TOKEN"] == "from-module"

    def test_kwargs_still_win_over_real_environ_and_companion_module(
        self, monkeypatch, tmp_path
    ):
        module = tmp_path / "precc_env.py"
        module.write_text("TOKEN = 'from-module'\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.setenv("PRECC_TOKEN", "from-environ")
        e = Env("precc", TOKEN="from-kwarg")
        assert e["TOKEN"] == "from-kwarg"

    def test_iter_includes_companion_module_only_keys(self, monkeypatch, tmp_path):
        module = tmp_path / "precd_env.py"
        module.write_text("ONLYMODULE = 'x'\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        e = Env("precd")
        assert "ONLYMODULE" in list(e)

    def test_broken_companion_module_import_error_propagates(
        self, monkeypatch, tmp_path
    ):
        """An ImportError raised INSIDE an existing companion module
        (not the companion's own absence) must propagate, not be swallowed.

        ``from os import no_such_name`` raises a plain ``ImportError`` (not a
        ``ModuleNotFoundError`` for the companion's own name), so the narrowed
        ``except ModuleNotFoundError`` in ``Env.__init__`` never catches it.
        """
        module = tmp_path / "prece_env.py"
        module.write_text("from os import no_such_name_at_all\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        with pytest.raises(ImportError):
            Env("prece")

    def test_broken_companion_module_missing_dependency_propagates(
        self, monkeypatch, tmp_path
    ):
        """A companion module's OWN failed import (e.g. a stdlib module
        missing on the 3.9 floor, or any other missing dependency) must
        propagate -- it is a different name than the companion module itself,
        so it is not mistaken for "no companion module shipped"."""
        module = tmp_path / "precf_env.py"
        module.write_text("import duho_totally_missing_dep_xyz\nDEBUG = '1'\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        with pytest.raises(ModuleNotFoundError):
            Env("precf")

    def test_autoload_filters_and_coerces(self, tmp_path, monkeypatch):
        module = tmp_path / "foo_env.py"
        module.write_text(
            "DEBUG = True\n"
            "_private = 1\n"
            "helper = object()\n"
            "import os as _os_alias\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.delitem(sys.modules, "foo_env", raising=False)

        e = Env("foo")
        # str()-coerced (a real bool would crash env.bool otherwise).
        assert e["DEBUG"] == "True"
        assert isinstance(e["DEBUG"], str)
        # env.bool does not crash on a real bool and reads True.
        assert e.bool("DEBUG") is True
        # Only the UPPER_CASE, non-underscore module variable is exposed
        # through the public mapping -- the private helper, the lower-case
        # import alias, and every dunder are filtered out (checked through
        # the mapping `e` itself, since autoload seeds `_defaults`, not
        # `_env`).
        assert "_private" not in e
        assert "helper" not in e
        assert not any(k.startswith("_") for k in e)
        assert set(e) == {"DEBUG"}

    def test_autoload_false_skips_import(self, tmp_path, monkeypatch):
        module = tmp_path / "bar_env.py"
        module.write_text("raise RuntimeError('should not be imported')\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.delitem(sys.modules, "bar_env", raising=False)

        # autoload=False must not import the (raising) canary module.
        e = Env("bar", autoload=False)
        assert e._env == {}


class TestAutoloadSkippedForUnsafePrefix:
    """Autoload never runs for a prefix that would import something
    other than a genuine ``<prefix>env`` companion module."""

    def test_empty_prefix_does_not_autoload(self, monkeypatch, tmp_path):
        """``Env("")`` must NOT import a bare top-level ``env`` module -- a
        very common name for a project's own settings module."""
        module = tmp_path / "env.py"
        module.write_text("SIDE_EFFECT = True\nDEBUG = 'yes'\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        import sys

        monkeypatch.delitem(sys.modules, "env", raising=False)
        e = Env("")
        assert "env" not in sys.modules
        assert e._defaults == {}

    def test_dotted_prefix_does_not_autoload(self, monkeypatch, tmp_path):
        """``Env("my.app")`` must NOT import the unrelated top-level package
        ``my`` while looking for ``my.app_env`` -- the dot survives prefix
        normalisation (only ``-`` is replaced), so this is a real risk."""
        pkg = tmp_path / "my"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("SIDE_EFFECT = True\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        import sys

        monkeypatch.delitem(sys.modules, "my", raising=False)
        e = Env("my.app")
        assert "my" not in sys.modules
        assert e._defaults == {}

    def test_plain_prefix_still_autoloads(self, monkeypatch, tmp_path):
        """Sanity check: a normal, valid prefix is unaffected by the guard."""
        module = tmp_path / "plainapp_env.py"
        module.write_text("DEBUG = 'yes'\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        e = Env("plainapp")
        assert e["DEBUG"] == "yes"


class TestBoolAnnotationNotShadowed:
    """On Python 3.14 (PEP 649 lazy annotations), a class body defining
    a method named ``bool`` next to a ``bool``-typed annotation must not have
    that annotation resolve to the method itself."""

    def test_get_type_hints_resolve_to_builtin_bool(self):
        import typing

        hints = typing.get_type_hints(Env.__init__)
        assert hints["autoload"] is bool

        method_hints = typing.get_type_hints(Env.bool)
        assert method_hints["return"] is bool
