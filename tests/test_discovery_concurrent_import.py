"""A file imported by several threads at once runs its module body once."""

import threading

from duho.discovery import import_from_path

_BODY = """\
import pathlib, time

time.sleep(0.05)
with open(pathlib.Path(__file__).with_name("count.txt"), "a") as fh:
    fh.write("x")
"""


def test_concurrent_first_import_executes_module_body_once(tmp_path):
    path = tmp_path / "step.py"
    path.write_text(_BODY)
    count = 8
    barrier = threading.Barrier(count)
    modules = []

    def worker():
        barrier.wait()
        modules.append(import_from_path("duho._test_concurrent.step", path))

    threads = [threading.Thread(target=worker) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(modules) == count
    assert len({id(m) for m in modules}) == 1
    assert (tmp_path / "count.txt").read_text() == "x"
