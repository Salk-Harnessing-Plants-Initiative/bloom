"""plot_correlation_matrix — correlation heatmap for trait relationships (#466).

Converged onto the ``@as_mcp_tool`` contract every other tool in this folder uses (Pydantic
I/O, structured ``BloomMCPError``, one stamped ``Provenance``, versioned ``ResultStore``
persistence) — the same read-only, pre-clean EDA pattern as ``qc_inspect``: reads the raw
frame via the :class:`ExperimentReader` port (no ``require_clean``), since a correlation view
is exactly what an agent uses *before* deciding ``qc_clean``'s thresholds.

Delegates the heatmap's actual rendering (the colored grid itself) to
``sleap_roots_analyze.visualization.create_correlation_heatmap``; this file owns no *plotting*
logic of its own — it does not compute or draw anything resembling a chart element. It does,
however, call ``Figure.text(...)`` directly on the delegate's returned ``Figure`` to draw the
``heatmap_caveat`` disclosure footnote (#466 review round 6 — "delegates rendering" should not
be read as "never touches the returned Figure object"; see the masking-mismatch paragraph
below for why that footnote exists and is tested separately from the delegate's own render).
The reported strong-correlation counts are a plain ``pandas`` summary of the same selection,
computed directly here (not delegated) — unchanged from the tool's pre-conversion behavior.

**Zero-variance / all-NaN traits are excluded from the strong-correlation counts with no
error** — ``pandas``' Pearson correlation is ``NaN`` for a constant or all-NaN column, and
``NaN > 0.7`` is ``False``, so such a trait's pairs silently don't count toward either
``strong_positive_correlations``/``strong_negative_correlations``. Realistic here specifically
because this tool reads **raw, uncleaned** data (no QC has dropped a zero-variance trait yet —
see the raw-read decision below). ``zero_variance_traits`` in the result names exactly which
selected traits this affects, so the counts are not silently misleading.

**A pair with too few overlapping non-null observations is excluded the same way, via
``.corr(min_periods=...)``.** Raw, uncleaned data can have disjoint per-trait missingness, so
two traits can overlap in as few as 2 non-null rows — and 2 points are *always* perfectly
(anti)correlated, producing a spurious exact ±1.0 "strong correlation" from a near-empty
overlap. ``min_periods`` (reusing the same ``_qc_shared._CANONICAL_MIN_SAMPLES_PER_TRAIT``
threshold ``qc_clean``/``qc_inspect`` use for "enough samples to trust a trait") makes pandas
return ``NaN`` instead of a numerically valid but meaningless coefficient for any pair below
it, so it is excluded from the counts exactly like a zero-variance trait. ``low_overlap_trait_
pairs`` names exactly which pairs this affects (excluding any pair already explained by a
zero-variance trait, to avoid double-reporting the same ``NaN`` cell under two reasons).

**Known, narrow taxonomy gap (not fixed, disclosed):** a pair that is globally non-constant and
clears ``min_periods`` overlap can still be *locally* constant within that shared overlap (one
trait happens to take the same value on exactly the rows where both are non-null), producing a
``NaN`` cell named in neither ``zero_variance_traits`` nor ``low_overlap_trait_pairs``. Not a
false-positive risk — the vendored heatmap independently produces the same ``NaN`` — just an
incompleteness in *why* a given blank cell is blank. Deferred as out of scope for this pass.

**At least 2 resolved trait columns are required, and at least 2 of them must carry non-zero
variance.** A correlation view of a single trait is not meaningful (there is no pair to
correlate); nor is one where every-but-one (or every) trait is constant/all-NaN, since every
cell would then be ``NaN`` — both rejected as ``invalid_input``/``assumption_violated`` before
any run is persisted, rather than silently committing a degenerate or all-``NaN`` result.

**The rendered PNG is NOT masked the same way the summary is — this is a known, disclosed gap,
not a silent one.** ``strong_positive_correlations``/``strong_negative_correlations`` and the
``zero_variance_traits``/``low_overlap_trait_pairs`` disclosure fields above are all computed
from this tool's own *guarded* ``.corr(min_periods=...)`` call. The persisted image, however, is
rendered by a separate, independent call to the vendored ``create_correlation_heatmap``, which
runs its own **unguarded** ``.corr()`` with no ``min_periods`` and no way to accept a
precomputed matrix, so the cell itself would still render as a solid, confidently-colored ±1.0
square with no fix applied here — genuinely fixing the *coloring* would mean either patching the
vendored delegate (outside this package) or re-implementing heatmap rendering in bloommcp
(against this file's own no-vendored-plotting-logic principle); tracked at
https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/747. Two things ARE done here,
cheaply and in-scope, so a caller who only ever looks at the saved PNG — never the JSON — still
gets a signal: (1) a warning footnote is drawn directly onto the already-rendered ``Figure``
before ``savefig`` whenever either disclosure list is non-empty (#466 review round 4 — the
first version of this fix left the image itself untouched, a JSON-only disclosure a PNG-only
consumer would never see); (2) ``heatmap_caveat`` is also stamped into the persisted run's
``params`` (mirroring ``resolved_trait_columns`` below), not just the live response, so a later
manifest read gets the same signal a live call did; (3) the footnote names the actual flagged
trait(s)/pair(s) (capped at 10), not just a count (#466 review round 6 — a bare count told a
PNG-only viewer a problem existed with no way to tell *which* cell to distrust; since
``create_correlation_heatmap`` draws its axis tick labels from this same ``trait_cols`` list in
this same order, naming the flagged names directly lets that viewer cross-reference labels
they can already see on the image, with none of the "wrong cell" geometry risk a per-cell
hatch/marker would carry).

Persists a versioned run under its own tool class ``correlation_matrix`` (not the shared,
unclaimed legacy ``viz`` slot — see ``openspec/changes/converge-bloommcp-viz-tools/design.md``
for why each converged tool mints its own class rather than interleaving version history with
its siblings).
"""

from __future__ import annotations

from shutil import rmtree
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from sleap_roots_analyze.visualization import create_correlation_heatmap

from bloom_mcp.contract import BloomMCPError, Provenance, RunLinks, as_mcp_tool
from bloom_mcp.data_access import ExperimentReadError
from bloom_mcp.result_store import CommitFailedError, ManifestReadError
from bloom_mcp.tools import _ports
from bloom_mcp.tools._plots import FIGURE_REGISTRY_LOCK
from bloom_mcp.tools._qc_shared import (
    _CANONICAL_MIN_SAMPLES_PER_TRAIT,
    _validate_experiment_name,
)

from ._viz_shared import resolve_trait_columns

_TOOL_CLASS = "correlation_matrix"
_HEATMAP_PNG = "correlation_matrix.png"
# Reuses qc_clean/qc_inspect's "enough samples to trust a trait" convention as the minimum
# pairwise overlap .corr() requires before reporting a coefficient — below it, pandas returns
# NaN instead of a numerically valid but statistically meaningless value (see module docstring).
_MIN_CORR_OVERLAP = _CANONICAL_MIN_SAMPLES_PER_TRAIT


class PlotCorrelationMatrixParams(BaseModel):
    """Inputs for ``plot_correlation_matrix``. No ``seed`` — rendering is deterministic."""

    # extra="forbid": an unknown field isn't currently exploitable (it would be dropped
    # before persistence either way), but silently accepting it masks a caller typo
    # (#466 review round 5, matching the recommendation already made on sibling PR #726).
    model_config = ConfigDict(extra="forbid")

    experiment: str = Field(
        ..., description="Experiment identifier from list_available_experiments."
    )
    trait_columns: Optional[list[str]] = Field(
        default=None,
        description="Subset of trait columns to correlate; omit to use all detected traits. "
        "An explicit empty list is rejected rather than treated as 'all traits'.",
    )
    user_label: Optional[str] = Field(
        default=None,
        description="Optional slug appended to the version directory name.",
    )


class PlotCorrelationMatrixResult(RunLinks):
    """A small summary + links to the persisted correlation-heatmap run."""

    experiment: str
    source: str
    n_traits: int
    strong_positive_correlations: int = Field(
        description="Off-diagonal trait pairs with Pearson correlation > 0.7."
    )
    strong_negative_correlations: int = Field(
        description="Off-diagonal trait pairs with Pearson correlation < -0.7."
    )
    zero_variance_traits: list[str] = Field(
        default_factory=list,
        description="Selected traits with zero variance (constant) or entirely NaN in the "
        "raw data. Pearson correlation against a zero-variance trait is NaN, which counts "
        "toward neither strong_positive_correlations nor strong_negative_correlations — "
        "empty when none were affected.",
    )
    low_overlap_trait_pairs: list[list[str]] = Field(
        default_factory=list,
        description="Trait pairs whose overlapping non-null observations fell below the "
        "minimum this tool requires to report a correlation coefficient — raw data can have "
        "disjoint missingness, and a near-empty overlap (as few as 2 points) can otherwise "
        "produce a spurious exact +/-1.0 'strong correlation'. Excludes any pair already "
        "explained by zero_variance_traits. Empty when every pair had enough overlap.",
    )
    heatmap_caveat: Optional[str] = Field(
        default=None,
        description="Populated only when zero_variance_traits or low_overlap_trait_pairs is "
        "non-empty: some cell(s) in the rendered heatmap are not backed by enough real data to "
        "trust, but the image still colors them as if they were a genuine strong correlation. "
        "Names the affected trait(s)/pair(s) directly (capped at 10, '+N more' beyond that) so "
        "a PNG-only viewer can match them against the image's own axis labels — not just a "
        "count. The same text is also drawn as a footnote directly on the saved PNG and stamped "
        "into the persisted run's params. Cross-check zero_variance_traits/low_overlap_trait_"
        "pairs for the complete, uncapped list before trusting a highlighted cell in the image.",
    )
    resolved_trait_columns: list[str] = Field(
        description="The exact trait columns used to render/persist this run, in selection "
        "order — recorded even when trait_columns was omitted (auto-detected), so a later "
        "reader of this run's manifest can tell exactly which traits produced it without "
        "re-deriving auto-detection against data that may have drifted since.",
    )


@as_mcp_tool(
    input_model=PlotCorrelationMatrixParams,
    output_model=PlotCorrelationMatrixResult,
    errors=(ExperimentReadError, CommitFailedError, ManifestReadError),
)
def plot_correlation_matrix(
    params: PlotCorrelationMatrixParams, *, provenance: Provenance
) -> PlotCorrelationMatrixResult:
    """Render a correlation heatmap for ``experiment``'s **raw, uncleaned** data via
    ``create_correlation_heatmap`` and persist it. No QC cleaning has been applied — this is
    a pre-clean EDA view, the same category as ``qc_inspect``."""
    reader = _ports.reader()
    store = _ports.store()

    _validate_experiment_name(params.experiment)

    frame = reader.load_experiment(params.experiment, version="raw")
    trait_cols = resolve_trait_columns(frame, params.trait_columns, params.experiment)
    if len(trait_cols) < 2:
        raise BloomMCPError(
            code="invalid_input",
            message=f"plot_correlation_matrix requires at least 2 trait columns to "
            f"correlate; {params.experiment!r} resolved only {trait_cols!r}.",
            remedy="Select at least 2 trait columns, or omit trait_columns if the "
            "experiment has more than one detected trait.",
        )

    zero_variance_traits = [
        c for c in trait_cols if not (frame.df[c].std(skipna=True) > 0)
    ]
    zero_variance_set = set(zero_variance_traits)
    if len(trait_cols) - len(zero_variance_set) < 2:
        raise BloomMCPError(
            code="assumption_violated",
            message=f"plot_correlation_matrix requires at least 2 non-constant trait "
            f"columns; {params.experiment!r} resolved {trait_cols!r}, of which "
            f"{sorted(zero_variance_set)!r} are constant or entirely NaN.",
            remedy="Select at least 2 trait columns with non-zero variance, or use a "
            "different experiment.",
        )

    corr = frame.df[trait_cols].corr(min_periods=_MIN_CORR_OVERLAP)

    # Vectorized pairwise overlap counts (notna^T @ notna) — a python double loop over
    # trait_cols x trait_cols would be O(n^2) even just to build this, prohibitive at
    # cylinder's ~846-trait scale; only the (typically small) flagged-pair list below is.
    notna = frame.df[trait_cols].notna().to_numpy(dtype=int)
    overlap_counts = notna.T @ notna
    low_overlap_mask = np.triu(overlap_counts < _MIN_CORR_OVERLAP, k=1)
    low_overlap_trait_pairs = [
        [trait_cols[i], trait_cols[j]]
        for i, j in zip(*np.where(low_overlap_mask))
        if trait_cols[i] not in zero_variance_set
        and trait_cols[j] not in zero_variance_set
    ]

    upper = corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool))
    high_pos = int((upper > 0.7).sum().sum())
    high_neg = int((upper < -0.7).sum().sum())

    # Names the actual flagged trait(s)/pair(s), not just a count (#466 review round 6):
    # create_correlation_heatmap draws its axis tick labels from this same trait_cols list,
    # in this same order, so a PNG-only viewer can cross-reference a name here against a
    # label they can already see on the image — closing the "told a problem exists, no way
    # to tell which cell" gap a bare count left open. Capped so a wide (cylinder-scale)
    # selection with many flagged pairs doesn't produce an unreadably long footnote; the
    # full, uncapped lists are always in zero_variance_traits/low_overlap_trait_pairs.
    _flagged_names = list(zero_variance_traits) + [
        f"{a}×{b}" for a, b in low_overlap_trait_pairs
    ]
    _MAX_CAVEAT_NAMES = 10
    if _flagged_names:
        _shown = _flagged_names[:_MAX_CAVEAT_NAMES]
        _remainder = len(_flagged_names) - len(_shown)
        _names_text = ", ".join(_shown) + (
            f", +{_remainder} more" if _remainder else ""
        )
        heatmap_caveat = (
            f"Cell(s) involving {_names_text} have too little real data behind them "
            f"to trust as drawn — the image still colors them like a genuine strong "
            f"correlation. Match these names against the image's own axis labels; see "
            f"zero_variance_traits/low_overlap_trait_pairs for the complete list."
        )
    else:
        heatmap_caveat = None

    prov = provenance.model_copy(
        update={
            "based_on_version": frame.source,
            "params": {
                **provenance.params,
                "resolved_trait_columns": trait_cols,
                "heatmap_caveat": heatmap_caveat,
            },
        }
    )
    run = store.create_run(
        experiment=params.experiment,
        tool_class=_TOOL_CLASS,
        provenance=prov,
        user_label=params.user_label,
        source_csv=_ports.raw_source_for(params.experiment),
        source=frame.resolved_source,
    )
    fig = None
    try:
        # FIGURE_REGISTRY_LOCK: allocates a figure against the shared global matplotlib
        # registry, which a concurrent figure-creating call elsewhere in the process could
        # otherwise interleave with (see that lock's own comment in bloom_mcp.tools._plots).
        with FIGURE_REGISTRY_LOCK:
            fig = create_correlation_heatmap(frame.df, trait_cols)
        if heatmap_caveat is not None:
            # Cheap, in-scope: a footnote drawn directly onto the already-rendered Figure,
            # not a per-cell hatch/marker — the latter would require reverse-engineering the
            # vendored delegate's cell geometry (row/column orientation, any axis flip), and
            # getting that wrong would mislabel a DIFFERENT cell as flagged, which is worse
            # than no annotation. A caller who only ever opens the saved PNG (never the JSON
            # response) still gets the warning this way (#466 review round 4).
            fig.text(
                0.5,
                -0.02,
                f"⚠ {heatmap_caveat}",
                ha="center",
                va="top",
                fontsize=8,
                color="darkred",
                wrap=True,
                transform=fig.transFigure,
            )
        fig.savefig(run.staging_dir / _HEATMAP_PNG, dpi=150, bbox_inches="tight")
        stored = store.commit(run, {_HEATMAP_PNG: _HEATMAP_PNG})
    except Exception:
        rmtree(run.staging_dir, ignore_errors=True)
        raise
    finally:
        if fig is not None:
            plt.close(fig)

    return PlotCorrelationMatrixResult(
        experiment=params.experiment,
        source=frame.source,
        n_traits=len(trait_cols),
        strong_positive_correlations=high_pos,
        strong_negative_correlations=high_neg,
        zero_variance_traits=zero_variance_traits,
        low_overlap_trait_pairs=low_overlap_trait_pairs,
        heatmap_caveat=heatmap_caveat,
        resolved_trait_columns=trait_cols,
        run_ref=stored.run_ref,
        version_dir=stored.version_dir,
        manifest_path=stored.manifest_path,
        outputs=dict(stored.output_keys),
        output_links=stored.output_links,
    )
