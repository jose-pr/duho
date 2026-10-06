"""Breaking two dependency cycles gives one step order whatever the hash seed."""

import subprocess
import sys

from conftest import subprocess_env

_SCRIPT = """\
import logging
from duho.runpath._load import _order_steps
from duho.runpath._steps import _Step

logging.disable(logging.CRITICAL)
noop = lambda *a, **k: None
steps = [
    _Step("s0", 0, ["x", "y"], noop),
    _Step("p", 1, ["x"], noop),
    _Step("q", 2, ["y"], noop),
    _Step("x", 3, ["p"], noop),
    _Step("y", 4, ["q"], noop),
]
print(" ".join(s.name for s in _order_steps(steps)))
"""


def _order(seed):
    env = subprocess_env(extra={"PYTHONHASHSEED": str(seed)})
    done = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def test_two_cycles_break_in_rank_order_for_every_hash_seed():
    orders = {_order(seed) for seed in range(6)}
    assert orders == {"p x q y s0"}
