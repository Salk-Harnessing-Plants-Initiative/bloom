"""Reproducible evidence for `design.md`'s cost and payload tables.

Run from the bloommcp service so the pinned pandas/numpy are the ones measured::

    cd bloommcp && uv run --extra test python \
        ../openspec/changes/add-bloommcp-corr-pair-disclosure/benchmarks/corr_pair_disclosure_bench.py

ABSOLUTE timings are machine-specific and will not reproduce off the machine that wrote
`design.md` (measured there at roughly half the wall-clock seen elsewhere); the RATIO of added
to existing cost is the figure design.md relies on, and that does reproduce.

Sections (both run by default; pass ``--cost``/``--payload`` to select):

* ``--cost``    — wall-clock of the two existing steps vs the two this change adds, at
                  cylinder width (846 traits). Backs `design.md` Risks.
* ``--payload`` — how many strong pairs realistic trait collinearity produces, and how large
                  the uncapped JSON would be. Backs Decision 2's cap. Also measures the
                  "saturating trait" case that forced `locally_constant_trait_pairs` to be
                  capped too (Risks, "Response size").
This file is evidence for the proposal, not a shipped test.

A ``--fuzz`` section used to live here, claiming "400 randomly degenerate frames, 0
unexplained and 0 double-counted cells" as the guard on the remainder bucket. It was removed
in the #784 review because **it could not fail**: it partitioned the NaN cells as A, not-A and
B, not-A and not-B, which sums to exactly 1 for every cell regardless of what the buckets
actually contained — sabotaging the bucket logic still printed 0/0. It also never called the
tool. The taxonomy property is pinned for CI instead by, in
``bloommcp/tests/tools/test_plot_correlation_matrix_tool.py``:

* ``test_every_nan_cell_has_exactly_one_reason`` and
  ``test_taxonomy_totality_over_randomly_degenerate_frames`` — totality, with the buckets
  taken from the TOOL's response and the NaN cells from an independent pandas call.
* ``test_locally_constant_pairs_are_really_locally_constant`` — the label itself, re-derived
  via ``nunique()`` over each reported pair's shared finite rows. This is the only check that
  can catch mislabelling, which no partition check ever could.
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
    std = df[cols].std(skipna=True)
    zero_variance = [c for c in cols if not (0 < std[c] < np.inf)]
    corr = df[cols].corr(min_periods=min_overlap).to_numpy()
    finite = np.isfinite(df[cols].to_numpy(dtype="float64", na_value=np.nan)).astype(
        np.int64
    )
    overlap = finite.T @ finite

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


def bench_cost(latent: int = 5) -> None:
    """Cost at cylinder width on a frame that actually produces strong pairs.

    An earlier version drew iid normals, which yields ZERO strong pairs — so the new
    sort+cap step was timed doing nothing (#784 review). `latent` builds the frame from a
    handful of shared factors, which is what `bench_payload` argues realistic trait data
    looks like, and puts tens of thousands of pairs past the cutoff.
    """
    rng = np.random.default_rng(0)
    factors = rng.normal(size=(N_ROWS, latent))
    values = factors @ rng.normal(size=(latent, N_TRAITS))
    values += 0.05 * rng.normal(size=values.shape)
    values[rng.random(values.shape) < 0.15] = np.nan
    df = pd.DataFrame(values, columns=[f"t{i}" for i in range(N_TRAITS)])
    cols = list(df.columns)

    t0 = time.perf_counter()
    corr = df[cols].corr(min_periods=MIN_OVERLAP).to_numpy()
    t1 = time.perf_counter()
    finite = np.isfinite(df[cols].to_numpy(dtype="float64", na_value=np.nan)).astype(
        np.int64
    )
    overlap = finite.T @ finite
    t2 = time.perf_counter()

    # Mirrors the shipped derivation, zero-variance mask included — an earlier version of
    # this bench omitted those writes and so under-counted the new step (#784 review).
    std = df[cols].std(skipna=True)
    zv = [i for i, c in enumerate(cols) if not (0 < std[c] < np.inf)]
    upper = np.triu(np.ones(corr.shape, dtype=bool), k=1)
    zv_mask = np.zeros(corr.shape, dtype=bool)
    if zv:
        zv_mask[zv, :] = True
        zv_mask[:, zv] = True
    nan_mask = np.isnan(corr) & upper
    low_mask = (overlap < MIN_OVERLAP) & upper
    _residual = nan_mask & ~zv_mask & ~low_mask
    t3 = time.perf_counter()

    scored = upper & ~zv_mask
    strong = ((corr > 0.7) & scored) | ((corr < -0.7) & scored)
    si, sj = np.where(strong)
    np.lexsort((sj, si, -np.abs(corr[si, sj]), overlap[si, sj]))[:20]
    t4 = time.perf_counter()

    print(f"# cost — {N_TRAITS} traits x {N_ROWS} rows, 15% missing, {latent} latent factors")
    print(f"  (strong pairs found: {int(strong.sum()):,} — the sort below is doing real work)")
    print(f"  .corr(min_periods=…)  existing   {t1 - t0:7.3f}s")
    print(f"  finite.T @ finite     existing   {t2 - t1:7.3f}s")
    print(f"  residual-NaN bucket   NEW        {t3 - t2:7.3f}s")
    print(f"  strong-pair sort+cap  NEW        {t4 - t3:7.3f}s")
    added, base = (t3 - t2) + (t4 - t3), (t1 - t0) + (t2 - t1)
    print(f"  -> added {added:.3f}s on {base:.3f}s = {100 * added / base:.1f}%")
    print(f"  one {N_TRAITS}^2 bool mask = {upper.nbytes / 1e6:.1f} MB")
    print("  NOTE: absolute seconds are machine-specific; the ratio is the reproducible part.")


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


if __name__ == "__main__":
    print(f"pandas {pd.__version__}  numpy {np.__version__}  python {sys.version.split()[0]}")
    flags = set(sys.argv[1:]) or {"--cost", "--payload"}
    if "--cost" in flags:
        bench_cost()
    if "--payload" in flags:
        bench_payload()
