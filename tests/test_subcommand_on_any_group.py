"""``duho.subcommand(parent)`` registers a child on any ``Cmd`` group."""

import duho


def test_decorator_registers_on_a_plain_cmd_group():
    class Group(duho.Cmd):
        """A group."""

    @duho.subcommand(Group)
    class Leaf(duho.Cmd):
        def __call__(self):
            return 5

    assert Group._subcommands_ == [Leaf]
    assert Leaf.__name__ == "Leaf"
    inst = duho.parse(Group, ["leaf"])
    assert type(inst) is Leaf
    assert inst() == 5


def test_decorator_is_idempotent_and_does_not_touch_siblings():
    class A(duho.Cmd):
        pass

    class B(duho.Cmd):
        pass

    class Leaf(duho.Cmd):
        pass

    duho.subcommand(A)(Leaf)
    duho.subcommand(A)(Leaf)
    assert A._subcommands_ == [Leaf]
    assert getattr(B, "_subcommands_", None) is None


def test_register_subcmd_lives_on_cmd_and_cli_keeps_subcommand():
    class Leaf(duho.Cmd):
        pass

    assert "_register_subcmd_" in vars(duho.Cmd)
    assert "_register_subcmd_" not in vars(duho.Cli)
    assert not hasattr(duho.Cmd, "subcommand")

    class Root(duho.Cli):
        pass

    assert Root.subcommand(Leaf) is Leaf
    assert Root._subcommands_ == [Leaf]


def test_decorator_copies_an_inherited_list():
    class Base(duho.Cmd):
        pass

    class First(duho.Cmd):
        pass

    class Second(duho.Cmd):
        pass

    duho.subcommand(Base)(First)

    class Derived(Base):
        pass

    duho.subcommand(Derived)(Second)
    assert Base._subcommands_ == [First]
    assert Derived._subcommands_ == [First, Second]


def test_exported_at_the_root():
    assert "subcommand" in duho.__all__
    assert "subcommand" in duho.args.__all__
