"""descriptive_stats — per-trait descriptive statistics on a cleaned experiment.

The simplest granular **consumer** (#488): it reads a *cleaned* experiment through the
:class:`ExperimentReader` port with ``require_clean=True``, restricts the analysis to the
certified-clean trait set (``frame.trait_cols``), and delegates **all** computation to
``sleap_roots_analyze.calculate_trait_statistics`` in one call. The MCP contains no
statistics math of its own — no mean/std/quantile/skewness/kurtosis computation, and no
ANOVA, heritability, or variance-decomposition logic (each out of this tool's scope; see
the openspec proposal ``add-bloommcp-descriptive-stats-tool``).

**Deterministic — no seed.** ``calculate_trait_statistics`` has no RNG, so this tool
declares no ``random_state`` and provenance records ``seed = None`` (matching `qc_clean` /
`pca_analysis`).

**Re-verifies finiteness before delegating, per trait — deliberately NOT `pca_analysis`'s
all-or-nothing guard.** Nothing but ``qc_clean``'s own pre-commit guard normally keeps a
certified trait NaN-free, so a residual non-finite value is still rejected rather than
silently under-counting ``n`` via the delegate's own per-trait ``dropna()``. But unlike
``pca_analysis``/``clustering`` (whose cross-trait fit genuinely needs every selected column
finite at once, so one bad trait must block the whole call), each trait's descriptive
statistics are independent — a residual NaN in one of up to 880 cylinder traits has no
mathematical reason to block the other healthy traits. The offending trait(s) are routed to
``failed_traits``/``n_failed`` (the same channel a delegate-reported failure uses) instead of
raising and aborting the entire request.

**A missing or malformed delegate entry is also routed to `failed_traits`, never emitted as a
partially-``None`` row.** If the delegate omits a trait from its result dict, returns an
explicit ``{"error": ...}``, or (defense-in-depth) is missing any expected stat key, that
trait fails rather than producing a row indistinguishable from a genuine non-finite
coercion — ``r.get(k)`` for an absent key would otherwise return ``None``, identical to a
coerced ``inf``/``nan``, with no signal either happened.

**Non-finite statistics are coerced, not leaked.** ``cv`` is ``inf`` whenever a certified
trait's mean is exactly 0 — genuinely reachable (no cleanup step excludes a zero-mean
trait). ``skewness``/``kurtosis`` are ``nan`` for a zero-variance trait — reachable only via
a hand-crafted cleaned frame that bypasses ``qc_clean``'s own zero-variance filter, never
through real ``qc_clean`` output. Either way, non-finite values are coerced to ``None``
before the output model and the persisted CSV, and the affected trait names are collected
in ``nonfinite_stat_traits`` rather than left as an unexplained blank cell.

**A near-zero (but not exactly zero) mean is NOT coerced.** Coercion only fires on a
non-finite (``inf``/``nan``) result — ``cv = std / mean`` for a mean of e.g. ``1e-9`` is a
large but perfectly finite float, so it passes through as an ordinary (if visually extreme)
``cv`` value, uncaveated beyond its sheer magnitude. This is a known, expected shape for
zero-inflated cylinder traits, not a bug; a caller reading a huge ``cv`` should treat it as
a near-zero-mean signal rather than a coercion the tool silently missed.

**Bounded inline summary.** ``stats_per_trait`` is capped to the first 50 traits
(``_SUMMARY_TRAIT_CAP``, carried over from the retired legacy workflow), with
``truncated_in_summary`` + ``omitted_traits`` naming exactly what was cut — necessary given
the cylinder fixture's ~649-880 traits. The persisted ``stats.csv`` always contains every
computed (non-failed) trait, uncapped.

Persists a versioned run via the :class:`ResultStore` port under tool class ``stats`` —
**reused, not new**: ``"stats"`` has been a reserved entry in
``manifest.CANONICAL_TOOL_CLASSES`` and ``list_existing_analyses.TOOL_CLASSES`` since the
pre-#438 legacy ``run_descriptive_stats_workflow`` was retired, kept intact there
specifically so historical runs could still read back (see those modules' own "do NOT
prune retired classes" comments). This tool reactivates it as a live write target rather
than claiming a genuinely new slot. Practically: this tool's output doesn't compose as
another tool's input the way a cleaned CSV or a PCA result does, so there's still no
reason to share a class or a ``_resolve_versioned_cleaned``-style resolution rule with
``qc``/``pca``/``clustering`` — but if any experiment already has legacy ``stats``-class
runs sitting in storage from the retired workflow, this tool's runs land in the *same*
version lineage, distinguishable only by ``VersionEntry.tool``. Verify no such legacy runs
exist for experiments this tool will touch before relying on version numbers/``"latest"``
resolution meaning what a fresh lineage would imply.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from sleap_roots_analyze import calculate_trait_statistics

from bloom_mcp.contract import BloomMCPError, Provenance, RunLinks, as_mcp_tool
from bloom_mcp.data_access import CleanedVersionRequiredError, ExperimentReadError
from bloom_mcp.tools import _ports
from bloom_mcp.tools._consumer_utils import snapshot_frame
from bloom_mcp.tools._qc_shared import _finite_or_none, _validate_trait_subset

_TOOL_CLASS = "stats"
_STATS_CSV_NAME = "stats.csv"
_SUMMARY_TRAIT_CAP = 50

# The delegate's per-trait statistic keys, in the legacy stats.csv column order (minus
# "trait"/"n", which are handled separately — "n" is the delegate's "count", renamed).
_STAT_FIELDS = (
    "mean",
    "std",
    "median",
    "q25",
    "q75",
    "min",
    "max",
    "cv",
    "skewness",
    "kurtosis",
)


class DescriptiveStatsParams(BaseModel):
    """Inputs for ``descriptive_stats``. No ``seed`` — the delegate is deterministic."""

    experiment: str = Field(
        ...,
        description="Experiment identifier to analyze. Must have a cleaned version "
        "produced by qc_clean; descriptive_stats consumes it (require_clean).",
    )
    trait_columns: Optional[list[str]] = Field(
        default=None,
        description="Subset of certified-clean trait columns to summarize; omit to use "
        "all certified-clean traits. Each must be a cleaned trait column of the "
        "experiment. Pass at least one column with no duplicates (an empty list is "
        "rejected).",
    )
    user_label: Optional[str] = Field(
        default=None,
        description="Optional slug appended to the version directory name.",
    )


class TraitStatistics(BaseModel):
    """One trait's statistics, taken directly from ``calculate_trait_statistics``.

    ``n`` is the delegate's ``count`` field (the one deliberate rename, kept for
    continuity with the retired legacy workflow's ``stats.csv`` column). Every other
    field is ``Optional`` because a non-finite delegate value (``inf``/``nan``) is
    coerced to ``None`` here rather than leaked as a raw JSON token.
    """

    trait: str
    n: int
    mean: Optional[float] = None
    std: Optional[float] = None
    median: Optional[float] = None
    q25: Optional[float] = None
    q75: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    cv: Optional[float] = None
    skewness: Optional[float] = Field(
        default=None,
        description="SciPy's biased/population estimator (scipy.stats.skew's "
        "default), NOT the sample-corrected version pandas/Excel default to -- the "
        "two differ for the same data, so a value hand-verified against "
        "df[trait].skew() won't match bit-for-bit.",
    )
    kurtosis: Optional[float] = Field(
        default=None,
        description="SciPy's biased/population estimator (scipy.stats.kurtosis's "
        "default, excess kurtosis), NOT pandas/Excel's sample-corrected version -- "
        "same caveat as skewness above.",
    )


class DescriptiveStatsResult(RunLinks):
    """A bounded per-trait summary + links to the persisted full stats table."""

    experiment: str
    source: str
    n_samples: int
    n_traits_requested: int
    n_traits_reported: int
    n_failed: int
    failed_traits: list[str] = Field(default_factory=list)
    stats_per_trait: list[TraitStatistics]
    truncated_in_summary: bool
    omitted_traits: list[str] = Field(
        default_factory=list,
        description="Trait names cut from stats_per_trait by the 50-trait cap, in the "
        "same resolved order. Empty when nothing was cut. Every omitted trait is still "
        "in the persisted stats.csv.",
    )
    nonfinite_stat_traits: list[str] = Field(
        default_factory=list,
        description="Traits whose stats_per_trait entry has at least one field coerced "
        "from a non-finite delegate value (inf/-inf/nan) to None — e.g. cv for a "
        "zero-mean trait. Empty when every reported statistic was finite.",
    )


@as_mcp_tool(
    input_model=DescriptiveStatsParams,
    output_model=DescriptiveStatsResult,
    errors=(ExperimentReadError,),
)
def descriptive_stats(
    params: DescriptiveStatsParams, *, provenance: Provenance
) -> DescriptiveStatsResult:
    """Summarize ``experiment`` via ``calculate_trait_statistics`` and persist it."""
    reader = _ports.reader()
    store = _ports.store()

    # Consumer: require a cleaned version. Genuinely mirrors pca_analysis/clustering's
    # own handling of this exact guard (both use code="tool_error") — not
    # remove_outliers's assumption_violated.
    try:
        frame = reader.load_experiment(params.experiment, require_clean=True)
    except CleanedVersionRequiredError:
        raise BloomMCPError(
            code="tool_error",
            message=(
                f"No cleaned version of {params.experiment!r} exists; "
                f"descriptive_stats requires a cleaned input."
            ),
            remedy=(
                f"Run qc_clean on {params.experiment!r} first, then retry "
                f"descriptive_stats."
            ),
        ) from None

    if params.trait_columns is None:
        trait_cols = list(frame.trait_cols)
    else:
        _validate_trait_subset(
            frame, params.trait_columns, params.experiment, require_certified=True
        )
        trait_cols = list(params.trait_columns)
    selected = frame.df[trait_cols]

    # Defense-in-depth, per trait — NOT pca_analysis/clustering's all-or-nothing guard.
    # Nothing but qc_clean's own write-time guard normally enforces finiteness; without
    # this check, a residual NaN would make the delegate's own per-trait dropna()
    # silently under-count n with no signal. But each trait's descriptive stats are
    # independent (no cross-trait fit the way PCA/clustering have), so a non-finite
    # value in one trait must not block every other healthy trait in a selection of up
    # to 880 (cylinder) — it is routed to failed_traits instead of aborting the request.
    finite_by_col = np.isfinite(selected.to_numpy(dtype=float)).all(axis=0)
    nonfinite_input_traits = {
        trait for trait, finite in zip(trait_cols, finite_by_col) if not finite
    }
    delegate_trait_cols = [t for t in trait_cols if t not in nonfinite_input_traits]

    # Delegate ALL statistics. No math of its own here.
    results = (
        calculate_trait_statistics(frame.df, delegate_trait_cols)
        if delegate_trait_cols
        else {}
    )

    rows: list[TraitStatistics] = []
    failed: list[str] = []
    nonfinite_traits: list[str] = []
    for trait in trait_cols:
        if trait in nonfinite_input_traits:
            failed.append(trait)
            continue
        r = results.get(trait)
        # r is None when the delegate omits a requested trait from its dict entirely
        # (rather than an explicit "error" key); "error" in r is the delegate's own
        # all-NaN branch. A missing expected stat key is a malformed delegate entry —
        # routed the same way rather than emitting a partially-None row indistinguishable
        # from a genuine non-finite coercion. All three are unreachable through a
        # genuinely certified-clean selection (qc_clean guarantees no NaN cells in kept
        # trait columns), but handled anyway as defense-in-depth rather than assumed
        # impossible.
        if (
            r is None
            or "error" in r
            or "count" not in r
            or any(k not in r for k in _STAT_FIELDS)
        ):
            failed.append(trait)
            continue
        fields = {k: _finite_or_none(r.get(k)) for k in _STAT_FIELDS}
        if any(value is None for value in fields.values()):
            nonfinite_traits.append(trait)
        rows.append(TraitStatistics(trait=trait, n=int(r["count"]), **fields))

    n_traits_reported = len(rows)
    truncated_in_summary = n_traits_reported > _SUMMARY_TRAIT_CAP
    stats_per_trait = rows[:_SUMMARY_TRAIT_CAP]
    omitted_traits = [row.trait for row in rows[_SUMMARY_TRAIT_CAP:]]

    prov = provenance.model_copy(update={"based_on_version": frame.source})
    stats_df = pd.DataFrame(
        [
            {
                "trait": row.trait,
                "n": row.n,
                "mean": row.mean,
                "std": row.std,
                "median": row.median,
                "q25": row.q25,
                "q75": row.q75,
                "min": row.min,
                "max": row.max,
                "cv": row.cv,
                "skewness": row.skewness,
                "kurtosis": row.kurtosis,
            }
            for row in rows
        ]
    )

    with snapshot_frame(frame.df) as source_snapshot:
        run = store.create_run(
            experiment=params.experiment,
            tool_class=_TOOL_CLASS,
            provenance=prov,
            user_label=params.user_label,
            source_csv=source_snapshot,
            source=frame.resolved_source,
        )
        stats_df.to_csv(run.staging_dir / _STATS_CSV_NAME, index=False)
        stored = store.commit(run, {_STATS_CSV_NAME: _STATS_CSV_NAME})

    return DescriptiveStatsResult(
        experiment=params.experiment,
        source=frame.source,
        n_samples=len(frame.df),
        n_traits_requested=len(trait_cols),
        n_traits_reported=n_traits_reported,
        n_failed=len(failed),
        failed_traits=failed,
        stats_per_trait=stats_per_trait,
        truncated_in_summary=truncated_in_summary,
        omitted_traits=omitted_traits,
        nonfinite_stat_traits=nonfinite_traits,
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
    )
