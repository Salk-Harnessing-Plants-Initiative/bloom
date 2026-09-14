## Why

#713 (`add-bloommcp-plot-snapshot-tests`) added pixel-diff regression testing for the
dedicated plotting tools but deliberately scoped out the optional plot keys
`pca_analysis`/`umap_analysis`/`clustering` emit under `include_plots=True`, tracking them
as **#723** rather than dropping them silently. That follow-up is this change.

#723 describes the existing coverage as "`.is_file()`-style checks". The real shape is
different — and slightly stronger, not weaker: there is **no `.is_file()` check on any of
these keys**. What the three tool test files assert is a 4-byte PNG magic-number check,
which proves a little more than mere existence and still nothing about content:

```python
assert staged[key][:4] == b"\x89PNG", f"{key} is not a valid PNG"
```

Nothing reads past byte 4. Every pixel of all 8 figures is untested: a matplotlib/Pillow/
numpy bump, a `sleap-roots-analyze` delegate upgrade, or a refactor that changes color
mapping, axes geometry, or label layout would ship with every existing assertion green.
The live-smoke tests do not close this either — `test_pca_analysis_smoke.py` asserts an
`outputs` set that excludes `.png` keys entirely, so `include_plots` is never exercised
against the real stack.

## What Changes

- **ADD** 6 committed baseline PNGs under `bloommcp/tests/fixtures/plot_baselines/`, named
  `<catalog_key>_turface_19_baseline.png`. All 8 keys are rendered and commit-checked, but
  the 2 `umap_analysis` keys carry no baseline: CI measured their canvas size as
  platform-dependent (design.md Decision 3 records the evidence), and no tolerance can
  absorb a dimension mismatch. They keep the magic-byte coverage they had — so the baseline name
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
  other UMAP plot key it exposes" — though the issue contradicts itself, its Proposal
  section saying "these **7** additional plot keys". This change follows the enumeration
  clause, and Scenario 2 of the spec makes the set self-tracking off the catalogs so the
  count cannot drift again. `create_umap_single_trait` is also the sole consumer of the
  `plot_cmap` / `plot_point_size` fields (#662/#721) — `plot_alpha` is **not** exclusive to
  it, since `pca_analysis` forwards its own `plot_alpha` to `create_pca_biplot` — so leaving
  it out would mean the plot with the largest user-controllable style surface stays
  unsnapshotted.
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
  tools: `pca_analysis.py:422` and `umap_analysis.py:649` iterate `list(frozenset)` where
  `clustering.py:644` and `heritability_analysis.py:594` use `sorted(...)` — a 2-of-4
  divergence across the plot-emitting family, not a two-tool outlier. It cannot affect pixel
  content (the tests address figures by filename, never by `outputs` order), so it is out of
  scope for a testing change. **No issue exists for it yet; one should be filed.**
- Not restoring pixel coverage for `heritability_analysis`'s two folded-in figures (the
  other gap `tests/fixtures/README.md` records — it promises this is "a follow-up, not a
  silent loss"). Different fixture shape, and outside what #723 asks for. **No issue exists
  for it yet either; one should be filed.**

## Impact

- **Affected specs**: `bloommcp-plot-snapshot-testing` (ADDED only — the capability is still
  in-flight under #713 and not yet in `openspec/specs/`, so these are additive requirements
  alongside its three, not modifications to them).
- **Affected code**:
  - `bloommcp/tests/fixtures/plot_baselines/` — 8 new PNGs (~581 KB total, largest 214 KB;
    each under the 500 KB `check-added-large-files` pre-commit limit).
  - `bloommcp/tests/fixtures/plot_baselines/MANIFEST.json` — **extended**. It currently
    records only matplotlib/Pillow/sleap-roots-analyze/platform/python, which omits most of
    what these 8 renders actually depend on: `umap-learn`, `numba`, `llvmlite` (both UMAP
    baselines), `scikit-learn` (PCA solver and `svd_flip`'s sign convention), `seaborn` (the
    heatmap), `adjustText` (the biplot's label solver, whose *absence* is swallowed by a bare
    `except ImportError: pass` and silently changes layout), `numpy`, and matplotlib's
    bundled FreeType version — the single biggest driver of the cross-platform text-extent
    risk Decision 3 is about. It also gains the fixture's SHA-256 and the resolved seeds.
  - `openspec/changes/add-bloommcp-plot-snapshot-tests/specs/…/spec.md` — corrected. That
    delta still says "the 5 Plotting Tools" and requires two baselines #462 deleted, and its
    regeneration command predates the `--frozen`/`--yes` flags. It is unarchived, so it would
    carry both falsehoods into `openspec/specs/`. PR #724 merged to staging on 2026-09-03, so
    the file is editable from this branch — and this is the next change to touch the
    capability, which makes it the cheapest moment to fix.
  - `bloommcp/scripts/gen_plot_snapshots_golden.py` — extended, not restructured.
  - `bloommcp/tests/tools/test_viz_snapshot.py` — new parametrization + 2 negative controls.
  - `bloommcp/tests/scripts/test_gen_plot_snapshots_golden.py` — cover the new render path.
  - `bloommcp/tests/fixtures/README.md` — documented section updated.
- **Affected CI**: none structurally — the new tests are unmarked and run inside
  `python-audit`'s existing sweep, as `bloommcp-plot-snapshot-testing` already requires. The
  `*-failed-diff.png` artifact upload added by #713 covers them with no change.
- **No new dependency.** `compare_images`, Pillow, and all 8 plotters are already resolvable;
  `umap-learn` arrives transitively via `sleap-roots-analyze`, unchanged by this proposal.
