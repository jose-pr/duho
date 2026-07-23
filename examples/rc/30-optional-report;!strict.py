"""A `!strict`-tokened step: non-strict for THIS step alone, independent of the rest.

If this step's entrypoint raises, it's logged and the run continues -- even if
`--rcopts strict` is NOT passed, and even though `20-provision.py` (no token)
is strict in the very same run. `;` (not `:`) is used here so this filename is
authorable on Windows too -- both separators parse identically.
"""

AFTER = ["provision"]


def main(cmd):
    print("emailing a status report (best-effort)")
