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
therefore reserved in ``experiment_1``/``experiment_2`` and rejected up front, as is
``experiment_1 == experiment_2`` (self-correlation), any path-unsafe name, and — since a
naive un-sanitized composite is silently truncated by ``AnalysisDir``'s own re-applied
``Path(...).stem`` (found in review) — a filename stem containing a ``.`` or the
``__x__`` separator itself: see :func:`_reject_unsafe_composite_stem` for why rejecting
these outright, rather than sanitizing them away, is the fix that actually closes the
collision risk (a first, sanitizing fix reopened the identical class one level down,
found in a second review pass). Persisted under the reused ``correlation`` tool class
(design.md D9) — reserved since the pre-#438 legacy correlation tools were retired,
exactly as ``descriptive_stats`` reactivated the reserved ``stats`` slot — so
``list_existing_analyses`` requires no changes. Argument order is significant and undoes
no normalization: ``(A, B)`` and ``(B, A)`` persist as two distinct, un-cross-referenced
runs (an open question in design.md, called out in each experiment field's own
description so a caller can discover it from the schema).

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
from bloom_mcp.tools._qc_shared import _validate_experiment_name, _validate_trait_subset

_TOOL_CLASS = "correlation"  # reused reserved slot (design.md D9), not a new one
_CORRELATIONS_NAME = "correlations.csv"
_SIGNIFICANT_NAME = "significant.csv"
_GENOTYPE_MEANS_1_NAME = "genotype_means_1.csv"
_GENOTYPE_MEANS_2_NAME = "genotype_means_2.csv"
_SUMMARY_NAME = "summary.json"

# Reserved for the composite `experiment=`/`based_on_version=` string encoding (D1) —
# centralized here (not repeated as bare literals in the builder below, found in review)
# so the guard and the builder can't drift.
_VERSION_SEPARATOR = "@"  # joins a filename to its resolved cleaned-version label
_PAIR_SEPARATOR = "|"  # joins the two `name@version` halves of based_on_version
_RESERVED_ENCODING_CHARS = (_VERSION_SEPARATOR, _PAIR_SEPARATOR)
_COMPOSITE_SEPARATOR = "__x__"  # joins the two stems of the composite `experiment=` key

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
        "Must not contain '@' or '|' (reserved for this tool's persisted-run encoding), "
        "and its filename stem (the part before the final extension) must not contain "
        "'.' or '__x__' (reserved for this tool's composite storage-key encoding). Must "
        "differ from experiment_2 (self-correlation is rejected). Argument "
        "order is significant: swapping experiment_1/experiment_2 produces a different "
        "persisted run (a distinct storage key) — the two calls are not cross-referenced.",
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
        "a confirmed no-op (talmolab/sleap-roots-analyze#205); this tool does not rely on it. "
        "The ge=1 floor only rejects a non-positive value; it does not by itself protect "
        "against single-replicate noise — a separate, always-enforced floor requires at "
        "least 3 aligned genotypes total regardless of this setting.",
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
    """Summary counts + links to the persisted run — never the full correlation matrix.

    Traceability note (design.md D12): the persisted ``genotype_means_1.csv``/
    ``genotype_means_2.csv`` record each experiment's full per-genotype trait means and
    ``n_samples``, but not the exact per-trait-pair NaN-aligned genotype subset behind a
    specific correlation — upstream discards that identity internally
    (``_prepare_aligned_values``). Cross-reference the two genotype-means tables by hand
    for a specific trait pair if a finer audit trail is needed.
    """

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


def _reject_path_unsafe_names(experiment_1: str, experiment_2: str) -> None:
    """Explicit path-traversal guard for both experiment names (defense-in-depth).

    Neither this tool's own composite-key construction nor
    ``ExperimentReader.load_experiment`` guarantees this today — both happen to key off
    ``Path(name).stem``/``.name`` rather than raising, so relying on that would be
    incidental safety, not an explicit check (flagged in review). ``pca_analysis``/
    ``clustering`` share the same gap; fixing it there too is a follow-up, out of scope
    for this tool.
    """
    _validate_experiment_name(experiment_1, "experiment_1")
    _validate_experiment_name(experiment_2, "experiment_2")


def _reject_reserved_encoding_characters(experiment_1: str, experiment_2: str) -> None:
    """Reject '@'/'|' in either experiment name (design.md D1's composite-string guard)."""
    for label, name in (("experiment_1", experiment_1), ("experiment_2", experiment_2)):
        if any(ch in name for ch in _RESERVED_ENCODING_CHARS):
            raise BloomMCPError(
                code="invalid_input",
                message=(
                    f"{label} ({name!r}) contains one of the reserved characters "
                    f"{list(_RESERVED_ENCODING_CHARS)!r}, used to encode this tool's "
                    "two-experiment persisted run."
                ),
                remedy=(
                    "Rename the experiment file to avoid these characters: "
                    f"{list(_RESERVED_ENCODING_CHARS)!r}."
                ),
            )


def _reject_self_correlation(experiment_1: str, experiment_2: str) -> None:
    """Reject experiment_1 == experiment_2.

    A plausible copy-paste mistake that would otherwise silently compute and persist a
    meaningless self-vs-self correlation matrix under a "foo__x__foo" storage key
    instead of a clear, fixable error (flagged in review).
    """
    if experiment_1 == experiment_2:
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"experiment_1 and experiment_2 are both {experiment_1!r} — "
                "cross_experiment_correlations compares two different experiments."
            ),
            remedy="Pass two distinct experiment filenames.",
        )


def _reject_unsafe_composite_stem(experiment_1: str, experiment_2: str) -> None:
    """Reject a stem this tool's composite storage key can't safely encode (design.md D1).

    ``AnalysisDir`` (the real backend's storage-prefix builder,
    ``bloommcp/src/bloom_mcp/manifest/analysis_dir.py:33``) re-applies ``Path(...).stem``
    to whatever ``experiment=`` string this tool passes it. A composite key built by
    joining two stems with ``_COMPOSITE_SEPARATOR`` is vulnerable to that re-stemming
    whenever either original stem contains a dot: e.g. ``"my.experiment.v2.csv"`` has
    stem ``"my.experiment.v2"``; joined with ``"cylinder.csv"``'s stem, the composite
    ``"my.experiment.v2__x__cylinder"`` gets re-stemmed by ``AnalysisDir`` at the LAST
    dot, silently truncating to ``"my.experiment"`` — losing the second experiment's
    name and the separator entirely, risking a storage-key collision between unrelated
    runs (found in review, reproduced directly against the real code).

    A first fix attempted a lossy sanitization (``_storage_safe_stem``, since removed
    here): replace each dot with an underscore before joining, so the composite string
    itself is dot-free and ``AnalysisDir``'s re-applied ``.stem`` becomes a no-op. That
    reopened the identical collision class one level down — found in a second review
    pass: ``"my.experiment.csv"`` and ``"my_experiment.csv"`` both sanitize to the
    identical stem ``"my_experiment"`` and would silently collide on the same storage
    key. A lossy substitution can narrow a collision class but can't close it. Rejecting
    a dotted stem outright, instead of sanitizing it away, is the only fix that doesn't
    trade one collision class for a narrower one.

    A stem containing ``_COMPOSITE_SEPARATOR`` itself is rejected for the same reason:
    it would let two distinct ``(experiment_1, experiment_2)`` pairs join to the
    identical composite string — e.g. ``experiment_1="control__x__treatment.csv",
    experiment_2="foo.csv"`` joins to the same string as ``experiment_1="control.csv",
    experiment_2="treatment__x__foo.csv"``.
    """
    for label, name in (("experiment_1", experiment_1), ("experiment_2", experiment_2)):
        stem = Path(name).stem
        if "." in stem:
            raise BloomMCPError(
                code="invalid_input",
                message=(
                    f"{label} ({name!r})'s filename stem ({stem!r}) contains a '.', "
                    "which this tool's composite storage-key encoding cannot safely "
                    "represent."
                ),
                remedy=(
                    "Rename the experiment file so its stem (the part before the final "
                    "extension) contains no '.' characters."
                ),
            )
        if _COMPOSITE_SEPARATOR in stem:
            raise BloomMCPError(
                code="invalid_input",
                message=(
                    f"{label} ({name!r})'s filename stem ({stem!r}) contains "
                    f"{_COMPOSITE_SEPARATOR!r}, reserved as this tool's composite "
                    "storage-key separator."
                ),
                remedy=(
                    f"Rename the experiment file so its stem does not contain "
                    f"{_COMPOSITE_SEPARATOR!r}."
                ),
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
    """Normalize identify_significant_correlations' columnless empty return (design.md D6).

    A 0-column DataFrame is always ``.empty`` too, so checking column count alone is
    sufficient (a prior ``sig_df.empty and`` clause here was redundant).
    """
    if len(sig_df.columns) == 0:
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
    _reject_path_unsafe_names(params.experiment_1, params.experiment_2)
    _reject_reserved_encoding_characters(params.experiment_1, params.experiment_2)
    _reject_self_correlation(params.experiment_1, params.experiment_2)
    _reject_unsafe_composite_stem(params.experiment_1, params.experiment_2)

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

    # Safe un-sanitized: _reject_unsafe_composite_stem already ruled out a dotted or
    # separator-containing stem on either side, so AnalysisDir's own re-applied
    # Path(...).stem is a no-op on this composite (design.md D1).
    composite_experiment = (
        f"{Path(params.experiment_1).stem}{_COMPOSITE_SEPARATOR}"
        f"{Path(params.experiment_2).stem}"
    )
    based_on_version = (
        f"{params.experiment_1}{_VERSION_SEPARATOR}{frame1.source}"
        f"{_PAIR_SEPARATOR}"
        f"{params.experiment_2}{_VERSION_SEPARATOR}{frame2.source}"
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
