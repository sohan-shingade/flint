"""Consolidated legacy-vs-Nautilus parity suite (§19.4, plan §A8, N6).

This package is the CI-required superset harness that the N9 default flip gates
on. The per-phase parity tests (``tests/test_nautilus_*.py``) stay where they
are; ``tests/parity/`` re-runs the §19.3 golden set through one canonical
harness that proves both §19.4 layers on every golden.
"""
