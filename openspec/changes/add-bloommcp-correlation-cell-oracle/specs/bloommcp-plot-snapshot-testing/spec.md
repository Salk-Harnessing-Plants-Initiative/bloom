## ADDED Requirements

### Requirement: Per-Cell Structural Verification Of The Correlation Heatmap

`bloommcp/tests/tools/test_viz_cell_oracle.py` SHALL verify every drawn cell of
`plot_correlation_matrix`'s rendered heatmap against a correlation matrix recomputed in the
test from the raw fixture, rather than against a whole-image statistic or a committed baseline
image. The oracle SHALL be the same unguarded `DataFrame.corr()` the rendering delegate itself
computes, so that this check reports "the heatmap drew the wrong value" and does not conflate
it with the separately-tracked question of whether a low-overlap cell should have been drawn at
all (#747).

The oracle is independent of the *rendering* path, not of the whole system: it recomputes the
correlation values and the drawn-cell set itself, but resolves trait columns through the same
production `experiment_utils.detect_columns` the tool uses, and maps an expected value to an
expected color through the rendered figure's own colormap. Those two couplings are covered from
the other side by the color-scale and axis-label scenarios below and by the whole-image RMS
layer; they are stated here so the requirement is not read as a stronger independence claim
than it makes.

#### Scenario: The drawn cell set is exactly the finite lower-triangle cells

- **WHEN** the correlation heatmap is rendered for the `turface_19` fixture
- **THEN** the `QuadMesh`'s mask equals `np.triu(ones_like, dtype=bool) | ~np.isfinite(corr)` —
  the caller's upper-triangle-and-diagonal mask combined with the delegate's own
  `masked_invalid` pass — so a cell that should carry a finite correlation but was left blank,
  and a cell that should be blank but was filled in, both fail
- **AND** the unmasked entries of the `QuadMesh`'s data array equal the test's recomputed
  `df[trait_cols].corr()` at the same positions

#### Scenario: Every drawn cell's annotation states that cell's own correlation value

- **WHEN** the rendered figure's annotation texts are read
- **THEN** there is exactly one annotation per drawn cell — no more, no fewer — and each one
  equals `f"{expected:.2f}"` for the recomputed correlation at **that annotation's own grid
  position**, so a correct value drawn in the wrong cell fails as well as a wrong value

#### Scenario: Every drawn cell is colored by its own value

- **WHEN** the rendered `QuadMesh`'s facecolors are read
- **THEN** each drawn cell's facecolor equals `cmap(norm(expected_value))` for that cell's
  recomputed correlation

#### Scenario: Cells outside the drawn set are not painted

- **WHEN** the facecolors of every cell outside the drawn set are read
- **THEN** every one of them is fully transparent RGBA `(0, 0, 0, 0)`
- **AND** the drawn set and its complement are both asserted non-empty, so neither this
  scenario nor the three above can pass vacuously on an empty selection

#### Scenario: The color scale spans exactly the drawn cells

- **WHEN** the heatmap's normalization is read
- **THEN** its `vmin` and `vmax` equal the minimum and maximum of exactly the recomputed values
  of the drawn cells — so a global rescale of the color mapping fails this check rather than
  shifting every cell's expected color along with it

#### Scenario: Axis labels are the resolved trait columns in order

- **WHEN** both axes' tick labels are read
- **THEN** they equal `resolved_trait_columns` in selection order, and the label list is
  asserted non-empty — this is the premise `plot_correlation_matrix`'s `heatmap_caveat` relies
  on when it directs a PNG-only viewer to match flagged trait names against the image's own
  axis labels

#### Scenario: The annotation precondition is pinned to the delegate, not assumed

- **WHEN** the resolved trait count is checked before the annotation assertions run
- **THEN** the threshold it is checked against is read from
  `create_correlation_heatmap`'s own `annot_threshold` parameter default rather than
  duplicated as a literal, and a trait count **strictly greater** than that threshold — the
  condition under which the delegate stops drawing annotations — fails with a message naming
  both numbers, rather than raising an incidental error from an empty annotation list
- **AND** that failure path is exercised directly by a test, not left asserted only by this
  scenario, since the committed fixture's trait count can never trigger it

### Requirement: Saved-PNG Cell Pixels Match Their Correlation Value

The per-cell verification SHALL extend to the persisted image, not stop at the in-memory
figure: each drawn cell's pixels in the saved PNG SHALL match the color that cell's own
correlation value implies, at a tolerance derived from a real measurement of the legitimate
anti-aliasing/quantization disagreement rather than a guessed constant.

This layer is retained even though the artist-state scenarios above already detect every defect
in the change's measured table, because it is the only per-cell coverage that does not read the
seaborn/matplotlib artist tree: an upgrade that restructures that tree breaks the scenarios
above at their assertions, and this requirement is what keeps per-cell coverage from lapsing
entirely in that window.

#### Scenario: Each drawn cell's saved pixels match its value

- **WHEN** the rendered figure is saved with the same `dpi` and `bbox_inches` the tool uses,
  and each drawn cell's region is located in the saved image and sampled
- **THEN** the sampled color equals `cmap(norm(expected_value))` for that cell within the
  documented per-channel tolerance

#### Scenario: Cell sampling does not depend on where glyphs land

- **WHEN** each drawn cell is sampled twice — once over a wide inset that includes its centered
  annotation text, and once over a narrow inset containing no text
- **THEN** the two samples agree with each other, and both agree with the cell's expected
  color, within the same documented tolerance — demonstrating rather than asserting that the
  sampling is unbiased by glyph placement, and keeping the check independent of the
  cross-platform font-rendering differences that constrain the whole-image RMS tolerance

#### Scenario: The oracle's subject is the tool's real output

- **WHEN** the figure the oracle inspects is compared against the PNG
  `plot_correlation_matrix` actually renders and commits for the same fixture
- **THEN** the two are pixel-identical (`compare_images` at `tol=0`), so the per-cell
  assertions are known to describe the artifact the tool really produces and not a test-only
  re-render that has drifted from it

### Requirement: The Two Verification Layers' Division Of Labor Is Enforced By A Test

The suite SHALL assert, in a single test, both that whole-image RMS comparison at the
established tolerance does **not** detect a realistic single-cell correlation defect and that
the per-cell verification **does** — so the reason both layers exist is enforced rather than
only described in prose, and a future change to either outcome fails loudly.

The defect SHALL be introduced by making the delegate render a doctored correlation matrix, not
by recoloring the saved PNG after the fact. A post-hoc pixel edit is invisible to every
artist-state assertion and would negatively control only the saved-PNG layer, leaving the bulk
of the first requirement unexercised.

#### Scenario: RMS misses a single-cell defect that the cell oracle catches

- **WHEN** the delegate renders a matrix in which exactly one cell carries a different
  correlation value, across a range of magnitudes up to the widest error the color scale can
  express
- **THEN** `compare_images` against the clean render at the snapshot suite's tolerance returns
  no failure for every one of those magnitudes
- **AND** the per-cell verification flags each of them at the data-array, annotation, and
  facecolor layers individually — each layer asserted separately, so no one layer can carry
  the test while another silently stops detecting

#### Scenario: RMS gaining single-cell sensitivity fails loudly rather than passing silently

- **WHEN** a future change causes whole-image RMS to detect a single-cell defect it previously
  missed
- **THEN** the test fails with a message directing the reader to update this change's design
  notes, the correlation-matrix known-limitation prose, and the RMS layer's own negative pin,
  rather than silently passing

#### Scenario: A per-cell layer losing sensitivity fails loudly

- **WHEN** a future change causes any one of the data-array, annotation, or facecolor layers to
  stop detecting the doctored cell
- **THEN** that layer's own assertion fails and names the layer, rather than the test still
  passing on the strength of the two layers that remain

### Requirement: The Cell Oracle Runs Unmarked In Per-PR CI

Every test in `test_viz_cell_oracle.py` MUST run unmarked (no `integration`, `live_smoke`, or
`live_smoke_slow` marker) so it executes inside the existing `python-audit` CI job's default
`pytest tests/ -m "not integration and not live_smoke"` invocation on every PR, with no live
dev stack required — the same condition the whole-image snapshot suite it complements already
meets. Per-cell verification that only ran on request would not close #768, because the defect
class it exists to catch is one nobody knows to go looking for.

#### Scenario: python-audit's default invocation includes the cell-oracle tests

- **WHEN** the `python-audit` CI job's pytest invocation collects `bloommcp/tests/`
- **THEN** every test in `test_viz_cell_oracle.py` is collected and executed, not excluded by
  the `-m "not integration and not live_smoke"` filter
