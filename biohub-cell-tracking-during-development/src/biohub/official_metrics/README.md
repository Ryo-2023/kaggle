# Vendored official Biohub metrics

`metrics.py` and `division_metrics.py` are unmodified copies from the official
Royer Lab baseline. The exact upstream repository, commit and blob SHAs are
recorded in `__init__.py`; the upstream BSD-3-Clause license is included as
`LICENSE`.

Do not add project-specific behavior to these two files. Put adapters,
visualization and experiment code outside this package so that the evaluator
can be refreshed and hash-checked against upstream.
