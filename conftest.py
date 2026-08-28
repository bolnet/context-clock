"""Pytest collection boundaries for the repo suite.

The cache benchmark's agent writes real Python — including its own test suite —
into a workspace under `results/`. Those tests belong to the workload being
measured, not to context-clock: collecting them would make this repo's suite
pass or fail on whatever the model happened to write, and a red agent workspace
is a *valid measurement*, not a broken build.
"""

collect_ignore_glob = ["results/*", "logs/*"]
