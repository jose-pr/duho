"""Tests for duho.env.Env (prefixed environment accessor)."""

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

    def test_missing_key_single_empty_element(self):
        # Intentional split contract: "".split(":") == [""], so a missing
        # or empty var yields one empty-string element, not [].
        assert Env("ma").list("MISSING") == [""]

    def test_empty_value_single_empty_element(self, monkeypatch):
        monkeypatch.setenv("MA_EMPTY", "")
        assert Env("ma").list("EMPTY") == [""]


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
        # Ensure no MA_-prefixed vars leak in from the outer environment.
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
