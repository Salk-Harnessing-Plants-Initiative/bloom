## Context

`clustering.py` is the first **polymorphic** analysis tool: it dispatches on `method` (`kmeans`
/ `gmm` / `hierarchical`) to three different upstream entry points, whose raw `result_dict`
shapes are *not* uniform — notably, `hierarchical_cluster_labels` (the composed entry point
`clustering.py` calls for `method="hierarchical"`) does not return `data_processed` the way
`perform_kmeans_clustering`/`perform_gmm_clustering` do (verified directly against the
installed `sleap_roots_analyze==0.1.0a5`). Six plotter signatures in
`sleap_roots_analyze.cluster_visualization` were checked against what each method's
`result_dict` actually carries.

## Goals / Non-Goals

- Goals: optional clustering plots, uniform across all three methods with no per-method
  branching in the catalog, zero impact on default callers, reuse `_plots.py` verbatim (no
  changes), no new dependency, no breaking change.
- Non-Goals: `create_silhouette_plot`, `create_bic_aic_comparison_plot`, `create_dendrogram`,
  `create_distance_distribution_plot` (see Decisions below for why each is excluded); style
  kwargs (`plot_alpha`/`plot_cmap`/`plot_point_size`) — neither included plotter's signature
  accepts them.

## Decisions

### Included: `create_cluster_scatter_pca`, computed via an internal non-persisted PCA call

`create_cluster_scatter_pca(cluster_result, pca_result=None, ...)` accepts an **optional**
`pca_result` dict. When omitted, it falls back to `cluster_result["data_processed"]` — but that
key is absent from the `hierarchical` method's `result_dict` (`hierarchical_cluster_labels`
strips it; only `perform_kmeans_clustering`/`perform_gmm_clustering` return it). Passing our own
`pca_result`, computed via an internal, non-persisted `perform_pca_analysis(frame.df[trait_cols])`
call, sidesteps that gap entirely and makes the catalog key work identically for all three
methods. This mirrors `umap_analysis`'s `create_umap_colored_by_top_traits` precedent (its
design.md Decision #3: an internal non-persisted PCA call, never committed as its own versioned
run, using the exact same certified-clean trait selection already validated for the primary fit).

The function still reads `cluster_result["cluster_labels"]` and `cluster_result["data_indices"]`
directly from the raw dict (unconditionally, regardless of whether `pca_result` is passed) — both
keys **are** present across all three methods' raw `result_dict`s (verified against
`perform_kmeans_clustering`, `perform_gmm_clustering`, and `hierarchical_cluster_labels`), so no
further gap exists there.

The call forwards `standardize=params.standardize` — the same flag the primary clustering fit
was given — rather than the delegate's own default. Passing the default here instead would
silently compute the projection in a different coordinate space than the one actually
clustered, so the plot's geometry would disagree with the real fit (caught in PR review; see
tasks.md § 4).

If the internal PCA call fails (e.g. the certified-clean selection is degenerate for PCA even
though it fit the requested clustering method), it is translated to `assumption_violated`.
**Revised in PR review**: the exception tuple is `(ValueError, numpy.linalg.LinAlgError)` — not
`umap_analysis`'s wider `(ValueError, KeyError, RuntimeError, TypeError)`, which `_top_traits`
justifies by *matching its own module's primary UMAP delegate call* (a symmetry argument that
doesn't apply here, since this module's primary clustering delegates are caught with
`(ValueError, RuntimeError)`, not that 4-tuple). `perform_pca_analysis`'s own docstring
documents only `ValueError`; `pca_analysis.py`'s own call to the same function catches only
`ValueError` too. `numpy.linalg.LinAlgError` is added on top as the one genuinely reachable
non-`ValueError` failure mode (a singular matrix during eigendecomposition) that the narrower,
derived-from-the-wrapped-function tuple would otherwise miss. This translation happens before
`create_run`, so no run is committed. A caller who does not want this failure mode can omit
`create_cluster_scatter_pca` from `plots`. A `logger.debug(...)` call precedes the translation
(mirroring `umap_analysis`'s identical path) so a genuine upstream failure is distinguishable
server-side from routine degenerate input — the original draft omitted this (module had no
logger at all); also caught in PR review.

The plotter itself degrades gracefully on a 1-feature selection (`X_pca.shape[1] < 2` renders an
explanatory placeholder figure rather than raising) — no additional guard needed in bloommcp for
that case.

### Included: `create_cluster_size_barplot`

`create_cluster_size_barplot(cluster_labels, n_clusters, figsize)` takes only the already-typed
`result.cluster_labels` / `result.n_clusters` as direct arguments — no `result_dict` shape
dependency at all, so it is trivially uniform across all three methods with zero extra
computation. Included as the second catalog key precisely because it introduces no additional
risk or plumbing.

### Excluded: `create_silhouette_plot`

Requires `cluster_result["data_processed"]` **unconditionally inside the function body** (no
override parameter, unlike `create_cluster_scatter_pca`'s optional `pca_result`) — so it cannot
be made to work for `hierarchical` without either (a) modifying `_labels_frame`/`result_dict`
construction to smuggle in a standardized array bloommcp does not otherwise retain for
hierarchical, duplicating the delegate's own internal standardization, or (b) monkey-patching the
dict before the call. Both cross into "vendored computation logic" the tool's docstring
explicitly disclaims. Excluded from this change; would need a follow-up upstream change (e.g. an
optional override parameter on the plotter itself) to include uniformly.

### Excluded: `create_bic_aic_comparison_plot`

Only meaningful for `method="gmm"` (needs `bic_scores`/`aic_scores`, which do not exist for
kmeans/hierarchical), and even for GMM its interpretation is degenerate on the **fixed**
`n_components` path (`bic_scores`/`aic_scores` become single-element lists — see
`_gmm_selected_scores`'s existing upstream-bug workaround for the same asymmetry). Adding a
GMM-only catalog key would require per-method catalog/validation branching this change
deliberately avoids to keep the catalog uniform; tracked as a possible GMM-specific follow-up,
not bundled here.

### Excluded: `create_dendrogram`

Only meaningful for `method="hierarchical"` (needs `linkage_matrix`, which `kmeans`/`gmm` never
produce), and `hierarchical_cluster_labels` — the composed entry point `clustering.py` actually
calls — does not return `linkage_matrix` either (only the raw, uncomposed
`perform_hierarchical_clustering` does, which `clustering.py` does not call directly). Same
per-method-branching and missing-key concerns as `create_bic_aic_comparison_plot`; excluded for
the same reason.

### Excluded: `create_distance_distribution_plot`

Takes raw `distances`/`threshold` arrays intended for outlier-detection visualization (K-Means
distances-to-center, GMM log-likelihoods vs. a cutoff) — not a clustering-quality visualization,
and no threshold concept exists in `clustering`'s own params. Out of scope for this change
regardless of method.

### `result_dict` retained in scope

Mirrors `pca_analysis`: `result_dict` (the raw dict from whichever delegate ran) must stay in
local scope after being wrapped into the typed `KMeansResult`/`GMMResult`/`ClusterResult`, since
`create_cluster_scatter_pca` and `create_cluster_size_barplot` need it (or a raw array from it)
directly.

### `_plots.py` protocol: zero-arg callables (no changes to that module)

Identical protocol to `pca_analysis`/`umap_analysis`: `_clustering_plot_calls(result_dict, result,
frame, trait_cols)` returns `dict[str, Callable[[], Figure]]`; `_plots.py`'s
`validate_plot_keys`/`generate_figures`/`close_figures` are reused verbatim, exactly as
`umap_analysis` reused them from the PCA-plots change with zero modification.

```python
# In clustering.py (not _plots.py):
def _clustering_plot_calls(result_dict, result, frame, trait_cols):
    from sleap_roots_analyze import (
        create_cluster_scatter_pca,
        create_cluster_size_barplot,
        perform_pca_analysis,
    )

    def _scatter_pca():
        try:
            pca_result_dict = perform_pca_analysis(frame.df[trait_cols])
        except (ValueError, KeyError, RuntimeError, TypeError):
            raise BloomMCPError(
                code="assumption_violated",
                message=(
                    "Could not compute the PCA projection for "
                    "create_cluster_scatter_pca — the certified-clean trait selection "
                    "is degenerate for PCA."
                ),
                remedy=(
                    "Select a broader set of numeric trait columns, or omit "
                    "create_cluster_scatter_pca from plots, then retry."
                ),
            ) from None
        return create_cluster_scatter_pca(result_dict, pca_result=pca_result_dict)

    return {
        "create_cluster_scatter_pca": _scatter_pca,
        "create_cluster_size_barplot": lambda: create_cluster_size_barplot(
            np.asarray(result_dict["cluster_labels"]), int(result.n_clusters)
        ),
    }
```

### Figure/tempdir nesting order

`clustering.py`'s persistence block is `with tempfile.TemporaryDirectory(prefix=
"clustering_input_") as _tmp:` (unlike `pca_analysis`'s `snapshot_frame` context manager — a
different helper, same shape). The `try/finally` for figure cleanup wraps this block identically
to `pca_analysis`'s nesting:

```python
figures: dict = {}
try:
    if params.include_plots:
        import matplotlib
        matplotlib.use("Agg")
        validate_plot_keys(params.plots, _CLUSTERING_CATALOG_KEYS)
        calls = _clustering_plot_calls(result_dict, result, frame, trait_cols)
        keys_to_generate = (
            list(params.plots) if params.plots is not None else list(_CLUSTERING_CATALOG_KEYS)
        )
        generate_figures({k: calls[k] for k in keys_to_generate}, figures)  # before create_run

    with tempfile.TemporaryDirectory(prefix="clustering_input_") as _tmp:
        source_snapshot = Path(_tmp) / _INPUT_SNAPSHOT_NAME
        frame.df.to_csv(source_snapshot, index=False)
        run = store.create_run(...)
        _labels_frame(result, frame).to_csv(run.staging_dir / _LABELS_NAME, index=False)
        (run.staging_dir / _RESULT_NAME).write_text(result.to_json())
        outputs = {_LABELS_NAME: _LABELS_NAME, _RESULT_NAME: _RESULT_NAME}
        for name, fig in figures.items():
            fig.savefig(run.staging_dir / f"{name}.png", bbox_inches="tight")
            outputs[f"{name}.png"] = f"{name}.png"
        stored = store.commit(run, outputs)
finally:
    close_figures(figures)
```

`validate_plot_keys` fires before `create_run`, so an unknown key never commits a run. Figure
generation happens before the tempdir/persistence block so a plot failure never leaves an
orphaned staging dir.

### `plots=[]` and `include_plots=False` with `plots` provided

Identical rule to `pca_analysis`/`umap_analysis`:
- `plots=[]` with `include_plots=True` → rejected as `invalid_input`.
- `plots=[...]` with `include_plots=False` → silently ignored, no error.

### Plots merged into `outputs`, no new result field

`ClusteringResult.outputs` already exists as `dict[str, str]`; plots append additional keys to
it (e.g. `"create_cluster_scatter_pca.png" → object_key`) — no new `plot_links` field, keeping
it structurally symmetric with `PCAAnalysisResult`/`UMAPAnalysisResult`.

### Lazy matplotlib import (no fresh Tier-0 guarantee — corrected framing)

`import matplotlib; matplotlib.use("Agg")` stays inside the `if params.include_plots:` branch.
**Verified directly** (not assumed): `clustering.py`'s existing top-level import — `from
sleap_roots_analyze import ClusterResult, GMMResult, KMeansResult, hierarchical_cluster_labels,
perform_gmm_clustering, perform_kmeans_clustering` — already puts `matplotlib`/
`matplotlib.pyplot` in `sys.modules` today, on `main`, with no change from this proposal:
importing any symbol from the `sleap_roots_analyze` package executes its `__init__.py`, which
imports `cluster_visualization` (among others), which does `import matplotlib.pyplot as plt` at
module level. This is the *same* situation `umap_analysis`'s docstring already documents for
itself ("this module's own top-level `sleap_roots_analyze` import already pulls in matplotlib
transitively... the same as `pca_analysis`/`clustering`") — it is not new to this change, and an
earlier draft of this design doc incorrectly claimed the opposite for `clustering.py`.
Consequently, `include_plots=False` does **not** give a fresh "matplotlib never touches
`sys.modules`" guarantee (none of the three sibling tools actually have one). What the lazy
placement *does* still guarantee, and what the test actually checks: the `include_plots=False`
code path never itself **executes** an `import matplotlib` statement — mirrors
`umap_analysis`'s corrected test, renamed from the PCA precedent's
`test_matplotlib_not_imported_on_default_path` to
`test_default_path_never_executes_an_import_matplotlib_statement` for the same reason.

## Risks / Trade-offs

- **Plotter API drift**: both call sites use keyword args; if `sleap_roots_analyze` renames a
  param, the tool fails loudly at call time — no silent misbehavior.
- **Internal PCA call cost**: see the "Included: `create_cluster_scatter_pca`" Decision above —
  a second (non-persisted) PCA fit runs alongside the primary clustering fit whenever this key
  is requested. Acceptable at this tool's scale (same trade-off `umap_analysis` already accepts).
- **Narrower catalog than PCA's four / UMAP's two-of-which-both-differ**: only two keys, both
  method-agnostic. A future change could add method-specific keys (GMM's BIC/AIC comparison,
  hierarchical's dendrogram) behind method-conditional validation if requested; deliberately not
  bundled here to keep this change's validation logic uniform across methods.

## Open Questions

None — both included plotters were verified importable and functionally sufficient against the
already-pinned `sleap-roots-analyze==0.1.0a5` with no upstream changes needed.
