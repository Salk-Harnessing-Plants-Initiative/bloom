## Context

`pca_analysis_tool.py`'s persistence block is a `with snapshot_frame(frame.df) as
source_snapshot:` context manager (the `_consumer_utils` seam shared with the other
consumer tools). Adding matplotlib figures introduces two new concerns: (a) figures
must be closed even when persistence fails, and (b) validation must fail before any
run is committed. Five plotter signatures were checked against the installed
`sleap_roots_analyze 0.1.0a4`; one plotter was excluded after its API was found incompatible
with available PCA outputs.

## Goals / Non-Goals

- Goals: optional PCA plots, zero impact on default callers, reusable `_plots.py`
  for UMAP (#425), no new dep, no breaking change
- Non-Goals: `create_variance_decomposition_plot` (heritability pipeline, separate change),
  signed URL generation (tracked by #388 Part 2), inline figure bytes in the result

## Decisions

### Excluded plotter: `create_variance_decomposition_plot`

`create_variance_decomposition_plot(comparison_df, ...)` requires a `comparison_df` produced
by `compare_trait_heritabilities()` — a Linear Mixed Model pipeline output that is not
derivable from `PCAResult` or `result_dict`. Including it would require running the
heritability pipeline as a side effect of `pca_analysis`, which violates the tool's single
responsibility. Excluded from this change; tracked separately.

### Heatmap `plot_type='loadings'`

`create_feature_contribution_heatmap` with the default `plot_type='both'` returns
`Tuple[Figure, Figure]`, not a single `Figure`. To keep the figure-dispatch loop simple and
the catalog uniform (one key → one PNG), the catalog fixes `plot_type='loadings'`. A
`'scores'`-only variant can be added later via a separate catalog key if needed.

### `result_dict` retained in scope

All four plotters take `pca_results: Dict` (the raw dict from `perform_pca_analysis`) as
their first argument — not a `PCAResult` instance. The current tool discards `result_dict`
after `PCAResult.from_pca_dict(result_dict, ...)`. The implementation must retain it as a
local variable for use on the plot path:

```python
result_dict = perform_pca_analysis(selected, ...)
pca = PCAResult.from_pca_dict(result_dict, explained_variance_threshold=...)
# result_dict remains in scope for plotters
```

### `_plots.py` protocol: zero-arg callables

`generate_figures` accepts `resolved_calls: dict[str, Callable[[], Figure]]` — a dict of
zero-arg callables, each wrapping one plotter call with its bespoke kwargs. The PCA-specific
dispatch (which plotter, which args drawn from `result_dict` / `pca` / `frame`) stays in
`pca_analysis_tool.py` as `_PCA_PLOT_CATALOG`:

```python
# In pca_analysis_tool.py (not _plots.py):
def _pca_plot_calls(result_dict, pca, frame, explained_variance_threshold):
    return {
        "create_pca_scree_plot": lambda: create_pca_scree_plot(
            result_dict, variance_threshold=explained_variance_threshold
        ),
        "create_pca_biplot": lambda: create_pca_biplot(
            result_dict, df=_biplot_df(frame), trait_names=list(pca.feature_names),
            color_by=frame.genotype_col,
        ),
        "create_feature_contribution_plot": lambda: create_feature_contribution_plot(
            result_dict, trait_names=list(pca.feature_names),
            n_components=pca.n_components,
            variance_threshold=explained_variance_threshold,
        ),
        "create_feature_contribution_heatmap": lambda: create_feature_contribution_heatmap(
            result_dict, n_components=pca.n_components,
            n_features=len(pca.feature_names), plot_type="loadings",
        ),
    }
```

`_plots.py` is then purely:

```python
def generate_figures(
    resolved_calls: dict[str, Callable[[], Figure]],
    figures: dict[str, Figure],
) -> None:
    """Populate ``figures`` one key at a time (not an all-or-nothing dict
    comprehension) so a mid-generation exception still leaves every
    already-successful figure in the caller's dict for `close_figures` to
    reach in `finally`."""
    for key, fn in resolved_calls.items():
        figures[key] = fn()
```

`generate_figures` takes the caller's `figures` dict and mutates it in place rather than
returning a new one — a dict comprehension would build its result only at the very end,
so an exception partway through would discard everything already generated, and the
caller's `finally: close_figures(figures)` would find nothing to close (see "Graceful
degradation on plotter failure" below).

UMAP (#425) builds its own `_umap_plot_calls(...)` dict and reuses the same
`validate_plot_keys` / `generate_figures` / `close_figures` without any changes to `_plots.py`.

### Figure/snapshot nesting order

The tool's persistence block is `with snapshot_frame(frame.df) as source_snapshot:`. The
`try/finally` for figure cleanup must wrap this block so figures are always closed even when
the snapshot or persistence fails. Correct nesting:

```python
figures: dict[str, Figure] = {}
try:
    if params.include_plots:
        import matplotlib
        matplotlib.use("Agg")
        validate_plot_keys(params.plots, _PCA_PLOT_CATALOG_KEYS)
        calls = _pca_plot_calls(result_dict, pca, frame, params.explained_variance_threshold)
        keys_to_generate = (
            list(params.plots) if params.plots is not None else list(_PCA_PLOT_CATALOG_KEYS)
        )
        generate_figures({k: calls[k] for k in keys_to_generate}, figures)  # before create_run

    with snapshot_frame(frame.df) as source_snapshot:
        run = store.create_run(...)
        # write loadings / scores / pca_result.json
        outputs = {...}  # the three data keys
        for name, fig in figures.items():
            fig.savefig(run.staging_dir / f"{name}.png", bbox_inches="tight")
            outputs[f"{name}.png"] = f"{name}.png"
        stored = store.commit(run, outputs)
finally:
    close_figures(figures)
```

`validate_plot_keys` fires before `create_run`, so an unknown key never commits a run.
`generate_figures` mutates the same `figures` dict `close_figures` later reads (see above) —
so figures are reachable in `finally` from the moment each one is generated, not just on a
fully successful run. The `with snapshot_frame(...)` block is nested inside the `try` so a
failure there cannot bypass `finally` either.

### Graceful degradation on plotter failure

If a plotter raises during `generate_figures`, the exception propagates out of the `try` block.
Because `generate_figures` populates the caller's `figures` dict incrementally (one key per
successful call, not a single all-or-nothing dict comprehension), every figure produced by an
earlier, successful plotter in the same call is already in `figures` when the exception fires —
so `finally: close_figures(figures)` actually closes it, rather than finding an empty dict. The
contract maps unhandled exceptions to `tool_error`. No run is committed (the exception fires
before `create_run`). No silent partial-plot persistence occurs.

### `plots=[]` and `include_plots=False` with `plots` provided

- `plots=[]` with `include_plots=True` → rejected as `invalid_input` (ambiguous: use
  `plots=None` for all, or `include_plots=False` for none).
- `plots=[...]` with `include_plots=False` → silently ignored. `plots` is a filter for the
  plots path; when that path is disabled, the filter has no effect. No error is raised to
  avoid surprising callers who have `plots` set in a workflow template.

### Plots merged into `outputs`, no new result field

`remove_outliers` merges plot PNGs into its existing `outputs: dict[str, str]` field.
`pca_analysis` follows the same pattern — plots appear as additional keys in `outputs` (e.g.,
`"create_pca_scree_plot.png" → object_key`). No new `plot_links` field is added, keeping the
two result models structurally symmetric.

### Lazy matplotlib import

`import matplotlib; matplotlib.use("Agg")` is placed inside the `if params.include_plots:`
branch (same as `remove_outliers`). The top-level module never imports `matplotlib` or
`matplotlib.pyplot`, preserving the Tier-0 import-clean guarantee.

## Risks / Trade-offs

- **Plotter API drift**: the four call sites use keyword args; if `sleap_roots_analyze`
  renames a param, the tool fails loudly at call time. Mitigation: the smoke test exercises
  at least one plot per run, so regressions surface in CI.
- **heatmap `plot_type='loadings'`**: the scores heatmap is not produced. Acceptable
  tradeoff; add a `create_feature_contribution_heatmap_scores` catalog key in a follow-up if
  needed.

## Open Questions

- Should `create_pca_biplot` receive `color_by=frame.genotype_col` (may be `None` → blue
  points) or always `color_by=None`? Decision: pass `frame.genotype_col` (`None` falls back
  to blue points — scientifically correct for genotype-less experiments). **Caveat found
  during review**: `create_pca_biplot`'s categorical-coloring check only recognizes
  `dtype == "object"` or `CategoricalDtype` — it does not recognize pandas's newer default
  `StringDtype` for string columns (pandas ≥ 2.x with the string-dtype default, e.g. 3.0.2),
  so passing the raw genotype column crashes with `ValueError: 'c' argument must be a
  color...` on that pandas version. Verified fix: `_biplot_df(frame)` casts a *copy* of
  `frame.df[frame.genotype_col]` to `pd.Categorical` before the call, which the plotter's
  check does recognize, restoring genotype-colored biplots without touching `frame.df`
  itself. The dtype-detection gap is upstream in `sleap_roots_analyze`, not bloommcp-specific
  — worth filing there so callers on newer pandas don't need this workaround.
