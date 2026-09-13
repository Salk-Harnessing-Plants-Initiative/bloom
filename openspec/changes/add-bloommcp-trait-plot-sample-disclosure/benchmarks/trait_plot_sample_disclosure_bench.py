"""Reproduce every measured number in this change's ``design.md`` (#748).

Run:  cd bloommcp && uv run --extra test python \\
          ../openspec/changes/add-bloommcp-trait-plot-sample-disclosure/benchmarks/\\
trait_plot_sample_disclosure_bench.py

Exits non-zero if a *claim* fails (the quartile property, the flier-rate ordering, the floor
comparison) rather than only printing timings — a design doc whose arguments are re-checkable is
the point, not a table of wall clocks nobody re-runs.

Timings vary by machine; the design doc quotes what this printed on the authoring machine
(macOS, matplotlib 3.10.8, pandas 3.0.2, sleap-roots-analyze 0.1.0a5). The *ratios* are what the
decisions rest on, not the absolute seconds.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sleap_roots_analyze.visualization import create_trait_boxplots_by_genotype

from bloom_mcp import experiment_utils as eu
from bloom_mcp.sections.sleap_roots.analysis._viz_shared import (
    MIN_PLOTTED_SAMPLES,
    group_sample_size_table,
)

_FIXTURES = Path(__file__).resolve().parents[4] / "bloommcp" / "tests" / "fixtures"
_failures: list[str] = []


def _check(claim: str, ok: bool) -> None:
    print(f"  [{'ok' if ok else 'FAIL'}] {claim}")
    if not ok:
        _failures.append(claim)


def quartile_order_statistics() -> None:
    """Decision 3: n=5 is the smallest n>1 whose Q1/median/Q3 are all order statistics."""
    print("\n== Quartiles as order statistics (Decision 3) ==")
    print("  n   q1     med    q3     all on order statistics?")
    on = {}
    for n in range(2, 14):
        x = np.arange(1.0, n + 1)
        q1, med, q3 = np.percentile(x, [25, 50, 75])
        on[n] = all(any(abs(q - v) < 1e-12 for v in x) for q in (q1, med, q3))
        print(f"  {n:<3} {q1:<6.2f} {med:<6.2f} {q3:<6.2f} {on[n]}")
    _check(
        f"MIN_PLOTTED_SAMPLES ({MIN_PLOTTED_SAMPLES}) is the smallest n>1 on the list",
        on[MIN_PLOTTED_SAMPLES]
        and not any(on[n] for n in range(2, MIN_PLOTTED_SAMPLES)),
    )


def spurious_flier_rates(reps: int = 60_000) -> None:
    """Decision 3: clearing the floor does NOT make the whiskers/fliers trustworthy."""
    print("\n== Spurious fliers on clean normal data (Decision 3) ==")
    print("  n    P(>=1 flier)  mean fraction flagged")
    rng = np.random.default_rng(7)
    any_rate, frac = {}, {}
    for n in (3, 4, 5, 6, 8, 10, 15, 30):
        sample = rng.standard_normal((reps, n))
        q1 = np.percentile(sample, 25, axis=1)
        q3 = np.percentile(sample, 75, axis=1)
        iqr = q3 - q1
        out = (sample < (q1 - 1.5 * iqr)[:, None]) | (
            sample > (q3 + 1.5 * iqr)[:, None]
        )
        any_rate[n], frac[n] = out.any(axis=1).mean(), out.mean()
        print(f"  {n:<4} {any_rate[n]:<13.3f} {frac[n]:.4f}")
    _check("no flier is drawable below n=4", any_rate[3] == 0.0)
    _check(
        "at n=5 -- just above the floor -- a spurious flier appears >30% of the time",
        any_rate[5] > 0.30,
    )
    _check(
        "the flagged FRACTION falls with n while P(>=1) does not",
        frac[30] < frac[5] and any_rate[30] > 0.25,
    )


def floor_comparison() -> None:
    """Decision 3: why not 10 -- a floor of 10 flags every cell of a healthy experiment."""
    print("\n== Floor comparison on turface_19_final_data.csv (Decision 3) ==")
    df = pd.read_csv(_FIXTURES / "turface_19_final_data.csv")
    detected = eu.detect_columns(df)
    table = group_sample_size_table(
        df, detected["trait_cols"], detected["genotype_col"]
    )
    cells = len(table)
    below_10 = int((table["n_finite"] < 10).sum())
    below_5 = int((table["n_finite"] < 5).sum())
    print(
        f"  {len(detected['trait_cols'])} traits x {table['genotype'].nunique()} "
        f"genotypes = {cells} cells"
    )
    print(f"  below 10: {below_10}/{cells}   below 5: {below_5}/{cells}")
    _check(
        "a floor of 10 flags every cell of a healthy, complete experiment",
        below_10 == cells,
    )
    _check("a floor of 5 flags none of them", below_5 == 0)


def counting_cost_at_cylinder_scale() -> None:
    """Decision 1: the disclosure's own cost is negligible next to the rendering."""
    print("\n== Counting cost at cylinder width (Decision 1) ==")
    rng = np.random.default_rng(0)
    n_rows, n_traits, n_geno = 3000, 846, 60
    df = pd.DataFrame(
        rng.normal(size=(n_rows, n_traits)),
        columns=[f"t{i}" for i in range(n_traits)],
    )
    df = df.mask(pd.DataFrame(rng.random((n_rows, n_traits)) < 0.1, columns=df.columns))
    df["geno"] = rng.integers(0, n_geno, n_rows)
    traits = [c for c in df.columns if c != "geno"]

    start = time.perf_counter()
    table = group_sample_size_table(df, traits, "geno")
    elapsed = time.perf_counter() - start
    csv_bytes = len(table.to_csv(index=False).encode("utf-8"))
    print(
        f"  group_sample_size_table: {elapsed * 1000:.0f} ms for {len(table):,} cells"
    )
    print(f"  group_sample_sizes.csv:  {csv_bytes / 1e6:.2f} MB")
    _check("the whole table is built in well under a second", elapsed < 1.0)


def render_cost_per_page() -> None:
    """Decision 4: why tight_layout is called on the unbatched path only."""
    print("\n== Render cost for one cylinder-shaped page (Decision 4) ==")
    rng = np.random.default_rng(0)
    n_rows, n_geno = 1200, 40
    df = pd.DataFrame(
        rng.normal(size=(n_rows, 16)), columns=[f"trait_{i}" for i in range(16)]
    )
    df["geno"] = [f"G{i % n_geno:02d}" for i in range(n_rows)]
    traits = [c for c in df.columns if c != "geno"]
    counts = df.groupby("geno")[traits].count()
    known = set(counts.index)
    timings = {}

    for label, relabel, tight in (
        ("baseline", False, False),
        ("relabel", True, False),
        ("relabel+tight_layout", True, True),
    ):
        start = time.perf_counter()
        fig = create_trait_boxplots_by_genotype(df, traits, genotype_col="geno")
        if relabel:
            for ax in fig.axes:
                if not ax.get_visible():
                    continue
                trait = ax.get_title().split("\n")[0]
                if trait not in counts.columns:
                    continue
                for axis in (ax.xaxis, ax.yaxis):
                    labels = [t.get_text() for t in axis.get_ticklabels()]
                    if labels and set(labels) <= known:
                        axis.set_ticklabels(
                            [f"{g} (n={int(counts.loc[g, trait])})" for g in labels]
                        )
        if tight:
            fig.tight_layout(rect=[0, 0.03, 1, 1])
        fig.savefig("/dev/null", format="png", dpi=150, bbox_inches="tight")
        timings[label] = time.perf_counter() - start
        plt.close(fig)
        print(f"  {label:22s} {timings[label]:.2f}s")

    overhead = timings["relabel"] / timings["baseline"] - 1
    tight_overhead = timings["relabel+tight_layout"] / timings["baseline"] - 1
    print(
        f"  relabel overhead: {overhead:+.0%}   with tight_layout: {tight_overhead:+.0%}"
    )
    _check(
        "tight_layout costs materially more than relabelling alone, which is why the "
        "batched path (53 such pages) skips it",
        tight_overhead > overhead * 2,
    )


def main() -> int:
    quartile_order_statistics()
    spurious_flier_rates()
    floor_comparison()
    counting_cost_at_cylinder_scale()
    render_cost_per_page()
    if _failures:
        print(f"\n{len(_failures)} claim(s) FAILED:")
        for claim in _failures:
            print(f"  - {claim}")
        return 1
    print("\nAll design.md claims reproduced.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
