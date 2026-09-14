## Why

#713 (`add-bloommcp-plot-snapshot-tests`) added pixel-diff regression testing for the
dedicated plotting tools but deliberately scoped out the optional plot keys
`pca_analysis`/`umap_analysis`/`clustering` emit under `include_plots=True`, tracking them
as **#723** rather than dropping them silently. That follow-up is this change.

The gap is worse than #723's own text states. #723 says these keys are "only asserted on
via `.is_file()`-style checks" — there is in fact **no `.is_file()` check on any of them**.
What the three tool test files assert is a 4-byte PNG magic-number check:

```python
assert staged[key][:4] == b"\x89PNG", f"{key} is not a valid PNG"
```

Nothing reads past byte 4. Every pixel of all 8 figures is untested: a matplotlib/Pillow/
numpy bump, a `sleap-roots-analyze` delegate upgrade, or a refactor that changes color
mapping, axes geometry, or label layout would ship with every existing assertion green.

## What Changes

- **ADD** 8 committed baseline PNGs under `bloommcp/tests/fixtures/plot_baselines/`, one per
  catalog plot key, named `<catalog_key>_turface_19_baseline.png` — so the baseline name
  matches the filename the tool itself commits (`create_pca_scree_plot.png`), removing the
  need for the `_PRODUCED_NAME_OVERRIDES` indirection the existing 3 require.
- **EXTEND** `bloommcp/scripts/gen_plot_snapshots_golden.py` to render these 8 alongside the
  existing 3, reusing its `--yes` confirmation gate, its old-vs-new RMS reporting, and its
  `FakeReader`/`FakeResultStore` commit-spy capture unchanged. These three tools need
  `add_cleaned_version` (they are `require_clean=True` consumers) where the existing 3 use
  `add_experiment` — the one seeding difference.
- **EXTEND** `bloommcp/tests/tools/test_viz_snapshot.py` with a second parametrized
  comparison over the 8 keys at the **same** `_TOL = 15`, re-validated by measurement
  (design.md Decision 1) rather than assumed to carry over.
- **ADD** two negative-control tests pinning measured blind spots (design.md Decision 2),
  following the precedent `test_realistic_single_cell_defect_in_correlation_matrix_is_not_caught`
  set for #768: a limit that is measured and pinned fails loudly if it ever changes, in
  either direction, instead of being quietly assumed away.
- **UPDATE** `bloommcp/tests/fixtures/README.md` and `test_viz_snapshot.py`'s module
  docstring, whose "Scope" section currently names these keys as an explicit non-goal.

**Scope decisions** (confirmed with the issue author before scaffolding):

- **8 keys, not #723's 7.** `create_umap_single_trait` is a first-class catalog key
  (`_UMAP_CATALOG_KEYS`) that #723's list omits; its own text anticipates this with "and any
  other UMAP plot key it exposes". It is also the sole consumer of the `plot_cmap` /
  `plot_point_size` / `plot_alpha` fields (#662/#721), so leaving it out would mean the plot
  with the largest user-controllable style surface stays unsnapshotted.
- **`clustering` at its default `method="kmeans"` only** — 2 baselines, not 6. One baseline
  per catalog key matches the per-key scope #723 itself counts in. `gmm`/`hierarchical`
  rendering keeps its existing key-set and magic-byte coverage.

## Non-goals

- Not covering `gmm`/`hierarchical` clustering renders, or any non-default parameterization
  (`n_clusters`, `trait_columns`, the style kwargs). One baseline per key at tool defaults.
- Not changing `_TOL`, the comparison mechanism, the regeneration gate, or anything else
  about the #713 harness. This change extends its coverage; it does not redesign it.
- Not closing the dilution blind spots this change measures (design.md Decision 2). They are
  documented and pinned, not fixed — closing them needs per-region or numeric comparison,
  the same heavier-weight machinery design.md Decision 7 of #713 already argued against, and
  the same structural limit #768 tracks.
- Not fixing the unrelated `outputs`-ordering non-determinism found while surveying these
  tools (`list(frozenset)` in `pca_analysis.py:422` / `umap_analysis.py:649`, where
  `clustering.py:644` sorts). It does not affect pixel content — only key insertion order —
  so it is out of scope here; filed separately rather than fixed in a testing change.
- Not restoring pixel coverage for `heritability_analysis`'s two folded-in figures (the
  other gap `tests/fixtures/README.md` records). Different fixture shape, different issue.

## Impact

- **Affected specs**: `bloommcp-plot-snapshot-testing` (ADDED only — the capability is still
  in-flight under #713 and not yet in `openspec/specs/`, so these are additive requirements
  alongside its three, not modifications to them).
- **Affected code**:
  - `bloommcp/tests/fixtures/plot_baselines/` — 8 new PNGs (~581 KB total, largest 214 KB;
    each under the 500 KB `check-added-large-files` pre-commit limit). `MANIFEST.json`
    records environment provenance only and lists no filenames, so it needs no edit beyond
    the regeneration stamp.
  - `bloommcp/scripts/gen_plot_snapshots_golden.py` — extended, not restructured.
  - `bloommcp/tests/tools/test_viz_snapshot.py` — new parametrization + 2 negative controls.
  - `bloommcp/tests/scripts/test_gen_plot_snapshots_golden.py` — cover the new render path.
  - `bloommcp/tests/fixtures/README.md` — documented section updated.
- **Affected CI**: none structurally — the new tests are unmarked and run inside
  `python-audit`'s existing sweep, as `bloommcp-plot-snapshot-testing` already requires. The
  `*-failed-diff.png` artifact upload added by #713 covers them with no change.
- **No new dependency.** `compare_images`, Pillow, and all 8 plotters are already resolvable;
  `umap-learn` arrives transitively via `sleap-roots-analyze`, unchanged by this proposal.
