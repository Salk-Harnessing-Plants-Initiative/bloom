## Why

#768. `bloommcp/tests/tools/test_viz_snapshot.py`'s whole-image RMS pixel-diff (`_TOL = 15`)
structurally cannot catch a single-cell defect in `plot_correlation_matrix` — the tool where
a silently wrong value is most scientifically consequential, because a researcher reads a
correlation off the image and acts on it. RMS is a whole-image average, so one cell out of
55 is diluted to a signal the same order of magnitude as ordinary cross-platform FreeType
noise (~5-12 RMS). No `_TOL` separates them. `test_realistic_single_cell_defect_in_
correlation_matrix_is_not_caught` currently pins this as an honest negative result.

**Re-measuring for this change found the gap is materially worse than #768 documented.**
#768 measured only an opaque-orange synthetic recolor (RMS≈5.2) and reasoned that a realistic
wrong-but-plausible value — one that shifts *within* `coolwarm`'s range — would score lower
still. Measured directly, and confirmed by two independent methods (see design.md Decision 2),
even the **most extreme wrong value the colormap can express** — the strongest real pair in
`turface_19` (Surface Area × Network Area, r=0.9942) flipped clean across the scale to
r=−0.306 — scores **RMS ≈11.3, still under `_TOL=15`**. Not "slips through under bad luck":
there is no single-cell correlation error in this heatmap, of any magnitude, that the current
suite catches.

## What Changes

- **ADD** `bloommcp/tests/tools/test_viz_cell_oracle.py` — a per-cell *structural* check on
  the correlation heatmap, the first of the two paths #768 names ("extract and compare each
  heatmap cell individually"), asserting each drawn cell against a `df[trait_cols].corr()`
  recomputed in the test rather than against a whole-image statistic:
  - the `QuadMesh`'s drawn-cell set equals `np.triu(ones_like, dtype=bool) | ~np.isfinite(corr)`
    — the caller's upper-triangle-and-diagonal mask **combined with the delegate's own
    `masked_invalid` pass** — and its unmasked values equal the recomputed matrix. Stating the
    mask this way rather than as a flat "55 lower-triangle cells" is what makes the check
    correct on a fixture with a zero-variance or low-overlap trait, where the delegate draws
    fewer cells (measured: forcing one constant trait into `turface_19` drops 55 drawn cells
    and 55 annotations to 45 of each);
  - the colormap normalization's `vmin`/`vmax` equal the min/max of exactly the drawn values —
    so a *global* rescale fails loudly too, not just a per-cell one;
  - every annotation equals `f"{expected:.2f}"` **at its own grid position** — this is the
    literal number a researcher reads off the plot — with the count pinned exactly, so a value
    drawn in the wrong cell fails as well as a wrong value;
  - every drawn cell's facecolor equals `cmap(norm(expected_value))`;
  - cells outside the drawn set are painted as nothing (RGBA `0,0,0,0`), with both the drawn
    set and its complement asserted non-empty so nothing passes vacuously;
  - both axes' tick labels equal `resolved_trait_columns` in order — the unstated premise the
    tool's own `heatmap_caveat` relies on when it tells a PNG-only viewer to "match these names
    against the image's own axis labels";
  - and, closing the loop to the actual bytes, **each drawn cell's pixels in the saved PNG**
    match the color its own correlation value implies (median-sampled; worst legitimate
    disagreement measured at 0.00196 across all 55 cells, checked at `atol=0.01`).
- **ADD** the negative control that is the entire point of #768:
  `test_single_cell_defect_rms_misses_but_cell_oracle_catches` makes the delegate render a
  **doctored correlation matrix** — one cell shifted, via a patched `DataFrame.corr` around the
  delegate call — and asserts in one test that whole-image RMS at `_TOL=15` does **not** flag
  it while the per-cell oracle does, at each of the array, annotation and facecolor layers
  separately. Doctoring the matrix rather than recoloring the saved PNG is what makes this a
  real control: a post-hoc pixel edit is invisible to every artist-state assertion and would
  exercise only the saved-PNG layer, leaving most of the new suite negatively uncontrolled.
- **ADD** a directly-exercised test of the annotation precondition. The delegate stops drawing
  annotations *above* `annot_threshold=50` (`show_annot = n_traits <= annot_threshold`), which
  the committed 11-trait fixture can never reach, so the guard is tested against a synthetic
  count and the threshold itself is read from the delegate's own parameter default rather than
  duplicated as a literal.
- **MOVE** `_render_to_dir` and `_TOL` out of `test_viz_snapshot.py` into
  `tests/tools/conftest.py`, so the new file can tie its subject to the tool's really-committed
  PNG and assert against the snapshot suite's real tolerance without a second copy of either
  drifting from the first. Exactly the precedent `add-bloommcp-plot-snapshot-tests`' Decision 6
  set when it moved `viz_env` there for the same reason. No behavior change to either file's
  existing tests.
- **UPDATE** the prose that will otherwise still tell a reader #768 is open:
  - `test_viz_snapshot.py`'s module docstring "Known limitation" section and
    `test_realistic_single_cell_defect_in_correlation_matrix_is_not_caught`'s docstring — the
    RMS layer's blind spot is still real and that pin still belongs there, but it must now say
    the gap is *covered by the cell oracle*, not that it is unaddressed;
  - `plot_correlation_matrix.py`'s "Known test-coverage gap (#768...)" paragraph, for the same
    reason — it currently reads as an open, compounding disclosure alongside #747;
  - `openspec/changes/add-bloommcp-plot-snapshot-tests/design.md`'s Decision 7, titled "why
    #768 … is tracked, not fixed here". That change is still unarchived and
    `plot_correlation_matrix.py` points readers directly at it, so leaving it alone would have
    the production docstring cite a live design document that contradicts it.

## Non-goals

- **Not lowering or per-plot-splitting `_TOL`.** `add-bloommcp-plot-snapshot-tests`' Decision 7
  already measured why that cannot work (a per-plot `_TOL` would need to drop to ~3-4, inside
  the noise floor a 2%-dim scores). This change adds a layer beside RMS; it does not retune it.
- **Not extending the oracle to the other plots, or to `pca_analysis`/`umap_analysis`/
  `clustering`'s optional plot keys.** Breadth is #723, deliberately kept separate — see the
  sequencing note in design.md Decision 7.
- **Not covering the >50-trait render.** Above `annot_threshold` the delegate draws no
  annotations, so that layer of the oracle does not apply to a cylinder-scale (615/846-trait)
  selection; the array/norm/facecolor layers still would. A 60-inch 846-trait figure is not
  something to add to an unmarked per-PR sweep. Disclosed and guarded, not silently assumed away.
- **Not fixing #747** (the delegate's unguarded `.corr()` colors low-overlap cells as confident
  ±1.0). This change asserts the image faithfully draws the matrix it was given; whether that
  matrix should have masked a low-overlap pair is a separate, still-open concern. The oracle
  deliberately compares against the delegate's own unguarded `.corr()` for that reason — see
  design.md Decision 4.
- **Not correcting the sibling change's own stale requirement.** `add-bloommcp-plot-snapshot-
  tests`' spec still requires 5 baseline PNGs for 5 plotting tools; bloom#462 retired two of
  those tools and the repo now has 3 baselines, so archiving that change unamended would write
  a knowingly-false requirement into `openspec/specs/`. Flagged here because this change is the
  only other thing touching that capability, but fixing it belongs to that change, not this one.

## Impact

- **Affected specs**: `bloommcp-plot-snapshot-testing` (ADDED requirements only — a per-cell
  structural oracle is orthogonal to, not a modification of, the existing whole-image RMS
  requirements).
- **Sequencing / archive order**: the `bloommcp-plot-snapshot-testing` capability does not yet
  exist under `openspec/specs/` — it is still a pending delta in the unarchived
  `add-bloommcp-plot-snapshot-tests`. Both changes are ADDED-only and their requirement headers
  are disjoint, so `openspec archive` succeeds in either order and there is no name collision.
  ADDED (not MODIFIED) is therefore the only authorable operation here: a MODIFIED delta has no
  base requirement to copy from and would hard-fail if this change archived first. The
  *implementation* does depend on the sibling having landed — the new file imports `_TOL` and
  `_render_to_dir` from the conftest and compares against its committed baseline — which it has,
  via PR #724 on staging.
- **Affected code**:
  - `bloommcp/tests/tools/test_viz_cell_oracle.py` — new.
  - `bloommcp/tests/tools/conftest.py` — gains the moved `_render_to_dir` and `_TOL`.
  - `bloommcp/tests/tools/test_viz_snapshot.py` — those two moved out (now imported from
    conftest); two docstrings updated. No test behavior change.
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/plot_correlation_matrix.py` — one
    docstring paragraph updated. **No production-code change.**
  - `openspec/changes/add-bloommcp-plot-snapshot-tests/design.md` — Decision 7 updated to point
    at the change that closed what it tracked.
- **Affected CI**: none structurally — the new tests are unmarked and run inside the existing
  `python-audit` job's `pytest tests/ -m "not integration and not live_smoke"` sweep, like the
  rest of the snapshot suite. The `tol=0` assertion is the strictest in the suite and has never
  run on `ubuntu-latest`, so the PR's own CI run is its first real cross-platform test — same
  posture, and same fallback, as the sibling change's Decision 3.
- **No new dependency**: numpy/pandas/matplotlib/Pillow/seaborn are all already resolved.
