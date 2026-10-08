"""The ``duho.config`` backends, registry and module functions."""

import io
import sys

import pytest

import duho
from duho import config
from duho.config import _backend
from duho.exceptions import (
    ConfigDependencyError,
    ConfigError,
    UnsupportedFormatError,
)

SECRET = "hunter2-planted-secret"

DATA = {"host": "example", "name": "über", "web": {"port": "8080", "debug": "yes"}}

# A document with a syntax error on line 2 and a secret value on line 1.
BAD = {
    "json": f'{{"key": "{SECRET}",\n  oops}}',
    "toml": f'key = "{SECRET}"\nkey2 = = 1\n',
    "yaml": f"key: {SECRET}\n\tkey2: 1\n",
    "ini": f"[s]\nkey = {SECRET}\nnot a pair\n",
}


@pytest.fixture(autouse=True)
def _restore_registry():
    saved = dict(_backend._REGISTRY)
    yield
    _backend._REGISTRY.clear()
    _backend._REGISTRY.update(saved)


def _need(name):
    if name == "yaml":
        pytest.importorskip("yaml")
    if name == "toml":
        pytest.importorskip("tomli_w")


@pytest.mark.parametrize("name", ["json", "toml", "yaml", "ini"])
def test_round_trip(name):
    _need(name)
    assert config.loads(config.dumps(DATA, name), name) == DATA


@pytest.mark.parametrize("name", ["json", "toml", "yaml", "ini"])
def test_syntax_error_is_a_config_error_with_a_position_and_no_text(name, tmp_path):
    _need(name)
    path = tmp_path / f"bad.{name}"
    path.write_text(BAD[name], encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        config.load(path)
    exc = caught.value
    assert isinstance(exc, ValueError)
    assert exc.path == str(path)
    assert f"invalid {name.upper()} in config file {path}" in str(exc)
    if name != "ini":
        assert exc.lineno == 2
    else:
        assert exc.lineno == 3
    assert SECRET not in str(exc)


def test_ini_reports_each_parser_problem_in_fixed_words():
    backend = config.INIBackend()
    for text, word in [
        ("key = 1\n", "no section header"),
        ("[a]\n[a]\n", "defined twice"),
        ("[a]\nk = 1\nk = 2\n", "defined twice"),
        ("[a]\nnot a pair\n", "not a key and value"),
    ]:
        with pytest.raises(ConfigError, match=word) as caught:
            backend.loads(text)
        assert caught.value.lineno is not None


def test_ini_shape():
    text = "[DEFAULT]\nPort = 9\n\n[web]\nHost = a%b\n"
    assert config.loads(text, "ini") == {"Port": "9", "web": {"Host": "a%b"}}
    # A table does not inherit the top level.
    assert config.loads(text, "ini")["web"] == {"Host": "a%b"}


def test_ini_dump_scalars_and_errors():
    text = config.dumps({"flag": True, "n": 3, "web": {"off": False}}, "ini")
    assert "flag = true" in text and "off = false" in text and "n = 3" in text
    for bad, key in [
        ({"items": [1]}, "items"),
        ({"a": {"b": {"c": 1}}}, "a.b"),
        ({"a": None}, "a"),
    ]:
        with pytest.raises(ConfigError, match=key):
            config.dumps(bad, "ini")


@pytest.mark.parametrize(
    "name, modules",
    [
        ("yaml", ["yaml"]),
        ("toml", ["tomllib", "tomli"]),
    ],
)
def test_missing_library_names_the_extra(name, modules, monkeypatch):
    for module in modules:
        monkeypatch.setitem(sys.modules, module, None)
    with pytest.raises(ConfigDependencyError) as caught:
        config.loads("a: 1" if name == "yaml" else "a = 1", name)
    extra = {"yaml": "yaml", "toml": "config"}[name]
    assert caught.value.extra == extra
    assert f"duho[{extra}]" in str(caught.value)


def test_missing_writer_names_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "tomli_w", None)
    with pytest.raises(ConfigDependencyError, match=r"duho\[config\]"):
        config.dumps({"a": 1}, "toml")


def test_a_missing_library_is_not_rewrapped_by_load(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", None)
    path = tmp_path / "a.yaml"
    path.write_text("a: 1\n")
    with pytest.raises(ConfigDependencyError):
        config.load(path)


def test_load_decodes_strict_utf8_and_drops_one_bom(tmp_path):
    path = tmp_path / "a.json"
    path.write_bytes(b"\xef\xbb\xbf" + '{"a": "é"}'.encode())
    assert config.load(path) == {"a": "é"}
    path.write_bytes(b'{"a": "\xff"}')
    with pytest.raises(ConfigError, match="byte offset 7"):
        config.load(path)


def test_load_takes_a_file_object_and_uses_its_name(tmp_path):
    path = tmp_path / "a.json"
    path.write_text('{"a": 1}')
    with open(path, "rb") as handle:
        assert config.load(handle) == {"a": 1}
    with pytest.raises(UnsupportedFormatError):
        config.load(io.StringIO("{}"))
    assert config.load(io.StringIO('{"a": 2}'), "json") == {"a": 2}


def test_load_expands_the_home_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / "c.json").write_text('{"a": 1}')
    assert config.load("~/c.json") == {"a": 1}


def test_dump_writes_utf8_without_translating_newlines(tmp_path):
    path = tmp_path / "a.json"
    config.dump({"a": "é"}, path)
    assert path.read_bytes() == b'{\n  "a": "\xc3\xa9"\n}\n'
    out = io.StringIO()
    config.dump({"a": 1}, out, "json")
    assert out.getvalue() == '{\n  "a": 1\n}\n'


def test_dump_leaves_an_existing_file_when_dumps_raises(tmp_path):
    path = tmp_path / "a.ini"
    path.write_text("keep me")
    with pytest.raises(ConfigError):
        config.dump({"a": [1]}, path)
    assert path.read_text() == "keep me"


def test_base_class_methods_raise_not_implemented():
    class Bare(config.ConfigBackend):
        pass

    with pytest.raises(NotImplementedError, match="Bare"):
        Bare().loads("")
    with pytest.raises(NotImplementedError, match="Bare"):
        Bare().dumps({})


def test_names_and_lookup():
    assert config.backend_names() == ("ini", "json", "toml", "yaml")
    assert isinstance(config.get_backend("YML"), config.YAMLBackend)
    assert isinstance(config.get_backend("Json"), config.JSONBackend)
    with pytest.raises(UnsupportedFormatError, match="ini, json, toml, yaml"):
        config.get_backend("hocon")


@pytest.mark.parametrize(
    "file, cls",
    [
        ("a.JSON", config.JSONBackend),
        ("a.yml", config.YAMLBackend),
        ("a.cfg", config.INIBackend),
        ("/x/y/a.toml", config.TOMLBackend),
    ],
)
def test_backend_for_a_suffix_ignores_case(file, cls):
    assert type(config.backend_for(file)) is cls


def test_backend_for_explicit_format_beats_suffix_and_default_is_last():
    assert type(config.backend_for("a.json", "toml")) is config.TOMLBackend
    assert type(config.backend_for("noext", default="ini")) is config.INIBackend
    with pytest.raises(UnsupportedFormatError, match=r"\.cfg, \.ini, \.json"):
        config.backend_for("a.xyz")


def test_a_subclass_with_a_name_registers_itself():
    class Hocon(config.ConfigBackend):
        name = "hocon"
        aliases = ("conf",)
        suffixes = (".hocon",)

        def loads(self, text):
            return {"text": text}

    assert "hocon" in config.backend_names()
    assert config.loads("x", "CONF") == {"text": "x"}
    assert type(config.backend_for("a.hocon")) is Hocon


def test_a_subclass_with_no_name_of_its_own_is_not_registered():
    class Plain(config.JSONBackend):
        pass

    assert config.backend_names() == ("ini", "json", "toml", "yaml")
    assert Plain.name == "json"


@pytest.mark.parametrize(
    "body",
    [
        'name = "json"',
        'name = "other"; aliases = ("JSON",)',
        'name = "other"; suffixes = (".JSON",)',
    ],
)
def test_a_clashing_name_alias_or_suffix_is_refused(body):
    with pytest.raises(ValueError, match="already holds"):
        exec(f"class Clash(config.ConfigBackend):\n    {body}\n", {"config": config})
    assert config.backend_names() == ("ini", "json", "toml", "yaml")


def test_replace_takes_over_a_registered_backend():
    class Mine(config.ConfigBackend, replace=True):
        name = "json"
        suffixes = (".json",)

        def loads(self, text):
            return "mine"

    assert config.backend_names() == ("ini", "json", "toml", "yaml")
    assert config.loads("{}", "json") == "mine"
    assert type(config.backend_for("a.json")) is Mine


def test_a_set_restricts_the_formats(tmp_path):
    assert config.backend_names(backends=["json", config.INIBackend]) == (
        "ini",
        "json",
    )
    with pytest.raises(UnsupportedFormatError):
        config.backend_for("a.toml", backends=["json"])
    with pytest.raises(UnsupportedFormatError):
        config.get_backend("yaml", backends=["json"])
    with pytest.raises(UnsupportedFormatError):
        config.backend_names(backends=["nope"])


def test_an_instance_carries_its_own_names_and_suffixes():
    ini = config.INIBackend(name="settings", aliases=["set"], suffixes=[".conf"])
    assert config.backend_for("a.conf", backends=[ini]) is ini
    assert config.get_backend("SET", backends=[ini]) is ini
    assert ini.name == "settings" and config.INIBackend.name == "ini"
    assert config.INIBackend.suffixes == (".ini", ".cfg")


def test_a_later_item_wins_a_suffix():
    first = config.JSONBackend(name="first")
    second = config.JSONBackend(name="second")
    assert config.backend_for("a.json", backends=[first, second]) is second
    assert config.backend_for("a.json", backends=[second, first]) is first


def test_an_unregistered_subclass_replaces_a_builtin_in_a_set(tmp_path):
    class Loud(config.JSONBackend):
        def loads(self, text):
            return "loud"

    path = tmp_path / "a.json"
    path.write_text("{}")
    assert config.load(path, backends=[Loud]) == "loud"
    assert config.load(path) == {}


def test_the_empty_suffix_matches_any_name_and_loses_to_a_longer_one():
    anything = config.INIBackend(name="any", suffixes=[""])
    items = ["json", anything]
    assert config.backend_for("noext", backends=items) is anything
    assert config.backend_for("a.unknown", backends=items) is anything
    assert type(config.backend_for("a.json", backends=items)) is config.JSONBackend


def test_a_set_item_that_is_not_a_backend_is_a_type_error():
    with pytest.raises(TypeError):
        config.backend_names(backends=[3])


def test_importing_duho_config_loads_no_parser():
    import subprocess

    from conftest import subprocess_env

    code = (
        "import sys, duho.config\n"
        "print(sorted(m for m in ('json','configparser','yaml','tomllib','tomli',"
        "'tomli_w') if m in sys.modules))\n"
    )
    out = subprocess.check_output(
        [sys.executable, "-c", code], text=True, env=subprocess_env()
    )
    assert out.strip() == "[]"


def test_the_attribute_is_lazy_on_the_root_package():
    assert duho.config is config
    assert "config" in duho.__all__
