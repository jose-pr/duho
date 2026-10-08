"""A config file is read by the backend its suffix names."""

import sys

import pytest

import duho
from duho import Args, Cli, config

TEXTS = {
    "app.yaml": "verbose: true\nport: 7\ninstall:\n  target: from-file\n",
    "app.yml": "verbose: true\nport: 7\ninstall:\n  target: from-file\n",
    "app.ini": "[DEFAULT]\nverbose = true\nport = 7\n[install]\ntarget = from-file\n",
    "app.cfg": "[DEFAULT]\nverbose = true\nport = 7\n[install]\ntarget = from-file\n",
    "app.json": '{"verbose": true, "port": 7, "install": {"target": "from-file"}}',
    "app.toml": 'verbose = true\nport = 7\n[install]\ntarget = "from-file"\n',
}


class Install(Args):
    target: str = "default"
    ("--target",)

    def __call__(self):
        return 0


class Root(Cli):
    _parsername_ = "dispatch-root"
    _subcommands_ = [Install]
    verbose: bool = False
    ("--verbose",)
    port: int = 1
    ("--port",)


@pytest.mark.parametrize("file_name", sorted(TEXTS))
def test_a_suffix_selects_the_backend_for_a_root_field_and_a_table(file_name, tmp_path):
    if file_name.endswith((".yaml", ".yml")):
        pytest.importorskip("yaml")
    if file_name.endswith(".toml"):
        pytest.importorskip("tomllib" if sys.version_info >= (3, 11) else "tomli")
    path = tmp_path / file_name
    path.write_text(TEXTS[file_name], encoding="utf-8")
    result = duho.parse(Root, ["install"], config=path)
    assert result.target == "from-file"
    assert result.verbose is True and result.port == 7


@pytest.mark.requires_toml
def test_a_name_with_no_suffix_is_still_toml(tmp_path):
    path = tmp_path / "settings"
    path.write_text("port = 5\n")
    assert duho.parse(Root, ["install"], config=path).port == 5


@pytest.mark.requires_toml
def test_an_unknown_suffix_is_still_toml(tmp_path):
    path = tmp_path / "settings.conf"
    path.write_text("port = 5\n")
    assert duho.parse(Root, ["install"], config=path).port == 5


def test_a_yaml_path_is_no_longer_read_as_toml(tmp_path, capsys):
    pytest.importorskip("yaml")
    path = tmp_path / "app.yaml"
    path.write_text("port: 7\n")
    assert duho.parse(Root, ["install"], config=path).port == 7
    path.write_text("port: [\n")
    with pytest.raises(SystemExit) as info:
        duho.parse(Root, ["install"], config=path)
    assert info.value.code == 2
    assert "invalid YAML" in capsys.readouterr().err


def test_missing_pyyaml_exits_2_naming_the_extra_but_help_works(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setitem(sys.modules, "yaml", None)
    path = tmp_path / "app.yaml"
    path.write_text("port: 7\n")
    with pytest.raises(SystemExit) as info:
        duho.parse(Root, ["install"], config=path)
    assert info.value.code == 2
    assert "duho[yaml]" in capsys.readouterr().err
    with pytest.raises(SystemExit) as info:
        duho.parse(Root, ["--help"], config=path)
    assert info.value.code == 0
    assert "--port" in capsys.readouterr().out


def test_a_restricted_set_refuses_an_unlisted_suffix(tmp_path, capsys):
    class Only(Root):
        _parsername_ = "only-root"
        _config_backends_ = ["json"]

    path = tmp_path / "app.toml"
    path.write_text("port = 5\n")
    with pytest.raises(SystemExit) as info:
        duho.parse(Only, ["install"], config=path)
    assert info.value.code == 2
    assert "accepted suffixes: .json" in capsys.readouterr().err
    # A name with no suffix is refused too: the TOML default needs the full set.
    bare = tmp_path / "bare"
    bare.write_text("{}")
    with pytest.raises(SystemExit):
        duho.parse(Only, ["install"], config=bare)


def test_an_instance_with_another_suffix_reads_conf(tmp_path):
    class Conf(Root):
        _parsername_ = "conf-root"
        _config_backends_ = [config.INIBackend(suffixes=(".conf",))]

    path = tmp_path / "app.conf"
    path.write_text("[DEFAULT]\nport = 6\n")
    assert duho.parse(Conf, ["install"], config=path).port == 6


def test_the_class_config_attribute_uses_the_set_too(tmp_path):
    path = tmp_path / "app.conf"
    path.write_text("[DEFAULT]\nport = 8\n")

    class ByAttribute(Root):
        _parsername_ = "attr-root"
        _config_ = str(path)
        _config_backends_ = [config.INIBackend(suffixes=(".conf",))]

    assert duho.parse(ByAttribute, ["install"]).port == 8


def test_an_application_subclass_serves_its_own_suffix(tmp_path):
    class Shouting(config.INIBackend):
        def loads(self, text):
            data = super().loads(text)
            return {k: v.upper() if isinstance(v, str) else v for k, v in data.items()}

    class Own(Root):
        _parsername_ = "own-root"
        _config_backends_ = [Shouting(name="shout", suffixes=(".ini",))]

    class Name(Cli):
        _parsername_ = "name-root"
        _config_backends_ = [Shouting(suffixes=(".ini",))]
        label: str = "x"
        ("--label",)

    path = tmp_path / "app.ini"
    path.write_text("[DEFAULT]\nlabel = quiet\n")
    assert duho.parse(Name, [], config=path).label == "QUIET"
    assert duho.parse(Own, ["install"], config=path).port == 1


def test_a_loader_still_outranks_the_set(tmp_path):
    class Loaded(Root):
        _parsername_ = "loaded-root"
        _config_backends_ = ["json"]
        _config_loader_ = staticmethod(lambda p: {"port": 3})

    path = tmp_path / "anything.xyz"
    path.write_text("")
    assert duho.parse(Loaded, ["install"], config=path).port == 3


def test_a_subclass_in_the_set_serves_yaml_in_place_of_the_builtin(tmp_path):
    class FixedYaml(config.YAMLBackend):
        def loads(self, text):
            return {"port": 4}

    class Own(Root):
        _parsername_ = "fixed-root"
        _config_backends_ = [FixedYaml]

    path = tmp_path / "app.yaml"
    path.write_text("not: read\n")
    assert duho.parse(Own, ["install"], config=path).port == 4
