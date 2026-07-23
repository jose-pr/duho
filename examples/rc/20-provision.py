"""A plain 1-arg step: no `ctx`, unaffected by the lifecycle addition.

No `!strict`/`!enabled` filename token here -> strict by default: if this
step's entrypoint raises, the run stops (even without a run-wide
`--rcopts strict`). Contrast with `30-optional-report;!strict.py`.
"""

REQUIRED = ["check"]


def main(cmd):
    print("provisioning...")
