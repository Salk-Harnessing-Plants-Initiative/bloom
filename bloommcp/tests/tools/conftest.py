"""Shared fixtures for `tests/tools/` (#713).

`viz_env` moved here from the former `test_viz_tools.py` so `test_viz_snapshot.py` could
reuse the exact same real-TRAITS_DIR-read / manifest-miss setup rather than maintain a
second copy that could silently desync. Post-#462 no plot tool writes to `PLOTS_DIR` (the
last two that did were retired into `heritability_analysis`), so the fixture's `plots`
directory now serves only as the scratch location `test_viz_snapshot.py` copies committed
bytes into.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bloom_mcp import experiment_utils as eu

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_RAW = _FIXTURES / "turface_19_final_data.csv"
_EXPERIMENT = "turface_19.csv"


@pytest.fixture
def viz_env(monkeypatch, tmp_path, fake_supabase_storage):
    """Real TRAITS_DIR read, versioned-manifest lookup misses; `plots` is scratch space."""
    traits = tmp_path / "traits"
    traits.mkdir()
    shutil.copy(_RAW, traits / _EXPERIMENT)
    monkeypatch.setattr(eu, "TRAITS_DIR", traits)

    plots = tmp_path / "plots"
    monkeypatch.setattr(eu, "PLOTS_DIR", plots)
    return plots


class CountingLock:
    """Proxy counting acquisitions of `FIGURE_REGISTRY_LOCK`, delegating to the real one.

    Shared here rather than copied per test file (#808 review): three suites need it,
    and a drifting copy is how the call-site lists this change corrects went stale.

    Patched onto the *module attribute* (`monkeypatch.setattr(_plots,
    "FIGURE_REGISTRY_LOCK", CountingLock(real))`), which is the only option:
    `threading.Lock` is a C type whose `acquire` is read-only (`'_thread.lock' object
    attribute 'acquire' is read-only`), so the lock object itself cannot be patched.

    Counts `acquire()` as well as `__enter__`, so an implementation rewritten as an
    explicit acquire/release pair is measured rather than silently counting zero.
    """

    def __init__(self, real):
        self._real = real
        self.entries = 0

    def __enter__(self):
        self.entries += 1
        return self._real.__enter__()

    def __exit__(self, *exc):
        return self._real.__exit__(*exc)

    def acquire(self, *a, **k):
        self.entries += 1
        return self._real.acquire(*a, **k)

    def release(self):
        return self._real.release()

    def locked(self):
        return self._real.locked()
