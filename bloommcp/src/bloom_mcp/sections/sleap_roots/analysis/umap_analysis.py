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

**Optional font-style override (#661).** ``plot_font_family``/``plot_font_size`` are
forwarded into ``_plots.generate_figures`` — the same shared helper call above — which
applies them uniformly to every generated figure's title, axis labels, tick labels,
standalone annotation text, figure-level text (e.g. a ``fig.suptitle`` — as
``create_umap_colored_by_top_traits`` sets), and legend text/title before it is persisted.
Both default to ``None`` (no override) and are ignored when ``include_plots=False``.
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
from bloom_mcp.result_store import CommitFailedError, ManifestReadError
from bloom_mcp.tools import _ports
from bloom_mcp.tools._consumer_utils import _build_output_frame, snapshot_frame
from bloom_mcp.tools._plots import (
    MAX_PLOT_FONT_SIZE,
    MAX_PLOT_POINT_SIZE,
    check_plot_style_ceiling,
    close_figures,
    generate_figures,
    validate_plot_keys,
)
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

# plot_font_size/plot_point_size ceilings (#721) live in bloom_mcp.tools._plots as
# MAX_PLOT_FONT_SIZE/MAX_PLOT_POINT_SIZE — shared with pca_analysis.py so the two tools
# can't silently desync on the same ceiling value.

# Allowlist for plot_cmap (#721): matplotlib exposes no runtime categorization of its own
# colormap registry (matplotlib.colormaps is a flat name -> Colormap mapping with no
# sequential/diverging/qualitative metadata), so this list is hand-authored from matplotlib's
# documented "Choosing Colormaps" reference categories: Perceptually Uniform Sequential,
# Sequential, Sequential (2), and Diverging. Restricting to these two families (as opposed to
# Qualitative/Cyclic/Miscellaneous) keeps plot_cmap limited to colormaps that render a
# continuous trait faithfully — a qualitative map like tab10 chops a continuous value into
# discrete-looking bands, and a cyclic map like hsv puts the same color at both ends of the
# scale. A future matplotlib release could rename or add colormaps this list doesn't know
# about yet — accepted risk (design.md Decision 3): the failure direction is a legitimate new
# name being rejected until this list is updated, never an invalid one being silently
# accepted. Note the "Sequential (2)" category (spring/summer/copper/etc.) and Spectral are
# flagged by matplotlib's own docs as not perceptually uniform — a real but weaker gap than
# the qualitative/cyclic maps (tab10/hsv) this allowlist is specifically guarding against;
# accepted as still a strict improvement over admitting those too.
#
# Tied to bloommcp's declared dependency floor: pyproject.toml pins "matplotlib>=3.7.0" (no
# upper bound). Every name below must be registered as of that FLOOR version, not just in
# whatever matplotlib happens to be installed here — otherwise an install that resolves to an
# older-but-still-permitted matplotlib would pass this allowlist check for a name it doesn't
# actually have, then hit the exact opaque matplotlib error this allowlist exists to prevent.
# Diverging colormaps "berlin"/"managua"/"vanimo" (Crameri's scientific colour maps) were only
# added in matplotlib 3.10.0 — excluded here for that reason. Re-add them if/when the
# pyproject.toml floor is bumped to >=3.10.0.
_ALLOWED_CMAP_BASE_NAMES: frozenset[str] = frozenset(
    {
        # Perceptually Uniform Sequential
        "viridis",
        "plasma",
        "inferno",
        "magma",
        "cividis",
        # Sequential
        "Greys",
        "Purples",
        "Blues",
        "Greens",
        "Oranges",
        "Reds",
        "YlOrBr",
        "YlOrRd",
        "OrRd",
        "PuRd",
        "RdPu",
        "BuPu",
        "GnBu",
        "PuBu",
        "YlGnBu",
        "PuBuGn",
        "BuGn",
        "YlGn",
        # Sequential (2)
        "binary",
        "gist_yarg",
        "gist_gray",
        "gray",
        "bone",
        "pink",
        "spring",
        "summer",
        "autumn",
        "winter",
        "cool",
        "Wistia",
        "hot",
        "afmhot",
        "gist_heat",
        "copper",
        # Diverging
        "PiYG",
        "PRGn",
        "BrBG",
        "PuOr",
        "RdGy",
        "RdBu",
        "RdYlBu",
        "RdYlGn",
        "Spectral",
        "coolwarm",
        "bwr",
        "seismic",
    }
)
_ALLOWED_CMAPS: frozenset[str] = frozenset(
    _ALLOWED_CMAP_BASE_NAMES | {f"{name}_r" for name in _ALLOWED_CMAP_BASE_NAMES}
)

# plot_cmap length cap (#721 PR review round 4): checked in the tool body, alongside the
# allowlist membership check, rather than a Pydantic Field(max_length=...) constraint —
# the same reason plot_font_size/plot_point_size's ceilings moved out of Field (see
# check_plot_style_ceiling's docstring): a Field constraint's violation is mapped by the
# contract layer into a message naming only the field and error type, never the submitted
# value, which would have been the ONE inconsistent field left doing that after this PR.
# 32 is generous — the longest real allowlisted name (with its _r variant) is 11 chars —
# and mainly guards against an arbitrarily long string being fully parsed and compared
# before the cheap allowlist rejection ever runs.
_MAX_PLOT_CMAP_LENGTH = 32


class UMAPAnalysisParams(BaseModel):
    """Inputs for ``umap_analysis``. Stochastic: the resolved ``seed`` drives the fit."""

    experiment: str = Field(
        ...,
        description="Experiment (CSV filename) to analyze. Must have a cleaned version "
        "produced by qc_clean; umap_analysis consumes it (require_clean). Resolves the most "
        "recent outlier trim when one exists for the experiment, not merely the most "
        "recent clean.",
    )
    version: str | None = Field(
        default=None,
        description="Pin the analysis to a specific committed cleaned version "
        "(e.g. 'v2'; see list_existing_analyses). Omit to use the latest "
        "cleaned version, same as today.",
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
    plot_font_family: str | None = Field(
        default=None,
        description="Font family override (e.g. 'serif', 'DejaVu Sans') applied to every "
        "text element (title, axis labels, tick labels, annotations, legend text/title) "
        "on each generated plot. Omit for each plot's default matplotlib styling. Ignored "
        "when include_plots=False. An unrecognized family name is not rejected — it "
        "silently falls back to matplotlib's default font rather than erroring, so a "
        "typo won't surface as invalid_input.",
    )
    plot_font_size: float | None = Field(
        default=None,
        json_schema_extra={"exclusiveMinimum": 0, "maximum": MAX_PLOT_FONT_SIZE},
        description=f"Font size (points) override applied to every text element on each "
        f"generated plot (0-{MAX_PLOT_FONT_SIZE}, exclusive of 0). The upper bound is a "
        f"sanity ceiling on this LLM-driven input surface, not a design limit (#721). "
        f"Checked in the tool body — not a Pydantic Field constraint, so the rejection "
        f"message names the value you submitted and the ceiling, not just a field name. "
        f"A valid value has no effect when include_plots=False (nothing is rendered to "
        f"style); an out-of-range value is rejected as invalid_input regardless of "
        f"include_plots.",
    )
    plot_cmap: str | None = Field(
        default=None,
        json_schema_extra={"maxLength": _MAX_PLOT_CMAP_LENGTH},
        description="Colormap for create_umap_single_trait's continuous trait coloring "
        "(e.g. 'plasma', 'viridis'). Restricted to matplotlib's documented sequential and "
        "diverging colormap names (plus each name's _r reversed variant); an unrecognized "
        "or excluded name (e.g. hsv, tab10 — valid matplotlib names but not sequential or "
        f"diverging, and misleading for continuous trait data), or one longer than "
        f"{_MAX_PLOT_CMAP_LENGTH} characters, is rejected as invalid_input naming the "
        "value, before any computation runs — regardless of include_plots. Has no "
        "effect on create_umap_colored_by_top_traits (its upstream signature does not "
        "accept cmap, and — separately, #721 — hardcodes its own cmap/point_size/alpha "
        "unconditionally; this field never reaches it).",
    )
    plot_point_size: float | None = Field(
        default=None,
        json_schema_extra={"exclusiveMinimum": 0, "maximum": MAX_PLOT_POINT_SIZE},
        description=f"Scatter point size for create_umap_single_trait (0-"
        f"{MAX_PLOT_POINT_SIZE}, exclusive of 0). Checked in the tool body — not a "
        f"Pydantic Field constraint, so the rejection message names the value you "
        f"submitted and the ceiling, not just a field name. A valid value has no effect "
        f"when include_plots=False (nothing is rendered to style); an out-of-range value "
        f"is rejected as invalid_input regardless of include_plots. Has no effect on "
        f"create_umap_colored_by_top_traits (its upstream signature does not accept "
        f"point_size, and — separately, #721 — hardcodes its own cmap/point_size/alpha "
        f"unconditionally; this field never reaches it).",
    )
    plot_alpha: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Point transparency (0=fully transparent, 1=fully opaque) for "
        "create_umap_single_trait. Has no effect on create_umap_colored_by_top_traits (its "
        "upstream signature does not accept alpha). Ignored (not rejected) when "
        "include_plots=False.",
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


def _compute_top_traits_pca(frame: ExperimentFrame, trait_cols: list[str]) -> dict:
    """Run the internal, non-persisted PCA fit ``create_umap_colored_by_top_traits``
    needs to rank trait contributions, over the exact same certified-clean trait
    selection already validated and used for the UMAP embedding — see design.md's
    Decision #3. Never committed as its own versioned run.

    Called by the tool body *before* ``generate_figures`` runs (#721 PR review) — not
    lazily from inside the plot callable itself — specifically so this real PCA fit
    executes outside ``FIGURE_REGISTRY_LOCK``'s held window. That lock only needs to
    cover the actual matplotlib rendering call; holding it for an unrelated PCA fit too
    would needlessly block every other concurrent plot-generating call for longer than
    the rendering itself takes.

    Same failure-translation as the main UMAP delegate call (including the same
    exception tuple — a degenerate selection can fail PCA even though it fit UMAP
    successfully, since PCA's standardization/eigendecomposition is stricter about
    near-constant columns than UMAP is), and this call happens before
    ``store.create_run()`` (so no run is ever orphaned by it either way) — but without
    this except, the raw exception would propagate as an opaque ``internal_error``
    instead of the actionable ``assumption_violated`` every other delegate failure in
    this tool surfaces.
    """
    try:
        return perform_pca_analysis(frame.df[trait_cols])
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


def _umap_plot_calls(
    result_dict: dict,
    frame: ExperimentFrame,
    trait_cols: list[str],
    *,
    plot_cmap: str | None = None,
    plot_point_size: float | None = None,
    plot_alpha: float | None = None,
    top_traits_pca_result_dict: dict | None = None,
) -> dict:
    """Return zero-arg callables for each catalog plot key, lazily importing plotters.

    Plotters are imported here (not at module level) to avoid a second, redundant import
    on the ``include_plots=True`` path. This does NOT keep matplotlib out of
    ``sys.modules`` on the default path — this module's top-level ``sleap_roots_analyze``
    import already pulls matplotlib in transitively (see the module docstring).

    ``plot_cmap``/``plot_point_size``/``plot_alpha`` are forwarded to
    ``create_umap_single_trait`` only — the sole catalog plotter here whose upstream
    signature accepts any of them (see design.md's per-plotter support table) — and only
    when set, so an unset (``None``) field reproduces the plotter's own hardcoded default
    exactly rather than passing that default back explicitly.

    ``top_traits_pca_result_dict`` must already be the *computed* result of
    ``_compute_top_traits_pca`` (or ``None`` if the caller knows
    ``create_umap_colored_by_top_traits`` won't be generated) — this function no longer
    runs that PCA fit itself, so the callable it returns for that key only ever does
    matplotlib rendering, never a fit (see ``_compute_top_traits_pca``'s docstring).
    """
    from sleap_roots_analyze import (
        create_umap_colored_by_top_traits,
        create_umap_single_trait,
    )

    single_trait_kwargs: dict = {}
    if plot_cmap is not None:
        single_trait_kwargs["cmap"] = plot_cmap
    if plot_point_size is not None:
        single_trait_kwargs["point_size"] = plot_point_size
    if plot_alpha is not None:
        single_trait_kwargs["alpha"] = plot_alpha

    return {
        "create_umap_single_trait": lambda: create_umap_single_trait(
            result_dict, frame.df, trait_cols[0], **single_trait_kwargs
        ),
        "create_umap_colored_by_top_traits": lambda: create_umap_colored_by_top_traits(
            result_dict,
            frame.df,
            trait_cols,
            trait_cols,
            top_traits_pca_result_dict,
        ),
    }


@as_mcp_tool(
    input_model=UMAPAnalysisParams,
    output_model=UMAPAnalysisResult,
    errors=(ExperimentReadError, CommitFailedError, ManifestReadError),
)
def umap_analysis(
    params: UMAPAnalysisParams, *, random_state: int, provenance: Provenance
) -> UMAPAnalysisResult:
    """Run UMAP on a cleaned ``experiment`` via ``perform_umap_analysis`` and persist it."""
    reader = _ports.reader()
    store = _ports.store()

    # Plot-style field validation (#721): checked here, first, before any I/O — these
    # three fields are derived entirely from the request, not from the loaded experiment,
    # so there is no reason to pay for reader.load_experiment (a full data read) or the
    # np.isfinite scan below before rejecting a bad one (PR review: validation this cheap
    # belongs before any computation, not just before the UMAP fit). plot_cmap is checked
    # in the tool body rather than a Pydantic @field_validator for the same reason
    # plot_font_size/plot_point_size are checked via check_plot_style_ceiling rather than
    # Field(gt=0, le=...): a Field constraint's violation is mapped by the contract layer's
    # BloomMCPError.from_input_validation into a generic message naming only the field and
    # error type, never the submitted value or the ceiling/allowlist (see
    # qc_clean.py's exactly-one-of-experiment/csv_content note for the same,
    # empirically-verified reasoning about @field_validator specifically).
    check_plot_style_ceiling(
        params.plot_font_size, field_name="plot_font_size", max_value=MAX_PLOT_FONT_SIZE
    )
    check_plot_style_ceiling(
        params.plot_point_size,
        field_name="plot_point_size",
        max_value=MAX_PLOT_POINT_SIZE,
    )
    if params.plot_cmap is not None and len(params.plot_cmap) > _MAX_PLOT_CMAP_LENGTH:
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"plot_cmap={params.plot_cmap!r} is {len(params.plot_cmap)} characters "
                f"long, exceeding the {_MAX_PLOT_CMAP_LENGTH}-character limit."
            ),
            remedy=(
                "Use a real matplotlib colormap name — none is anywhere close to "
                f"{_MAX_PLOT_CMAP_LENGTH} characters."
            ),
        )
    if params.plot_cmap is not None and params.plot_cmap not in _ALLOWED_CMAPS:
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"plot_cmap={params.plot_cmap!r} is not a recognized sequential or "
                f"diverging colormap."
            ),
            remedy=(
                "Use a matplotlib sequential or diverging colormap name (e.g. 'viridis', "
                "'plasma', 'RdBu', 'coolwarm') or its '_r' reversed variant. Qualitative "
                "colormaps (e.g. 'tab10') and cyclic colormaps (e.g. 'hsv') are not "
                "accepted — they render a continuous trait misleadingly."
            ),
        )

    # Consumer: require a cleaned version. A missing one is a precondition failure with a
    # concrete remedy — caught here so it carries "run qc_clean first" rather than the
    # contract's generic tool_error message for the declared read error.
    # #626: an explicit version selector is opt-in; omitting it makes this call
    # identical to before this change (no version kwarg -> Protocol default "latest").
    version_kwargs = {} if params.version is None else {"version": params.version}
    try:
        frame = reader.load_experiment(
            params.experiment, require_clean=True, **version_kwargs
        )
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
            keys_to_generate = (
                list(params.plots)
                if params.plots is not None
                else list(_UMAP_CATALOG_KEYS)
            )
            # Computed here, before _umap_plot_calls/generate_figures, and only when
            # actually needed — not lazily inside the plot callable itself — so this
            # real PCA fit runs outside FIGURE_REGISTRY_LOCK's held window (#721 PR
            # review; see _compute_top_traits_pca's docstring).
            top_traits_pca_result_dict = (
                _compute_top_traits_pca(frame, trait_cols)
                if "create_umap_colored_by_top_traits" in keys_to_generate
                else None
            )
            calls = _umap_plot_calls(
                result_dict,
                frame,
                trait_cols,
                plot_cmap=params.plot_cmap,
                plot_point_size=params.plot_point_size,
                plot_alpha=params.plot_alpha,
                top_traits_pca_result_dict=top_traits_pca_result_dict,
            )
            generate_figures(
                {k: calls[k] for k in keys_to_generate},
                figures,
                font_family=params.plot_font_family,
                font_size=params.plot_font_size,
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
        output_links=stored.output_links,
    )
