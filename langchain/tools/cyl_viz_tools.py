"""Figure builders for cylinder phenotyping charts.

Each function here takes per-accession (or per-wave) data and returns a
matplotlib Figure. Callers save the figure via
`helpers.plot_renderer.render_and_save(fig, prefix, namespace="cyl_supabase")`.

Auto-pick of chart layout (boxplot vs ranked profile) lives in the tool
that calls these — not here. Keeping the figure-builders pure makes them
easy to unit-test without env-var setup.
"""
from __future__ import annotations

import statistics

import matplotlib

matplotlib.use("Agg")
from matplotlib.figure import Figure  # noqa: E402


def _accession_boxplot(
    rankings: list[dict],
    values_by_accession: dict[str, list[float]],
    trait_name: str,
) -> Figure:
    """One box per accession, ordered left-to-right by median descending.

    Use when N (number of accessions) is small enough that all boxes and
    labels remain legible — typically N <= 10.
    """
    ordered_names = [r["accession_name"] for r in rankings]
    data = [values_by_accession[name] for name in ordered_names]

    fig = Figure(figsize=(max(8.0, len(ordered_names) * 0.9), 5.0))
    ax = fig.add_subplot(111)

    ax.boxplot(data, tick_labels=ordered_names, showfliers=True)
    ax.set_xlabel("Accession (ranked by median)")
    ax.set_ylabel(trait_name)
    ax.set_title(f"{trait_name} by accession")
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    return fig


def _accession_ranked_profile(
    rankings: list[dict],
    values_by_accession: dict[str, list[float]],
    trait_name: str,
) -> Figure:
    """Dot + Q1-to-Q3 error bar per accession, ordered by median descending.

    Use when N > 10 — boxplots become illegible at scale. Top-3 and
    bottom-3 accessions get inline text labels; the rest of the curve is
    unlabeled but visually scannable.
    """
    n = len(rankings)
    xs = list(range(1, n + 1))
    medians = [r["median"] for r in rankings]

    # Q1-to-Q3 error bars
    yerr_lower: list[float] = []
    yerr_upper: list[float] = []
    for r in rankings:
        vals = sorted(values_by_accession[r["accession_name"]])
        if len(vals) < 2:
            yerr_lower.append(0.0)
            yerr_upper.append(0.0)
            continue
        quartiles = statistics.quantiles(vals, n=4)
        q1, q3 = quartiles[0], quartiles[2]
        yerr_lower.append(max(0.0, r["median"] - q1))
        yerr_upper.append(max(0.0, q3 - r["median"]))

    fig = Figure(figsize=(12.0, 5.0))
    ax = fig.add_subplot(111)

    ax.errorbar(
        xs, medians,
        yerr=[yerr_lower, yerr_upper],
        fmt="o", markersize=4, capsize=2, color="#0ea5a4", ecolor="#94a3b8",
    )
    ax.set_xlabel(f"Accession rank (by median, n={n})")
    ax.set_ylabel(trait_name)
    ax.set_title(f"{trait_name} across {n} accessions")

    # Label top-3 and bottom-3 inline
    label_indices = sorted(set([0, 1, 2, n - 3, n - 2, n - 1]) & set(range(n)))
    for i in label_indices:
        offset_y = 8 if i < 3 else -14
        ax.annotate(
            rankings[i]["accession_name"],
            xy=(xs[i], medians[i]),
            xytext=(4, offset_y),
            textcoords="offset points",
            fontsize=8,
            color="#1f2937",
        )

    # Sparse x-ticks — don't label every rank
    tick_positions = sorted(set([1, n // 4 or 1, n // 2 or 1, 3 * n // 4 or 1, n]))
    ax.set_xticks(tick_positions)
    fig.tight_layout()
    return fig
