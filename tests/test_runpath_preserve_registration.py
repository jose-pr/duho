"""``duho.runpath.preserve_registration`` restores the registration on exit."""

import pytest

import duho
from duho import runpath


@pytest.fixture(autouse=True)
def _adapter():
    saved = runpath._ADAPTER
    try:
        yield
    finally:
        runpath._ADAPTER = saved


class _Base(duho.LoggingArgs):
    pass


def _adapt(entrypoint):
    return entrypoint


def _providers():
    return [p for p in duho.discovery._PROVIDERS if p[0] is runpath.is_runpath_dir]


def test_a_changed_base_and_adapter_are_restored():
    before = (runpath._BASE, runpath._ADAPTER)
    with runpath.preserve_registration():
        runpath.register(base=_Base, step_adapter=_adapt)
        assert (runpath._BASE, runpath._ADAPTER) == (_Base, _adapt)
    assert (runpath._BASE, runpath._ADAPTER) == before
    assert runpath._REGISTERED is not None and len(_providers()) == 1


def test_an_unregister_is_undone():
    runpath.register()
    with runpath.preserve_registration():
        runpath.unregister()
        assert _providers() == []
    assert runpath._REGISTERED is not None
    assert len(_providers()) == 1


def test_a_first_register_is_undone():
    runpath.unregister()
    with runpath.preserve_registration():
        runpath.register(base=_Base)
        assert len(_providers()) == 1
    assert runpath._REGISTERED is None
    assert _providers() == []
    assert runpath._BASE is not _Base


def test_it_restores_when_the_block_raises():
    before = runpath._BASE
    with pytest.raises(RuntimeError):
        with runpath.preserve_registration():
            runpath.register(base=_Base)
            runpath.unregister()
            raise RuntimeError("boom")
    assert runpath._BASE is before
    assert runpath._REGISTERED is not None


def test_the_names_stay_assignable():
    before = runpath._BASE
    runpath._BASE = _Base
    assert runpath._BASE is _Base
    runpath._BASE = before


def test_it_is_public():
    assert "preserve_registration" in runpath.__all__


def test_registration_reads_the_base_the_adapter_and_the_provider():
    with runpath.preserve_registration():
        runpath.unregister()
        runpath.register(base=_Base, step_adapter=_adapt)
        assert runpath.registration() == (_Base, _adapt, True)
        runpath.unregister()
        now = runpath.registration()
        assert (now.base, now.step_adapter, now.registered) == (_Base, _adapt, False)
        runpath.register(step_adapter=None)
        assert runpath.registration().step_adapter is None


def test_step_wants_context_is_the_runners_own_answer():
    import functools

    def one(cmd):
        pass

    def two(cmd, ctx):
        pass

    def any_number(*args):
        pass

    @functools.wraps(two)
    def passthrough(*args, **kwargs):
        return two(*args, **kwargs)

    assert runpath.step_wants_context(one) is False
    assert runpath.step_wants_context(two) is True
    assert runpath.step_wants_context(any_number) is True
    assert runpath.step_wants_context(passthrough) is True
    assert runpath.step_wants_context is runpath._step_wants_ctx


def test_the_new_names_are_public():
    for name in ("registration", "RunPathRegistration", "step_wants_context"):
        assert name in runpath.__all__
