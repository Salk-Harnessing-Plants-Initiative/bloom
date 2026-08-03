"""qc_clean — turn a raw experiment trait table into a clean, analysis-ready one.

The first granular, contract-wrapped tool (Tier 3 / #338) and the QC foundation.
It delegates **all** cleanup-and-validate to
``sleap_roots_analyze.clean_traits_for_analysis`` (the minimal-QC entry point,
analyze#164): the MCP contains **no QC logic** — it does not run the full
``QCPipeline`` and does not re-stitch ``load → cleanup → validate`` (that
orchestration is analyze's, tested upstream), nor does it touch the vendored
``bloom_mcp.data_cleanup``.

**Contract-valid, traceable inputs (#403).** ``qc_clean`` is the sole producer of
the analysis-ready ``_cleaned.csv`` the downstream sleap-roots-analyze tools
consume (``remove_outliers`` #378, ``pca_analysis`` #308). So it is the boundary
that makes that artifact contract-valid + traceable: it resolves columns through
the shared :func:`resolve_columns` (bloommcp role matching + upstream
``get_trait_columns`` trait detection, so numeric metadata like
``Computation.Time.s`` is never analyzed as a trait), **requires** a resolvable
genotype **and** sample identifier (so every cleaned/flagged sample traces back to
a real plant/scan — enforced at the bloommcp level, so it holds even when
``sleap-roots-contracts`` is absent), and runs analyze's input contract in ``warn``
mode via :func:`run_input_validation`. A missing required role returns a structured
``BloomMCPError`` listing the available columns and naming the override
(``sample_id_column`` / ``genotype_column``); no run is persisted.

On each call it reads the **raw** frame via the :class:`ExperimentReader` port
(qc_clean is the *producer* of cleaned data, so it never sets ``require_clean``),
calls the one upstream entry point with the resolved role columns, then persists a
versioned run via the :class:`ResultStore` port — the cleaned CSV
(``CLEANED_CSV_NAME``) + the cleanup log + provenance (including an additive
``input_validation`` manifest block) — under tool class ``qc``. That filename is
what the reader resolves as a *cleaned version*, so a later ``pca_analysis``
(``require_clean=True``) consumes this run. The result returns a small in/out
summary + the resolved roles + validation warnings + links — never the table inline.

**No-NaN guarantee.** Before persisting, the tool asserts the cleaned table has
no NaNs in its kept trait columns and at least one surviving sample/trait — the
contract ``pca_analysis(require_clean=True)`` relies on. A cleanup that would
leave residual NaNs, drop every trait, or drop every sample raises a structured
``BloomMCPError`` (with a relax-the-thresholds remedy) rather than committing a
degenerate or NaN-bearing "cleaned" run.

**Shared ``qc`` tool class.** ``qc_clean`` and ``remove_outliers`` both persist
under tool class ``qc`` writing ``CLEANED_CSV_NAME``, so the reader resolves
whichever committed most recently as "latest cleaned" — prefer the natural
``qc_clean`` → ``remove_outliers`` order per experiment (the legacy
``run_qc_workflow`` + the vendored ``data_cleanup`` this row used to reference
were retired by ``devendor-bloommcp-analysis``). Versioning is single-writer
(``create_run`` allocates ``v<N>`` without compare-and-set) — safe for one
bloom-mcp container; concurrent cleans on the same experiment are not guarded.
"""

from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, Field
from sleap_roots_analyze import clean_traits_for_analysis

from bloom_mcp.contract import BloomMCPError, Provenance, as_mcp_tool
from bloom_mcp.data_access import ExperimentReadError
from bloom_mcp.data_access.columns import resolve_columns, run_input_validation
from sleap_roots_analyze.data_utils import convert_to_json_serializable
from bloom_mcp.experiment_utils import CLEANED_CSV_NAME
from bloom_mcp.tools import _ports
from bloom_mcp.tools._qc_shared import (
    _CANONICAL_MAX_NANS_PER_SAMPLE,
    _CANONICAL_MAX_NANS_PER_TRAIT,
    _CANONICAL_MAX_ZEROS_PER_TRAIT,
    _CANONICAL_MIN_SAMPLES_PER_TRAIT,
    _validate_trait_subset,
)

_TOOL_CLASS = "qc"
_LOG_NAME = "cleanup_log.json"
_VALIDATION_MODE = "warn"

# Default cleanup thresholds mirror the **canonical QC pipeline** defaults, shared with
# qc_inspect and single-sourced in ``_qc_shared`` (``_CANONICAL_*``) so the two tools
# cannot silently desync — see that module for the full rationale.


class QCCleanParams(BaseModel):
    """Inputs for ``qc_clean``. No ``seed`` — QC is deterministic (threshold filters).

    The four cleanup-threshold defaults are the **canonical QC pipeline** values
    (see ``_CANONICAL_*`` above), so an unparameterized ``qc_clean`` reproduces the
    pipeline's clean rather than a looser one.

    A genotype **and** a sample identifier are **required** (auto-detected, or named
    via ``genotype_column`` / ``sample_id_column``) so every cleaned sample is
    traceable; if a required role can't be resolved the call fails with a structured
    error listing the available columns.
    """

    experiment: str = Field(
        ..., description="Experiment identifier from list_available_experiments."
    )
    trait_columns: Optional[list[str]] = Field(
        default=None,
        description="Subset of trait columns to clean; omit to clean all detected "
        "traits. Wins over exclude_columns when a column appears in both.",
    )
    sample_id_column: Optional[str] = Field(
        default=None,
        description="Column that uniquely identifies each sample (barcode/plant id). "
        "Omit to auto-detect; a sample identifier is REQUIRED for traceability.",
    )
    genotype_column: Optional[str] = Field(
        default=None,
        description="Genotype/accession column. Omit to auto-detect; a genotype is "
        "REQUIRED. Note: supplying an override that was previously auto-detected as a "
        "different role (or a numeric column previously treated as a trait) shifts that "
        "column's role assignment — see excluded_columns in the result.",
    )
    exclude_columns: Optional[list[str]] = Field(
        default=None,
        description="Metadata columns to exclude from the trait set (deny-list). "
        "An explicit trait_columns allow-list wins over this.",
    )
    max_zeros_per_trait: float = Field(
        default=_CANONICAL_MAX_ZEROS_PER_TRAIT,
        ge=0.0,
        le=1.0,
        description="Max fraction of zeros per trait before the trait is dropped. "
        "Default mirrors the canonical QC pipeline (CleanupConfig.max_zeros_per_trait=0.5).",
    )
    max_nans_per_trait: float = Field(
        default=_CANONICAL_MAX_NANS_PER_TRAIT,
        ge=0.0,
        le=1.0,
        description="Max fraction of NaNs per trait before the trait is dropped. "
        "Default mirrors the canonical QC pipeline (CleanupConfig.max_nans_per_trait=0.2).",
    )
    max_nans_per_sample: float = Field(
        default=_CANONICAL_MAX_NANS_PER_SAMPLE,
        ge=0.0,
        le=1.0,
        description="Max fraction of NaNs per sample before the sample is dropped. "
        "Default mirrors the canonical QC pipeline (CleanupConfig.max_nan_fraction=0.0): "
        "any sample with a residual NaN in a kept trait is dropped.",
    )
    min_samples_per_trait: int = Field(
        default=_CANONICAL_MIN_SAMPLES_PER_TRAIT,
        ge=1,
        description="Min valid samples required to keep a trait. "
        "Default mirrors the canonical QC pipeline (CleanupConfig.min_samples_per_trait=10).",
    )
    user_label: Optional[str] = Field(
        default=None,
        description="Optional slug appended to the version directory name.",
    )


class QCCleanResult(BaseModel):
    """A small in/out summary + resolved roles + validation findings + links."""

    experiment: str
    source: str
    n_samples_in: int
    n_samples_out: int
    n_traits_in: int
    n_traits_out: int
    n_samples_dropped: int
    n_traits_dropped: int
    sample_retention: float
    trait_retention: float
    kept_trait_columns: list[str]
    removed_traits: list[str]
    # Resolved role columns + the metadata excluded from the trait set (#403), so the
    # agent/scientist sees exactly what was treated as genotype/sample_id/replicate and
    # what numeric metadata (e.g. Computation.Time.s) was dropped from analysis.
    genotype_column: str
    sample_id_column: str
    replicate_column: Optional[str]
    excluded_columns: list[str]
    # Advisory findings from analyze's input contract (warn mode); the run still commits.
    validation_warnings: list[str]
    # NaN counts are scoped explicitly: the *input* (raw) frame vs the persisted
    # cleaned frame. `cleaned_nan_cells_remaining` is guaranteed 0 (see guard).
    input_nan_summary: dict[str, int]
    cleaned_nan_cells_remaining: int
    run_ref: str
    version_dir: str
    manifest_path: str
    outputs: dict[str, str]
    next_step: Optional[str] = Field(
        default=None,
        description=(
            "Advisory populated only when cleaning dropped samples: nudges the "
            "caller to run qc_inspect to see which traits drove the loss and get a "
            "threshold recommendation. None when no samples were dropped."
        ),
    )


@as_mcp_tool(
    input_model=QCCleanParams,
    output_model=QCCleanResult,
    errors=(ExperimentReadError,),
)
def qc_clean(params: QCCleanParams, *, provenance: Provenance) -> QCCleanResult:
    """Clean ``experiment`` via analyze's ``clean_traits_for_analysis`` and persist it."""
    reader = _ports.reader()
    store = _ports.store()

    # qc_clean is the producer of cleaned data, so it must always clean from the
    # RAW input — never re-clean a prior cleaned artifact. Force version="raw" so a
    # re-run (after a cleaned version already exists) still reads raw rather than
    # the default "latest" resolution, which would resolve the newest _cleaned.csv.
    frame = reader.load_experiment(params.experiment, version="raw")

    # B-4: the same column cannot serve as both genotype label and sample identifier.
    if (
        params.genotype_column is not None
        and params.sample_id_column is not None
        and params.genotype_column == params.sample_id_column
    ):
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"genotype_column and sample_id_column both name the same column "
                f"{params.genotype_column!r}. A single column cannot serve as both "
                f"genotype label and sample identifier."
            ),
            remedy="Supply different columns for genotype_column and sample_id_column.",
        )

    # BLOCK-2: Only role overrides (sample_id_column, genotype_column) must name a
    # column that actually exists. exclude_columns is a deny-list — absent entries are
    # a silent no-op (a shared config may list columns not present in every experiment).
    role_overrides = [
        c for c in (params.sample_id_column, params.genotype_column) if c is not None
    ]
    unknown = [c for c in role_overrides if c not in frame.df.columns]
    if unknown:
        raise BloomMCPError(
            code="invalid_input",
            message=f"Column override names columns not in {params.experiment!r}: "
            f"{sorted(set(unknown))}.",
            remedy="Use column names from list_available_experiments / "
            "load_experiment_data.",
        )

    # Resolve roles (bloommcp matching, honoring overrides) + traits (delegated to
    # get_trait_columns, so numeric metadata is excluded from analysis).
    resolved = resolve_columns(
        frame.df,
        sample_id_column=params.sample_id_column,
        genotype_column=params.genotype_column,
        exclude_columns=params.exclude_columns,
    )

    # BLOCK-2 (post-resolution): the partial-override case — e.g. genotype_column="Barcode"
    # when Barcode is also auto-detected as sample_id — cannot be caught by the pre-resolution
    # B-4 guard (which only fires when both params are explicit). Check after resolution so
    # any combination of explicit/auto-detected roles is covered.
    if resolved.genotype is not None and resolved.genotype == resolved.sample_id:
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"Column {resolved.genotype!r} resolved as both genotype and "
                f"sample_id. A single column cannot serve as both roles."
            ),
            remedy=(
                "Supply different columns via genotype_column and sample_id_column, "
                "or rename the column so only one role pattern matches."
            ),
        )

    # Build role_info once — used for BLOCK-4 (absorbed exclusion warnings) and for the
    # role-as-trait error message (explains HOW each column became a role).
    role_info: dict[str, str] = {}
    if resolved.genotype:
        how = (
            "explicitly set via genotype_column"
            if params.genotype_column
            else "auto-detected"
        )
        role_info[resolved.genotype] = f"genotype ({how})"
    if resolved.sample_id:
        how = (
            "explicitly set via sample_id_column"
            if params.sample_id_column
            else "auto-detected"
        )
        role_info[resolved.sample_id] = f"sample_id ({how})"
    if resolved.replicate:
        role_info[resolved.replicate] = "replicate (auto-detected)"

    # BLOCK-4: when an explicit exclude_columns entry is the resolved role column,
    # resolve_columns absorbs it silently. Surface this as a validation_warning so the
    # caller knows their exclusion had no effect.
    absorbed_warnings: list[str] = [
        f"exclude_columns: {col!r} is the resolved {role_info[col]} and cannot be "
        f"excluded from that role — the exclusion was absorbed."
        for col in (params.exclude_columns or [])
        if col in role_info
    ]

    # Traceability guard — a bloommcp policy enforced BEFORE the contract call, so it
    # holds even when sleap-roots-contracts is absent (the warn-mode contract alone
    # would not fail a missing sample_id). An untraceable cleaned frame is the root
    # cause of the barcode-less remove_outliers crash (#403/#400).
    missing = []
    if resolved.genotype is None:
        missing.append(("genotype", "genotype_column"))
    if resolved.sample_id is None:
        missing.append(("sample-identifier", "sample_id_column"))
    if missing:
        roles_txt = " and ".join(role for role, _ in missing)
        params_txt = ", ".join(f"{p}=<name>" for _, p in missing)
        raise BloomMCPError(
            code="assumption_violated",
            message=(
                f"No {roles_txt} column detected in {params.experiment!r}. "
                f"Available columns: {list(frame.df.columns)}."
            ),
            remedy=(
                f"Ask the user which column identifies each sample/genotype, then "
                f"re-call with {params_txt}. A genotype and a sample identifier are "
                f"required so every cleaned/flagged sample is traceable to a real "
                f"plant/scan."
            ),
        )

    # K — guard against a genotype override that names a column whose values are
    # entirely NaN/blank. resolved.genotype is non-None (column name resolved), but
    # the run would be scientifically untraceable. Check here so the bloommcp guard
    # catches it independently of the contract's warn-mode behaviour.
    geno_vals = frame.df[resolved.genotype].dropna()
    if geno_vals.empty or (geno_vals.astype(str).str.strip() == "").all():
        raise BloomMCPError(
            code="assumption_violated",
            message=(
                f"Genotype column {resolved.genotype!r} in {params.experiment!r} is "
                f"entirely NaN or blank — no sample is traceable to a genotype."
            ),
            remedy=(
                "Ensure the genotype column has valid (non-blank) values, or use "
                "genotype_column to name a different column."
            ),
        )

    if params.trait_columns is not None:
        _validate_trait_subset(frame, params.trait_columns, params.experiment)
        # A — reject any caller-supplied trait column that resolve_columns promoted to
        # a role. Use role_info so the error explains HOW each column became a role.
        as_role = [c for c in params.trait_columns if c in role_info]
        if as_role:
            details = ", ".join(f"{c!r} [{role_info[c]}]" for c in as_role)
            raise BloomMCPError(
                code="invalid_input",
                message=(
                    f"trait_columns includes resolved role columns: {details}. "
                    f"Role columns (genotype, sample_id, replicate) cannot be analyzed "
                    f"as traits."
                ),
                remedy=(
                    "Remove the role column(s) from trait_columns, or use "
                    "genotype_column / sample_id_column to remap the role to a "
                    "different column."
                ),
            )
    trait_cols = params.trait_columns or resolved.trait_cols

    # Run analyze's input contract in warn mode (delegated; no contract logic here).
    # warn surfaces advisories without failing minor issues, but still RAISES on the
    # universal structural errors — map that to a structured, self-correctable error.
    # Note: this validates the *input frame*, not the (possibly narrowed) trait_columns
    # selection, so a caller who narrows the analysis may still see advisories about
    # columns they excluded — intentional (the contract is about the input, not the pick).
    try:
        contract_warnings = run_input_validation(
            frame.df,
            resolved,
            exclude_columns=params.exclude_columns,
            mode=_VALIDATION_MODE,
        )
    except ValueError as exc:
        raise BloomMCPError(
            code="assumption_violated",
            message=f"Input failed the analysis contract: {exc}",
            remedy=(
                "Fix the flagged structural issue (e.g. ensure the genotype column "
                "has no blank/NaN values and at least one numeric trait is present), "
                "then retry."
            ),
        ) from None
    # BLOCK-4: absorbed_warnings (role-absorbed exclusions) prepended so they appear
    # first; contract_warnings follow.
    validation_warnings = absorbed_warnings + contract_warnings

    # BLOCK-3: when an allow-list (trait_columns) wins over the deny-list (exclude_columns),
    # a column appears in both resolved.excluded_cols and trait_cols — a contradiction in
    # the result. Compute the effective excluded set as what was NOT actually analyzed.
    trait_col_set = set(trait_cols)
    effective_excluded = [c for c in resolved.excluded_cols if c not in trait_col_set]

    n_samples_in = len(frame.df)
    n_traits_in = len(trait_cols)

    # Role kwargs from the RESOLVED columns (genotype + sample_id guaranteed present;
    # replicate omitted when None so the delegate applies its own default).
    role_kwargs = {
        "barcode_col": resolved.sample_id,
        "genotype_col": resolved.genotype,
    }
    if resolved.replicate is not None:
        role_kwargs["replicate_col"] = resolved.replicate

    # Delegate ALL cleanup + validate. No QC logic lives here. The delegate
    # *raises* ValueError on its degenerate cases (too-strict thresholds leaving
    # <2 samples / no non-constant trait / all traits dropped / empty input) — the
    # common real-world misuse. Map it to a structured, self-correctable error
    # (its message is already actionable) rather than letting it fall through to
    # the contract's opaque `internal_error`.
    try:
        cleaned_df, kept_cols, log = clean_traits_for_analysis(
            frame.df,
            trait_cols=trait_cols,
            max_zeros_per_trait=params.max_zeros_per_trait,
            max_nans_per_trait=params.max_nans_per_trait,
            max_nans_per_sample=params.max_nans_per_sample,
            min_samples_per_trait=params.min_samples_per_trait,
            **role_kwargs,
        )
    except ValueError as exc:
        raise BloomMCPError(
            code="assumption_violated",
            message=f"Cleanup could not produce an analysis-ready table: {exc}",
            remedy=(
                "Relax the cleanup thresholds (e.g. raise max_nans_per_trait / "
                "max_zeros_per_trait / max_nans_per_sample, or lower "
                "min_samples_per_trait) and retry."
            ),
        ) from None

    kept_cols = list(kept_cols)
    n_samples_out = len(cleaned_df)
    n_traits_out = len(kept_cols)

    # No-NaN / non-degenerate guarantee — defense-in-depth for a delegate that
    # *returns* (rather than raises on) a degenerate frame. Enforced before any
    # run is committed so a bad cleanup never ships a NaN-bearing or empty
    # "cleaned" artifact that pca_analysis(require_clean=True) would resolve and
    # fail on opaquely.
    cleaned_nan_cells = (
        int(cleaned_df[kept_cols].isna().sum().sum()) if kept_cols else 0
    )
    if not kept_cols or n_samples_out == 0 or cleaned_nan_cells > 0:
        reason = (
            "removed every trait column"
            if not kept_cols
            else (
                "removed every sample"
                if n_samples_out == 0
                else f"left {cleaned_nan_cells} NaN cell(s) in the kept trait columns"
            )
        )
        raise BloomMCPError(
            code="assumption_violated",
            message=f"Cleanup produced no analysis-ready table — it {reason}.",
            remedy=(
                "Relax the cleanup thresholds (e.g. raise max_nans_per_trait / "
                "max_zeros_per_trait / max_nans_per_sample, or lower "
                "min_samples_per_trait) and retry."
            ),
        )

    removed_traits = [c for c in trait_cols if c not in kept_cols]
    nan_mask = frame.df[trait_cols].isna()
    input_nan_summary = {
        "input_samples_with_nan_trait": int(nan_mask.any(axis=1).sum()),
        "input_traits_with_nan": int(nan_mask.any(axis=0).sum()),
        "input_nan_cells": int(nan_mask.sum().sum()),
    }

    # Additive manifest block: the resolved roles, excluded metadata, and warn-mode
    # findings, stamped onto the provenance so it lands in the version entry. The
    # contract_version is the provenance-recorded sleap-roots-contracts version (not
    # a live read) so the record is reproducible.
    input_validation_block = {
        "mode": _VALIDATION_MODE,
        "contract_version": provenance.code_versions.sleap_roots_contracts,
        "resolved_roles": {
            "genotype": resolved.genotype,
            "sample_id": resolved.sample_id,
            "replicate": resolved.replicate,
        },
        "excluded_columns": effective_excluded,
        "warnings": validation_warnings,
    }
    provenance = provenance.model_copy(
        update={"input_validation": input_validation_block}
    )

    # Persist a versioned cleaned run via the ResultStore port; the contract-stamped
    # provenance is carried into the manifest (no re-stamp). source_csv (when the raw
    # is on the local FS) lets the manifest content-address the cleaned run to its
    # input — sourced through the active reader so a custom BLOOM_EXPERIMENT_LOCAL_ROOT
    # is honoured rather than a hard-coded TRAITS_DIR (mirrors _ports.start_run).
    run = store.create_run(
        experiment=params.experiment,
        tool_class=_TOOL_CLASS,
        provenance=provenance,
        user_label=params.user_label,
        source_csv=_ports.raw_source_for(params.experiment),
        source=frame.resolved_source,
    )
    cleaned_df.to_csv(run.staging_dir / CLEANED_CSV_NAME, index=False)
    (run.staging_dir / _LOG_NAME).write_text(
        json.dumps(convert_to_json_serializable(log), indent=2)
    )
    stored = store.commit(
        run,
        {CLEANED_CSV_NAME: CLEANED_CSV_NAME, _LOG_NAME: _LOG_NAME},
    )

    # Message-only tie-in to qc_inspect (#360): when cleaning drops samples, nudge
    # the caller to inspect the missingness that drove the loss. Kept out of the
    # cleanup logic (no behavior change) — it is purely advisory on the summary.
    n_samples_dropped = n_samples_in - n_samples_out
    sample_word = "sample" if n_samples_in == 1 else "samples"
    next_step = (
        f"Cleaning dropped {n_samples_dropped} of {n_samples_in} {sample_word}. Run "
        f"qc_inspect on {params.experiment!r} to see which traits drove the loss "
        f"and get a max_nans_per_trait recommendation that retains more samples."
        if n_samples_dropped > 0
        else None
    )

    return QCCleanResult(
        experiment=params.experiment,
        source=frame.source,
        n_samples_in=n_samples_in,
        n_samples_out=n_samples_out,
        n_traits_in=n_traits_in,
        n_traits_out=n_traits_out,
        n_samples_dropped=n_samples_dropped,
        n_traits_dropped=n_traits_in - n_traits_out,
        sample_retention=(
            round(n_samples_out / n_samples_in, 4) if n_samples_in else 0.0
        ),
        trait_retention=round(n_traits_out / n_traits_in, 4) if n_traits_in else 0.0,
        kept_trait_columns=kept_cols,
        removed_traits=removed_traits,
        genotype_column=resolved.genotype,
        sample_id_column=resolved.sample_id,
        replicate_column=resolved.replicate,
        excluded_columns=effective_excluded,
        validation_warnings=validation_warnings,
        input_nan_summary=input_nan_summary,
        cleaned_nan_cells_remaining=cleaned_nan_cells,
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
        next_step=next_step,
    )
