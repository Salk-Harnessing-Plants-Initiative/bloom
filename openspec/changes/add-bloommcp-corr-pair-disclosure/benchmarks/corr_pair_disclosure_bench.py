"""Reproducible evidence for `design.md`'s cost table, payload table, and fuzz claim.

Run from the bloommcp service so the pinned pandas/numpy are the ones measured::

    cd bloommcp && uv run --extra test python \
        ../openspec/changes/add-bloommcp-corr-pair-disclosure/benchmarks/corr_pair_disclosure_bench.py

Sections (all three run by default; pass ``--cost``/``--payload``/``--fuzz`` to select):

* ``--cost``    — wall-clock of the two existing steps vs the two this change adds, at
                  cylinder width (846 traits). Backs `design.md` Risks.
* ``--payload`` — how many strong pairs realistic trait collinearity produces, and how large
                  the uncapped JSON would be. Backs Decision 2's cap. Also measures the
                  "saturating trait" case that forced `locally_constant_trait_pairs` to be
                  capped too (Risks, "Response size").
* ``--fuzz``    — the taxonomy-totality claim: over randomly degenerate frames, every
                  off-diagonal NaN cell falls into exactly one of the three buckets. Backs
                  Decision 1. Exits non-zero if any cell is unexplained or double-counted.

This file is evidence for the proposal, not a shipped test — the taxonomy property itself is
pinned for CI by ``test_every_nan_cell_has_exactly_one_reason`` in
``bloommcp/tests/tools/test_plot_correlation_matrix_tool.py``.
"""

from __future__ import annotations

import json
import sys
import time
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

MIN_OVERLAP = 10
N_TRAITS = 846  # cylinder width
N_ROWS = 500  # conservative stand-in for plant count; every new step is row-independent


def _buckets(df: pd.DataFrame, min_overlap: int = MIN_OVERLAP):
    """The three disclosure buckets, exactly as `design.md` Decision 1 derives them."""
    cols = list(df.columns)
    zero_variance = [c for c in cols if not (df[c].std(skipna=True) > 0)]
    corr = df[cols].corr(min_periods=min_overlap).to_numpy()
    notna = df[cols].notna().to_numpy(dtype=int)
    overlap = notna.T @ notna

    upper = np.triu(np.ones(corr.shape, dtype=bool), k=1)
    zv_idx = [cols.index(t) for t in zero_variance]
    zv_mask = np.zeros_like(upper)
    zv_mask[zv_idx, :] = True
    zv_mask[:, zv_idx] = True
    # NOTE: the raw sub-threshold MASK, not the published low_overlap_trait_pairs list —
    # that list already excludes zero-variance pairs, so subtracting it would leak them.
    low_mask = (overlap < min_overlap) & upper
    nan_mask = np.isnan(corr) & upper
    residual = nan_mask & ~zv_mask & ~low_mask
    return cols, corr, overlap, upper, nan_mask, zv_mask, low_mask, residual


def bench_cost() -> None:
    rng = np.random.default_rng(0)
    values = rng.normal(size=(N_ROWS, N_TRAITS))
    values[rng.random(values.shape) < 0.15] = np.nan
    df = pd.DataFrame(values, columns=[f"t{i}" for i in range(N_TRAITS)])
    cols = list(df.columns)

    t0 = time.perf_counter()
    corr = df[cols].corr(min_periods=MIN_OVERLAP).to_numpy()
    t1 = time.perf_counter()
    notna = df[cols].notna().to_numpy(dtype=int)
    overlap = notna.T @ notna
    t2 = time.perf_counter()

    upper = np.triu(np.ones(corr.shape, dtype=bool), k=1)
    nan_mask = np.isnan(corr) & upper
    low_mask = (overlap < MIN_OVERLAP) & upper
    _residual = nan_mask & ~low_mask
    t3 = time.perf_counter()

    strong = (np.abs(corr) > 0.7) & upper
    si, sj = np.where(strong)
    np.lexsort((sj, si, -np.abs(corr[si, sj]), overlap[si, sj]))[:50]
    t4 = time.perf_counter()

    print(f"# cost — {N_TRAITS} traits x {N_ROWS} rows, 15% missing")
    print(f"  .corr(min_periods=…)  existing   {t1 - t0:7.3f}s")
    print(f"  notna.T @ notna       existing   {t2 - t1:7.3f}s")
    print(f"  residual-NaN bucket   NEW        {t3 - t2:7.3f}s")
    print(f"  strong-pair sort+cap  NEW        {t4 - t3:7.3f}s")
    added, base = (t3 - t2) + (t4 - t3), (t1 - t0) + (t2 - t1)
    print(f"  -> added {added:.3f}s on {base:.3f}s = {100 * added / base:.1f}%")
    print(f"  one {N_TRAITS}^2 bool mask = {upper.nbytes / 1e6:.1f} MB")


def bench_payload() -> None:
    rng = np.random.default_rng(1)
    entry = {
        "traits": ["trait_name_123", "trait_name_456"],
        "r": 0.8123,
        "overlap_n": 412,
        "ci_low": 0.7712,
        "ci_high": 0.8451,
    }
    per_entry = len(json.dumps(entry))

    print(f"\n# payload — strong pairs by trait collinearity ({per_entry} B/entry)")
    for k in (20, 5, 3):
        latent = rng.normal(size=(N_ROWS, k))
        values = latent @ rng.normal(size=(k, N_TRAITS))
        values += 0.05 * rng.normal(size=values.shape)
        values[rng.random(values.shape) < 0.15] = np.nan
        df = pd.DataFrame(values, columns=[f"t{i}" for i in range(N_TRAITS)])
        corr = df.corr(min_periods=MIN_OVERLAP).to_numpy()
        upper = np.triu(np.ones(corr.shape, dtype=bool), k=1)
        n_strong = int(((np.abs(corr) > 0.7) & upper).sum())
        print(
            f"  {k:2d} latent factors -> {n_strong:>7,} strong pairs "
            f"(~{n_strong * per_entry / 1e6:5.1f} MB uncapped)"
        )

    # The case that forced locally_constant_trait_pairs to be capped as well: one trait that
    # is globally non-constant but takes a single value on every row where it is observed.
    n_traits = 300
    values = rng.normal(size=(N_ROWS, n_traits))
    saturating = np.full(N_ROWS, 4.0)
    saturating[:12] = np.nan
    values[:, 0] = saturating
    df = pd.DataFrame(values, columns=[f"t{i}" for i in range(n_traits)])
    df.loc[N_ROWS - 1, "t0"] = 99.0  # makes t0 globally non-constant …
    for col in df.columns[1:]:
        df.loc[N_ROWS - 1, col] = np.nan  # … on a row that joins no overlap
    _, _, _, _, _, zv_mask, low_mask, residual = _buckets(df)
    print(
        f"\n  one saturating trait in {n_traits} traits -> "
        f"{int(residual.sum())} locally-constant pairs, "
        f"{int((low_mask & ~zv_mask).sum())} low-overlap pairs"
    )


def bench_fuzz(trials: int = 400) -> int:
    rng = np.random.default_rng(7)
    unexplained = double_counted = residual_hits = 0

    for _ in range(trials):
        n_rows, n_cols = 40, 6
        values = rng.normal(size=(n_rows, n_cols))
        values[rng.random(values.shape) < rng.uniform(0.0, 0.8)] = np.nan
        for col in range(n_cols):
            roll = rng.random()
            if roll < 0.15:  # globally constant
                values[:, col] = 3.0
            elif roll < 0.25:  # entirely NaN
                values[:, col] = np.nan
            elif roll < 0.32:  # exactly one non-null
                values[:, col] = np.nan
                values[rng.integers(n_rows), col] = 1.0
            elif roll < 0.40:  # non-finite
                values[rng.integers(n_rows), col] = np.inf
            elif roll < 0.55:  # constant exactly where column 0 is observed
                values[~np.isnan(values[:, 0]), col] = 9.0
        df = pd.DataFrame(values, columns=[f"t{i}" for i in range(n_cols)])

        _, _, _, _, nan_mask, zv_mask, low_mask, residual = _buckets(df)
        claims = (
            (nan_mask & zv_mask).astype(int)
            + (nan_mask & ~zv_mask & low_mask).astype(int)
            + residual.astype(int)
        )
        unexplained += int((nan_mask & (claims == 0)).sum())
        double_counted += int((nan_mask & (claims > 1)).sum())
        residual_hits += int(residual.sum())

    print(f"\n# fuzz — {trials} randomly degenerate frames")
    print(f"  unexplained NaN cells  {unexplained}")
    print(f"  double-counted cells   {double_counted}")
    print(f"  residual-bucket hits   {residual_hits}")
    return 1 if (unexplained or double_counted) else 0


if __name__ == "__main__":
    print(f"pandas {pd.__version__}  numpy {np.__version__}  python {sys.version.split()[0]}")
    flags = set(sys.argv[1:]) or {"--cost", "--payload", "--fuzz"}
    status = 0
    if "--cost" in flags:
        bench_cost()
    if "--payload" in flags:
        bench_payload()
    if "--fuzz" in flags:
        status = bench_fuzz()
    sys.exit(status)
