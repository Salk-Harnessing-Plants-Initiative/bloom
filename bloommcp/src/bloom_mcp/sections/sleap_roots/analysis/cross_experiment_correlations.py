"""cross_experiment_correlations — genotype-mean trait correlation across two experiments,
delegating to sleap-roots-analyze.

The first granular consumer with a **two-experiment input** (#489): every prior consumer
(``qc_clean``, ``pca_analysis``, ``remove_outliers``, ``clustering``, ``umap_analysis``,
``descriptive_stats``) takes one ``experiment`` filename. This tool reads both
``experiment_1`` and ``experiment_2`` through the :class:`ExperimentReader` port with
``require_clean=True`` — "consume, don't re-clean" — and delegates **all** correlation math
to ``sleap_roots_analyze.cross_experiment_analysis``: ``calculate_genotype_means``,
``calculate_cross_experiment_correlations``, ``identify_significant_correlations``, and
``summarize_correlation_results``. It never calls ``load_and_align_experiments`` (which
reads CSVs directly off paths, bypassing the read port) and never re-implements genotype
aggregation via a bespoke ``groupby``/``mean``.

**Re-adds a capability ``devendor-bloommcp-analysis`` (PR #438) deleted rather than
rewired**, because upstream's ``cross_experiment_analysis`` module shares function names
with the vendored one it replaced but has a genuinely different contract. This is the
fresh design against that actual (richer) upstream contract.

**A confirmed upstream no-op, worked around (design.md D8, talmolab/sleap-roots-analyze#205).**
``calculate_cross_experiment_correlations``'s own ``min_samples`` filtering is dead code —
it computes a correctly-filtered genotype list, prints it, then never references it again;
the per-trait-pair loop hardcodes ``min_samples=0`` in its internal alignment call. So this
tool enforces ``min_samples`` itself: both genotype-means tables are filtered to
``n_samples >= min_samples`` *before* being handed to the delegate. ``min_samples`` is still
forwarded to the delegate call unchanged (harmless — it stays inert there today), so the
call remains correct and idempotent if upstream fixes the internal filter later.

**Required genotype role on both sides.** Correlation here is computed at the
genotype-mean level, so — unlike ``pca_analysis``/``clustering`` (which treat
``frame.genotype_col`` as optional, used only for plot coloring) — a missing genotype
column on either experiment means no meaningful result can be produced at all; both are
checked non-``None`` before any computation.

**Two-experiment persistence without touching shared ports (design.md D1).**
``ResultStore``/``Provenance`` are single-experiment-shaped (`experiment: str`,
``based_on_version: str``, one ``source_csv``). Rather than extending those shared types
for what is so far bloommcp's only dual-input consumer, both experiments are encoded into
the existing fields: a composite ``experiment`` key (both filenames' stems joined by
``__x__``), a composite ``based_on_version`` (``exp@version|exp@version``), and a single
combined ``source_csv`` snapshot (both selections concatenated, labeled by a leading
``_experiment`` column) so ``input_sha256`` content-addresses both inputs. ``@``/``|`` are
therefore reserved in ``experiment_1``/``experiment_2`` and rejected up front. Persisted
under the reused ``correlation`` tool class (design.md D9) — reserved since the pre-#438
legacy correlation tools were retired, exactly as ``descriptive_stats`` reactivated the
reserved ``stats`` slot — so ``list_existing_analyses`` requires no changes.

**Traceability (design.md D12).** Upstream itself discards which specific genotypes fed a
given trait pair's correlation (an internal alignment result assigned to ``_``), so this
tool persists both experiments' full genotype-means tables (with per-genotype
``n_samples``, post ``min_samples`` filter) alongside the correlation matrix, so a
scientist auditing a surprising correlation has enough to manually cross-reference.

**Degenerate vs. empty-but-valid (design.md D6).** Zero rows out of
``calculate_cross_experiment_correlations`` (no trait pair reached ``min_samples`` aligned
genotypes) is degenerate input — rejected, nothing persisted. Zero rows surviving
``identify_significant_correlations`` (correlations exist, none clear
``r_threshold``/``p_threshold``) is a normal outcome — persisted with a fixed-header empty
``significant.csv``, not upstream's columnless empty frame.

Deterministic — no ``random_state`` anywhere in the delegation chain, so no ``seed``
parameter is declared and ``Provenance`` records ``seed = None`` (matching
``pca_analysis``'s convention).

Out of scope for this tool (see proposal.md's deferred list): ``calculate_per_trait_correlations``
(a different, sample-level granularity), ``calculate_cross_experiment_correlations_extended``
(multi-statistic combinations), ``calculate_correlation_confidence_intervals`` (deferred —
a second, related upstream inconsistency, also reported in #205), all plotting, power
analysis, and redundant-trait clustering.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from sleap_roots_analyze import (
    calculate_cross_experiment_correlations,
    calculate_genotype_means,
    identify_significant_correlations,
    summarize_correlation_results,
)
from sleap_roots_analyze.data_utils import convert_to_json_serializable

from bloom_mcp.contract import BloomMCPError, Provenance, RunLinks, as_mcp_tool
from bloom_mcp.data_access import (
    CleanedVersionRequiredError,
    ExperimentFrame,
    ExperimentReadError,
)
from bloom_mcp.tools import _ports
from bloom_mcp.tools._consumer_utils import snapshot_frame
from bloom_mcp.tools._qc_shared import _validate_trait_subset

_TOOL_CLASS = "correlation"  # reused reserved slot (design.md D9), not a new one
_CORRELATIONS_NAME = "correlations.csv"
_SIGNIFICANT_NAME = "significant.csv"
_GENOTYPE_MEANS_1_NAME = "genotype_means_1.csv"
_GENOTYPE_MEANS_2_NAME = "genotype_means_2.csv"
_SUMMARY_NAME = "summary.json"

# Columns identify_significant_correlations' result carries beyond calculate_cross_experiment_
# correlations' own columns — used to normalize its columnless empty-DataFrame return (design.md D6).
_SIGNIFICANT_EXTRA_COLS = ("p_value_corrected", "significant_fdr")

_FDR_FAMILY_CAVEAT = (
    "FDR correction (when use_fdr=True) is computed over the full family of trait pairs "
    "in THIS call (trait_columns_1 x trait_columns_2); re-running with a narrower subset "
    "changes the correction family, so p_value_corrected values are not comparable across "
    "differently-scoped calls."
)


class CrossExperimentCorrelationsParams(BaseModel):
    """Inputs for ``cross_experiment_correlations``. No ``seed`` — deterministic."""

    experiment_1: str = Field(
        ...,
        description="First experiment (CSV filename). Must have a cleaned version "
        "produced by qc_clean; cross_experiment_correlations consumes it (require_clean). "
        "Must not contain '@' or '|' (reserved for this tool's persisted-run encoding).",
    )
    experiment_2: str = Field(
        ...,
        description="Second experiment (CSV filename). Same requirements as experiment_1.",
    )
    trait_columns_1: list[str] | None = Field(
        default=None,
        description="Subset of experiment_1's certified-clean trait columns to correlate; "
        "omit to use all of them. Validated independently of trait_columns_2. "
        + _FDR_FAMILY_CAVEAT,
    )
    trait_columns_2: list[str] | None = Field(
        default=None,
        description="Subset of experiment_2's certified-clean trait columns to correlate; "
        "omit to use all of them. Validated independently of trait_columns_1. "
        + _FDR_FAMILY_CAVEAT,
    )
    min_samples: int = Field(
        default=3,
        ge=1,
        description="Minimum replicates a genotype must have in BOTH experiments to "
        "participate. Enforced by a bloommcp-side pre-filter on both genotype-means "
        "tables before delegating — the upstream delegate's own min_samples filtering is "
        "a confirmed no-op (talmolab/sleap-roots-analyze#205); this tool does not rely on it.",
    )
    p_threshold: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="P-value threshold for significance (forwarded to "
        "identify_significant_correlations).",
    )
    r_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum absolute correlation threshold for significance (forwarded "
        "to identify_significant_correlations).",
    )
    use_fdr: bool = Field(
        default=True,
        description="Apply Benjamini-Hochberg FDR correction for multiple testing "
        "(forwarded to identify_significant_correlations).",
    )
    user_label: str | None = Field(
        default=None,
        description="Optional slug appended to the version directory name.",
    )


class CrossExperimentCorrelationsResult(RunLinks):
    """Summary counts + links to the persisted run — never the full correlation matrix."""

    experiment_1: str
    experiment_2: str
    source_1: str
    source_2: str
    n_traits_1: int
    n_traits_2: int
    n_correlations: int
    n_significant: int
    n_highly_significant: int = Field(
        description="Count of trait pairs with raw p_value < 0.01 over the full "
        "correlation set (calculate_cross_experiment_correlations' own 'highly_significant' "
        "flag) — independent of r_threshold/FDR filtering, unlike n_significant."
    )


def _reject_reserved_encoding_characters(experiment_1: str, experiment_2: str) -> None:
    """Reject '@'/'|' in either experiment name (design.md D1's composite-string guard)."""
    for label, name in (("experiment_1", experiment_1), ("experiment_2", experiment_2)):
        if "@" in name or "|" in name:
            raise BloomMCPError(
                code="invalid_input",
                message=(
                    f"{label} ({name!r}) contains '@' or '|', reserved characters used to "
                    "encode this tool's two-experiment persisted run."
                ),
                remedy="Rename the experiment file to avoid '@' and '|' characters.",
            )


def _load_cleaned(reader, name: str, label: str) -> ExperimentFrame:
    """Load ``name`` with require_clean=True, naming ``label`` in a tool_error remedy."""
    try:
        return reader.load_experiment(name, require_clean=True)
    except CleanedVersionRequiredError:
        raise BloomMCPError(
            code="tool_error",
            message=(
                f"No cleaned version of {name!r} ({label}) exists; "
                f"cross_experiment_correlations requires a cleaned input for both experiments."
            ),
            remedy=f"Run qc_clean on {name!r} first, then retry cross_experiment_correlations.",
        ) from None


def _require_genotype_col(frame: ExperimentFrame, name: str, label: str) -> str:
    """Return frame.genotype_col, or raise naming ``label`` if it is None (design.md D5)."""
    if frame.genotype_col is None:
        raise BloomMCPError(
            code="assumption_violated",
            message=f"{label} ({name!r}) has no resolvable genotype column.",
            remedy=(
                f"Re-run qc_clean on {name!r} with an explicit genotype_column override, "
                "then retry cross_experiment_correlations."
            ),
        )
    return frame.genotype_col


def _resolve_trait_cols(
    frame: ExperimentFrame, requested: list[str] | None, name: str
) -> list[str]:
    """Default to the full certified set, or validate a caller-supplied subset."""
    if requested is None:
        return list(frame.trait_cols)
    _validate_trait_subset(frame, requested, name, require_certified=True)
    return list(requested)


def _require_finite(
    frame: ExperimentFrame, trait_cols: list[str], name: str, label: str
):
    """Raise assumption_violated naming ``label`` if any selected trait is non-finite."""
    selected = frame.df[trait_cols]
    if not np.isfinite(selected.to_numpy(dtype=float)).all():
        raise BloomMCPError(
            code="assumption_violated",
            message=(
                f"{label} ({name!r}) carries a non-finite value (NaN or +/-inf) in its "
                "certified trait columns."
            ),
            remedy=f"Re-run qc_clean on {name!r} to produce a finite-valued cleaned version, "
            "then retry.",
        )
    return selected


def _genotype_means_prefiltered(
    df: pd.DataFrame, trait_cols: list[str], genotype_col: str, min_samples: int
) -> pd.DataFrame:
    """calculate_genotype_means, then drop genotype rows below min_samples (design.md D8)."""
    means = calculate_genotype_means(df, trait_cols, genotype_col=genotype_col)
    return means[means["n_samples"] >= min_samples]


def _normalized_significant(
    sig_df: pd.DataFrame, corr_df: pd.DataFrame
) -> pd.DataFrame:
    """Normalize identify_significant_correlations' columnless empty return (design.md D6)."""
    if sig_df.empty and len(sig_df.columns) == 0:
        return pd.DataFrame(
            columns=list(corr_df.columns) + list(_SIGNIFICANT_EXTRA_COLS)
        )
    return sig_df


def _combined_snapshot(
    selected_1: pd.DataFrame, selected_2: pd.DataFrame
) -> pd.DataFrame:
    """Both selections concatenated with a leading _experiment label (design.md D1)."""
    combined = pd.concat(
        [selected_1.assign(_experiment=1), selected_2.assign(_experiment=2)],
        ignore_index=True,
    )
    cols = ["_experiment"] + [c for c in combined.columns if c != "_experiment"]
    return combined[cols]


@as_mcp_tool(
    input_model=CrossExperimentCorrelationsParams,
    output_model=CrossExperimentCorrelationsResult,
    errors=(ExperimentReadError,),
)
def cross_experiment_correlations(
    params: CrossExperimentCorrelationsParams, *, provenance: Provenance
) -> CrossExperimentCorrelationsResult:
    """Correlate genotype-mean traits between two cleaned experiments and persist the run."""
    _reject_reserved_encoding_characters(params.experiment_1, params.experiment_2)

    reader = _ports.reader()
    store = _ports.store()

    frame1 = _load_cleaned(reader, params.experiment_1, "experiment_1")
    frame2 = _load_cleaned(reader, params.experiment_2, "experiment_2")

    genotype_col_1 = _require_genotype_col(frame1, params.experiment_1, "experiment_1")
    genotype_col_2 = _require_genotype_col(frame2, params.experiment_2, "experiment_2")

    trait_cols_1 = _resolve_trait_cols(
        frame1, params.trait_columns_1, params.experiment_1
    )
    trait_cols_2 = _resolve_trait_cols(
        frame2, params.trait_columns_2, params.experiment_2
    )

    selected_1 = _require_finite(
        frame1, trait_cols_1, params.experiment_1, "experiment_1"
    )
    selected_2 = _require_finite(
        frame2, trait_cols_2, params.experiment_2, "experiment_2"
    )

    # Delegate genotype-mean aggregation (D2), then enforce min_samples ourselves (D8) —
    # the upstream delegate's own min_samples filtering is a confirmed no-op.
    genotype_means_1 = _genotype_means_prefiltered(
        frame1.df, trait_cols_1, genotype_col_1, params.min_samples
    )
    genotype_means_2 = _genotype_means_prefiltered(
        frame2.df, trait_cols_2, genotype_col_2, params.min_samples
    )

    corr_df = calculate_cross_experiment_correlations(
        genotype_means_1,
        genotype_means_2,
        trait_cols_1,
        trait_cols_2,
        min_samples=params.min_samples,
    )

    if corr_df.empty:
        raise BloomMCPError(
            code="assumption_violated",
            message=(
                f"No trait pair between {params.experiment_1!r} and "
                f"{params.experiment_2!r} reached min_samples={params.min_samples} "
                "aligned genotypes."
            ),
            remedy=(
                "Lower min_samples, or check that the two experiments share enough "
                "genotypes with sufficient replication."
            ),
        )

    sig_df = _normalized_significant(
        identify_significant_correlations(
            corr_df,
            p_threshold=params.p_threshold,
            r_threshold=params.r_threshold,
            use_fdr=params.use_fdr,
        ),
        corr_df,
    )

    summary = convert_to_json_serializable(
        summarize_correlation_results(
            corr_df, exp1_name=params.experiment_1, exp2_name=params.experiment_2
        )
    )

    composite_experiment = (
        f"{Path(params.experiment_1).stem}__x__{Path(params.experiment_2).stem}"
    )
    based_on_version = (
        f"{params.experiment_1}@{frame1.source}|{params.experiment_2}@{frame2.source}"
    )
    prov = provenance.model_copy(update={"based_on_version": based_on_version})

    n_highly_significant = int(corr_df["highly_significant"].sum())

    with snapshot_frame(_combined_snapshot(selected_1, selected_2)) as source_snapshot:
        run = store.create_run(
            experiment=composite_experiment,
            tool_class=_TOOL_CLASS,
            provenance=prov,
            user_label=params.user_label,
            source_csv=source_snapshot,
        )
        corr_df.to_csv(run.staging_dir / _CORRELATIONS_NAME, index=False)
        sig_df.to_csv(run.staging_dir / _SIGNIFICANT_NAME, index=False)
        genotype_means_1.to_csv(run.staging_dir / _GENOTYPE_MEANS_1_NAME, index=True)
        genotype_means_2.to_csv(run.staging_dir / _GENOTYPE_MEANS_2_NAME, index=True)
        (run.staging_dir / _SUMMARY_NAME).write_text(json.dumps(summary))
        stored = store.commit(
            run,
            {
                _CORRELATIONS_NAME: _CORRELATIONS_NAME,
                _SIGNIFICANT_NAME: _SIGNIFICANT_NAME,
                _GENOTYPE_MEANS_1_NAME: _GENOTYPE_MEANS_1_NAME,
                _GENOTYPE_MEANS_2_NAME: _GENOTYPE_MEANS_2_NAME,
                _SUMMARY_NAME: _SUMMARY_NAME,
            },
        )

    return CrossExperimentCorrelationsResult(
        experiment_1=params.experiment_1,
        experiment_2=params.experiment_2,
        source_1=frame1.source,
        source_2=frame2.source,
        n_traits_1=len(trait_cols_1),
        n_traits_2=len(trait_cols_2),
        n_correlations=len(corr_df),
        n_significant=len(sig_df),
        n_highly_significant=n_highly_significant,
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
    )
