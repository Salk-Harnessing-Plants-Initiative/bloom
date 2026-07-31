"""umap_analysis — UMAP on a cleaned experiment, delegating to sleap-roots-analyze.

The fourth granular **consumer** (Tier 4 / #425), parallel to ``pca_analysis`` (#377) and
sibling to ``clustering`` (#309): it reads a *cleaned* experiment through the
:class:`ExperimentReader` port with ``require_clean=True`` and delegates **all** UMAP
computation to ``sleap_roots_analyze.perform_umap_analysis``, wrapping the result into the
upstream typed :class:`UMAPResult` via ``UMAPResult.from_umap_dict``. The MCP owns no UMAP
math of its own.

**Consume, don't re-clean.** Same certified-clean restriction as ``pca_analysis``/
``clustering``: a requested trait column outside the certified set, or one that still
carries a non-finite value, is rejected with ``invalid_input`` / ``assumption_violated``
rather than silently row-dropped by the delegate's internal NaN check.

**Stochastic — unlike ``pca_analysis``.** UMAP's embedding genuinely depends on the seed
(unlike PCA's inert one in this tool's regime), so this tool declares ``random_state`` —
mirroring ``clustering`` — so the contract resolves ``seed`` into it and stamps the
resolved integer into provenance (never ``None``).

**``n_neighbors`` bounded, not silently clamped.** ``perform_umap_analysis`` silently clamps
``n_neighbors`` to ``n_samples - 1`` when the request is larger, rather than raising. This
tool pre-checks that bound and raises a structured ``assumption_violated`` instead, so the
persisted result never silently describes a different ``n_neighbors`` than requested
(talmolab/sleap-roots-analyze#67).

**Non-finite embedding guarded before persistence.** ``UMAPResult.to_json()`` raises
``ValueError`` on a non-finite embedding value. Rather than let that raise inside the
``create_run``/``commit`` region (leaking an orphaned staging dir and an unhandled
``internal_error``), this tool checks embedding finiteness immediately after the delegate
call and before any run is created.

It persists a versioned run under tool class ``umap`` — the embedding coordinates as a CSV
(with sample identity) and the serialized ``UMAPResult`` — recording ``based_on_version`` =
the consumed cleaned version, and returns a shape/seed summary + links (never the embedding
matrix inline).

**Optional plots (#425, reusing #426's shared helper).** ``include_plots=True`` generates
figures via the two catalog plotters in ``_umap_plot_calls`` (``plots`` narrows the
selection; omit for both) and persists them as additional ``*.png`` entries in the existing
``outputs`` field — no new result field. Note: this module's own top-level
``sleap_roots_analyze`` import (below) already pulls in ``matplotlib`` transitively via that
package's ``visualization`` submodule, the same as ``pca_analysis``/``clustering`` — the lazy
plotter import inside ``_umap_plot_calls`` avoids a *second*, redundant import on the
``include_plots=True`` path, but does not itself keep matplotlib out of ``sys.modules`` on the
default path; no Tier-0 import-clean guarantee is claimed here. Figure construction is
delegated entirely to ``bloom_mcp.tools._plots`` (validate/generate/close), reused
**verbatim** from the PCA-plots change with no modification. ``create_umap_colored_by_top_traits``
needs a ``pca_results`` dict to rank trait contributions; this tool computes that via an
internal, **non-persisted** call to ``perform_pca_analysis`` over the exact same
certified-clean trait selection already used for the UMAP embedding (see design.md's
Decision #3 — no second versioned run is created for it).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from sleap_roots_analyze import UMAPResult, perform_pca_analysis, perform_umap_analysis

from bloom_mcp.contract import BloomMCPError, Provenance, RunLinks, as_mcp_tool
from bloom_mcp.data_access import (
    CleanedVersionRequiredError,
    ExperimentFrame,
    ExperimentReadError,
)
from bloom_mcp.tools import _ports
from bloom_mcp.tools._consumer_utils import _build_output_frame, snapshot_frame
from bloom_mcp.tools._plots import close_figures, generate_figures, validate_plot_keys
from bloom_mcp.tools._qc_shared import _validate_trait_subset

logger = logging.getLogger(__name__)

_TOOL_CLASS = "umap"
_EMBEDDING_NAME = "embedding_coords.csv"
_RESULT_NAME = "umap_result.json"

# Valid plot keys — two upstream plotters callable from UMAP outputs.
_UMAP_CATALOG_KEYS: frozenset[str] = frozenset(
    {"create_umap_single_trait", "create_umap_colored_by_top_traits"}
)

# Upper bound on n_components. UMAP has no natural clamp the way PCA does (variance-bounded),
# and this is an LLM-driven, low-trust input surface (the same risk LocalReader's own
# docstring flags for experiment names) — nothing but this field constraint stops a request
# like n_components=10_000_000, which would risk the OS OOM-killer on a shared container
# well before any Python-level exception handler could intervene. 50 is generous for any
# real embedding/visualization use (2-3 dims is typical; a few dozen is already unusual) while
# still catching a runaway or adversarial request.
_MAX_N_COMPONENTS = 50


class UMAPAnalysisParams(BaseModel):
    """Inputs for ``umap_analysis``. Stochastic: the resolved ``seed`` drives the fit."""

    experiment: str = Field(
        ...,
        description="Experiment identifier to analyze. Must have a cleaned version "
        "produced by qc_clean; umap_analysis consumes it (require_clean).",
    )
    trait_columns: list[str] | None = Field(
        default=None,
        description="Subset of cleaned trait columns to embed; omit to use all "
        "certified-clean traits. Each must be a cleaned trait column of the experiment. "
        "Pass at least one column with no duplicates (an empty list is rejected).",
    )
    n_neighbors: int = Field(
        default=15,
        ge=2,
        description="Size of the local neighborhood UMAP considers. Must be at least 2 "
        "(umap-learn's own hard constraint — n_neighbors=1 is never valid, for any data, "
        "and is rejected as invalid_input rather than reaching the delegate) and strictly "
        "less than the certified-clean sample count (rejected as assumption_violated "
        "otherwise, rather than silently clamped).",
    )
    min_dist: float = Field(
        default=0.1,
        ge=0.0,
        description="Minimum distance between embedded points; controls how tightly UMAP "
        "packs points together.",
    )
    n_components: int = Field(
        default=2,
        ge=1,
        le=_MAX_N_COMPONENTS,
        description=f"Number of embedding dimensions (1-{_MAX_N_COMPONENTS}). The upper "
        "bound is a sanity ceiling on this LLM-driven input surface, not a scientific "
        "limit — legitimate use is almost always 2-3.",
    )
    seed: int = Field(
        default=42,
        ge=0,
        description="Random seed driving the stochastic UMAP fit; resolved and recorded in "
        "the run's provenance.",
    )
    include_plots: bool = Field(
        default=False,
        description="If true, generate and persist UMAP plots as run artifacts. "
        "Returned as additional entries in outputs (object-key links). "
        "When false (default), no figures are generated (though matplotlib may already be "
        "resident in the process via this module's own sleap_roots_analyze import — see "
        "the module docstring; this flag does not control that).",
    )
    plots: list[str] | None = Field(
        default=None,
        description="Subset of plot keys to generate; omit (None) to generate both "
        "available plots when include_plots=True. Ignored when include_plots=False. "
        "Valid keys: create_umap_single_trait, create_umap_colored_by_top_traits.",
    )
    user_label: str | None = Field(
        default=None,
        description="Optional slug appended to the version directory name.",
    )


class UMAPAnalysisResult(RunLinks):
    """A shape/seed summary + links to the persisted UMAP run (no embedding inline)."""

    experiment: str
    source: str
    n_samples: int
    n_features: int
    n_components: int
    feature_names: list[str]
    n_neighbors: int
    min_dist: float
    standardized: bool
    seed: int


def _umap_plot_calls(
    result_dict: dict,
    frame: ExperimentFrame,
    trait_cols: list[str],
) -> dict:
    """Return zero-arg callables for each catalog plot key, lazily importing plotters.

    Plotters are imported here (not at module level) to avoid a second, redundant import
    on the ``include_plots=True`` path. This does NOT keep matplotlib out of
    ``sys.modules`` on the default path — this module's top-level ``sleap_roots_analyze``
    import already pulls matplotlib in transitively (see the module docstring).
    """
    from sleap_roots_analyze import (
        create_umap_colored_by_top_traits,
        create_umap_single_trait,
    )

    def _top_traits():
        # Internal, non-persisted PCA call over the exact same certified-clean trait
        # selection already validated and used for the UMAP embedding — see design.md's
        # Decision #3. This is never committed as its own versioned run.
        #
        # Same failure-translation as the main UMAP delegate call (including the same
        # exception tuple — a degenerate selection can fail PCA even though it fit UMAP
        # successfully, since PCA's standardization/eigendecomposition is stricter about
        # near-constant columns than UMAP is), and this call happens before
        # store.create_run() (so no run is ever orphaned by it either way) — but without
        # this except, the raw exception would propagate as an opaque internal_error
        # instead of the actionable assumption_violated every other delegate failure in
        # this tool surfaces.
        try:
            pca_result_dict = perform_pca_analysis(frame.df[trait_cols])
        except (ValueError, KeyError, RuntimeError, TypeError) as exc:
            logger.debug(
                "internal perform_pca_analysis call for create_umap_colored_by_top_traits "
                "failed, translating to assumption_violated: %s: %s",
                type(exc).__name__,
                exc,
            )
            raise BloomMCPError(
                code="assumption_violated",
                message=(
                    "Could not rank trait contributions for "
                    "create_umap_colored_by_top_traits — the certified-clean trait "
                    "selection is degenerate for PCA."
                ),
                remedy=(
                    "Select a broader set of numeric trait columns, or omit "
                    "create_umap_colored_by_top_traits from plots, then retry."
                ),
            ) from None
        return create_umap_colored_by_top_traits(
            result_dict,
            frame.df,
            trait_cols,
            trait_cols,
            pca_result_dict,
        )

    return {
        "create_umap_single_trait": lambda: create_umap_single_trait(
            result_dict, frame.df, trait_cols[0]
        ),
        "create_umap_colored_by_top_traits": _top_traits,
    }


@as_mcp_tool(
    input_model=UMAPAnalysisParams,
    output_model=UMAPAnalysisResult,
    errors=(ExperimentReadError,),
)
def umap_analysis(
    params: UMAPAnalysisParams, *, random_state: int, provenance: Provenance
) -> UMAPAnalysisResult:
    """Run UMAP on a cleaned ``experiment`` via ``perform_umap_analysis`` and persist it."""
    reader = _ports.reader()
    store = _ports.store()

    # Consumer: require a cleaned version. A missing one is a precondition failure with a
    # concrete remedy — caught here so it carries "run qc_clean first" rather than the
    # contract's generic tool_error message for the declared read error.
    try:
        frame = reader.load_experiment(params.experiment, require_clean=True)
    except CleanedVersionRequiredError:
        raise BloomMCPError(
            code="tool_error",
            message=(
                f"No cleaned version of {params.experiment!r} exists; umap_analysis "
                f"requires a cleaned input."
            ),
            remedy=f"Run qc_clean on {params.experiment!r} first, then retry umap_analysis.",
        ) from None

    if params.trait_columns is None:
        trait_cols = list(frame.trait_cols)
    else:
        _validate_trait_subset(
            frame, params.trait_columns, params.experiment, require_certified=True
        )
        trait_cols = list(params.trait_columns)
    selected = frame.df[trait_cols]

    # Defense-in-depth: the certified-clean set must be finite (same guard as
    # pca_analysis/clustering).
    if not np.isfinite(selected.to_numpy(dtype=float)).all():
        raise BloomMCPError(
            code="assumption_violated",
            message=(
                "The cleaned experiment carries non-finite values (NaN or ±inf) in its "
                "certified trait columns."
            ),
            remedy="Re-run qc_clean to produce a finite-valued cleaned version, then retry.",
        )

    # n_neighbors >= n_samples: the delegate silently clamps to n_samples - 1 rather than
    # raising. Guard before dispatch so the persisted result never silently describes a
    # different n_neighbors than requested (design.md's Decision; sleap-roots-analyze#67).
    n_samples = len(selected)
    if params.n_neighbors >= n_samples:
        raise BloomMCPError(
            code="assumption_violated",
            message=(
                f"n_neighbors={params.n_neighbors} is >= the certified-clean sample count "
                f"({n_samples}); UMAP requires n_neighbors < n_samples."
            ),
            remedy=(
                f"Use n_neighbors <= {n_samples - 1}, or supply more samples, then retry."
            ),
        )

    # Delegate ALL UMAP computation. Multiple upstream failure modes map to one remedy
    # (broader than pca_analysis's single ValueError — mirrors clustering's rationale for
    # catching more than one exception type). TypeError is included alongside
    # ValueError/KeyError/RuntimeError because umap-learn's spectral-embedding eigensolver
    # can raise a bare TypeError ("Cannot use scipy.linalg.eigh for sparse A with k >= N...")
    # for legitimate small-sample-count parameter combinations near the n_neighbors/n_samples
    # boundary (verified directly against the installed sleap_roots_analyze/umap-learn) —
    # not just the documented ValueError/KeyError from perform_umap_analysis's own docstring.
    try:
        result_dict = perform_umap_analysis(
            selected,
            feature_cols=trait_cols,
            n_neighbors=params.n_neighbors,
            min_dist=params.min_dist,
            n_components=params.n_components,
            random_state=random_state,
        )
    except (ValueError, KeyError, RuntimeError, TypeError) as exc:
        # Logged (not leaked): the raw message may carry backend internals (see the
        # no-leak test), but the original exception type/text is useful server-side to
        # tell "genuinely degenerate data" apart from "a new upstream failure mode this
        # except clause doesn't name yet" without needing to reproduce the call.
        logger.debug(
            "perform_umap_analysis failed, translating to assumption_violated: %s: %s",
            type(exc).__name__,
            exc,
        )
        raise BloomMCPError(
            code="assumption_violated",
            message=(
                "UMAP could not fit the selected traits — the cleaned selection is "
                "degenerate, or the requested parameters are incompatible with it."
            ),
            remedy=(
                "Select a broader set of numeric trait columns, adjust n_neighbors / "
                "min_dist / n_components, then retry."
            ),
        ) from None

    # Non-finite embedding guard, BEFORE any run is created: UMAPResult.to_json() raises on
    # a non-finite value (allow_nan=False). Checking here avoids leaking an orphaned staging
    # dir and an unhandled internal_error (see design.md's "Non-finite embedding" decision).
    embedding = np.asarray(result_dict["embedding"], dtype=float)
    if not np.isfinite(embedding).all():
        raise BloomMCPError(
            code="assumption_violated",
            message=(
                "UMAP produced a non-finite embedding coordinate (NaN or infinite) for "
                "this selection and seed."
            ),
            remedy=(
                "Try a different seed, adjust n_neighbors/min_dist, or select a broader "
                "set of trait columns, then retry."
            ),
        )

    umap = UMAPResult.from_umap_dict(result_dict, random_state=random_state)

    prov = provenance.model_copy(update={"based_on_version": frame.source})
    embedding_df = pd.DataFrame(
        umap.embedding, columns=[f"UMAP{i + 1}" for i in range(umap.n_components)]
    )

    # Optional plots — validate keys and generate figures BEFORE create_run so an unknown
    # key fails as invalid_input with no run committed. The try/finally wraps the whole
    # persistence region so figures are always closed even when the tempdir entry or store
    # operations fail (mirrors pca_analysis's nesting — see design.md).
    figures: dict = {}
    try:
        if params.include_plots:
            import matplotlib

            matplotlib.use("Agg")
            validate_plot_keys(params.plots, _UMAP_CATALOG_KEYS)
            calls = _umap_plot_calls(result_dict, frame, trait_cols)
            keys_to_generate = (
                list(params.plots)
                if params.plots is not None
                else list(_UMAP_CATALOG_KEYS)
            )
            generate_figures({k: calls[k] for k in keys_to_generate}, figures)

        with snapshot_frame(frame.df) as source_snapshot:
            run = store.create_run(
                experiment=params.experiment,
                tool_class=_TOOL_CLASS,
                provenance=prov,
                user_label=params.user_label,
                source_csv=source_snapshot,
            )
            _build_output_frame(frame, embedding_df).to_csv(
                run.staging_dir / _EMBEDDING_NAME, index=False
            )
            (run.staging_dir / _RESULT_NAME).write_text(umap.to_json())
            outputs: dict[str, str] = {
                _EMBEDDING_NAME: _EMBEDDING_NAME,
                _RESULT_NAME: _RESULT_NAME,
            }
            for name, fig in figures.items():
                rel = f"{name}.png"
                fig.savefig(run.staging_dir / rel, bbox_inches="tight")
                outputs[rel] = rel
            stored = store.commit(run, outputs)
    finally:
        close_figures(figures)

    return UMAPAnalysisResult(
        experiment=params.experiment,
        source=frame.source,
        n_samples=umap.n_samples,
        n_features=len(umap.feature_names),
        n_components=umap.n_components,
        feature_names=list(umap.feature_names),
        n_neighbors=umap.n_neighbors,
        min_dist=umap.min_dist,
        standardized=umap.standardized,
        seed=random_state,
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
    )
