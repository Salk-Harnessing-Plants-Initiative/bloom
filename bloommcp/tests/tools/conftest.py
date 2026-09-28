"""Shared fixtures and helpers for `tests/tools/` (#713, #768).

`viz_env` moved here from the former `test_viz_tools.py` so `test_viz_snapshot.py` could
reuse the exact same real-TRAITS_DIR-read / manifest-miss setup rather than maintain a
second copy that could silently desync. Post-#462 no plot tool writes to `PLOTS_DIR` (the
last two that did were retired into `heritability_analysis`), so the fixture's `plots`
directory now serves only as the scratch location `test_viz_snapshot.py` copies committed
bytes into.

`_render_to_dir` and `SNAPSHOT_TOL` moved here from `test_viz_snapshot.py` for the same
reason when #768 added `test_viz_cell_oracle.py`: that file needs to run a tool through its
real entrypoint (to prove its own subject is the artifact the tool really commits) and to
compare against the *same* tolerance the whole-image snapshot suite uses. Both files
importing one definition is what keeps "the cell oracle misses what RMS at `_TOL` misses"
a statement about one number rather than two that can drift apart.
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


# Empirically derived, not guessed -- see `test_viz_snapshot.py`'s module docstring (Tolerance
# and Known limitation sections) and `add-bloommcp-plot-snapshot-tests`' design.md Decisions 2
# & 3 for the measurement and the fallback plan. Changing this number should come with a fresh
# measurement, not a vibe. Lives here rather than in `test_viz_snapshot.py` so that
# `test_viz_cell_oracle.py`'s negative control asserts RMS misses a defect at the *same*
# tolerance the production comparison uses, not a second copy of it (#768).
SNAPSHOT_TOL = 15


def render_tool_to_dir(label, module, fn_name, viz_env):
    """Run one plotting tool through its real entrypoint; return the dir its PNG(s) landed in.

    The 3 converged tools write into a ``ResultStore`` staging dir that ``commit`` deletes on
    success, so their bytes are copied out inside a ``commit`` spy -- the last point at which
    the committed file still exists on disk. Capturing at commit (rather than spying on
    ``savefig``) means the bytes compared are exactly the bytes that were committed, not an
    intermediate render. (A ``PLOTS_DIR`` branch for the two legacy tools lived here until
    #462 retired them; ``viz_env`` now serves only as scratch space.)

    Moved here from `test_viz_snapshot.py` by #768 so `test_viz_cell_oracle.py` can tie its
    per-cell assertions to the tool's really-committed PNG without a second copy of this
    setup drifting from the first.
    """
    fn = getattr(module, fn_name)

    import pandas as pd
    from bloom_mcp.data_access import FakeReader, SupabaseReader
    from bloom_mcp.result_store import FakeResultStore, SupabaseResultStore
    from bloom_mcp.tools import _ports

    captured = viz_env / f"_committed_{label}"
    captured.mkdir(parents=True, exist_ok=True)

    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, pd.read_csv(_RAW))
    store = FakeResultStore()
    real_commit = store.commit

    def _spy_commit(run, outputs):
        for name in outputs:
            shutil.copy(run.staging_dir / name, captured / name)
        return real_commit(run, outputs)

    store.commit = _spy_commit
    _ports.configure(reader=reader, store=store)
    try:
        fn(experiment=_EXPERIMENT)
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    return captured
