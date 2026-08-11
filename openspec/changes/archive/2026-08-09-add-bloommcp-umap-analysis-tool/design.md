## Context

`umap_analysis` is the fourth granular consumer tool (after `pca_analysis` #377,
`remove_outliers` #378, `clustering` #309) and the second **stochastic** one after
`clustering`'s kmeans/gmm methods. The upstream `sleap_roots_analyze==0.1.0a5` (already
installed and pinned) ships exactly what #425 needs:

- `perform_umap_analysis(df, feature_cols, n_neighbors=15, min_dist=0.1, n_components=2,
  random_state=42) -> Dict` — still returns a plain dict (not `UMAPResult` directly); it
  silently clamps `n_neighbors` to `n_samples - 1` when the request is larger, and always
  fits a `StandardScaler` internally (no `standardize` toggle, unlike PCA/clustering).
- `UMAPResult` (`sleap_roots_analyze.result_types`, `frozen=True` dataclass) with
  `embedding`, `n_neighbors`, `min_dist`, `n_components`, `feature_names`, `n_samples`,
  `standardized`, `random_state`, plus `to_dict()`/`to_json()`/`from_umap_dict(d, *,
  random_state=None)`.
- Two plotters in `sleap_roots_analyze.visualization`: `create_umap_single_trait` (fully
  self-contained) and `create_umap_colored_by_top_traits` (requires a `pca_results: Dict`
  with `loadings`/`eigenvalues`/`cumulative_variance_ratio` to rank trait contributions).

## Goals / Non-Goals

- Goals: a granular `umap_analysis` tool matching the `pca_analysis`/`clustering` contract
  shape exactly; correct stochastic-seed provenance; reuse of `_plots.py` unmodified; the
  `n_neighbors >= n_samples` silent-clamp turned into a structured, pre-dispatch
  `assumption_violated`; no new dependency.
- Non-Goals: signed/openable links (blocked on #388 Part 2); a golden-coordinate oracle
  (UMAP is not cross-platform bit-reproducible); the Phase-1 `run_*_workflow` tools (retired
  outright by `devendor-bloommcp-analysis`, not something this change touches either way);
  t-SNE or any method beyond PCA/UMAP.

## Decisions

### Stochastic-seed wiring: follow `clustering`, not `pca_analysis`

`pca_analysis` declares no `random_state` tool-function parameter, so the contract records
`seed=None` (its delegate's `svd_solver="auto"` path is deterministic for this regime).
UMAP's embedding is genuinely seed-dependent, so `umap_analysis` must declare
`random_state: int` as a keyword-only parameter on the tool function (mirroring
`clustering(params, *, random_state: int, provenance: Provenance)`), forward it to
`perform_umap_analysis(..., random_state=random_state)`, and pass it into
`UMAPResult.from_umap_dict(result_dict, random_state=random_state)`. The contract wrapper
(`bloom_mcp/contract/wrap.py`) resolves `params.seed` into `random_state` and stamps the
*resolved* int into `Provenance.seed` only because the tool function declares that
parameter — this is an explicit kwarg-injection contract, not name inference, so the
parameter name and its presence both matter.

### `n_neighbors >= n_samples`: structured `assumption_violated`, not silent clamp

`perform_umap_analysis` clamps `n_neighbors` to `n_samples - 1` internally and returns the
*effective* value in its dict — it never raises. Letting that clamp happen silently would
mean a caller's requested `n_neighbors=30` on a 10-sample experiment gets embedded with
`n_neighbors=9` with no signal, and the persisted `UMAPResult.n_neighbors` would disagree
with what was asked for no visible reason (the same class of problem `pca_analysis` guards
against for delegate-dropped constant columns). `umap_analysis` pre-checks
`params.n_neighbors >= n_samples` **before** calling the delegate and raises
`BloomMCPError(code="assumption_violated")` naming both the requested value and the maximum
usable one (`n_samples - 1`), with a remedy to lower `n_neighbors` or supply more samples
(talmolab/sleap-roots-analyze#67).

### `create_umap_colored_by_top_traits`: internal, non-persisted PCA call

Unlike `create_variance_decomposition_plot` (excluded from PCA's plot catalog because it
needs a full `compare_trait_heritabilities()` LMM pipeline output with no natural place to
come from), `create_umap_colored_by_top_traits` only needs `loadings`/`eigenvalues`/
`cumulative_variance_ratio` — exactly what `perform_pca_analysis` already returns, over the
*same* trait selection `umap_analysis` already validated and loaded. Three options were
weighed:

1. **Exclude the plot** (mirror PCA's precedent). Rejected: the issue explicitly names both
   `create_umap_colored_by_top_traits` and `create_umap_single_trait` as the catalog: this
   plot is scientifically the more useful one (it interprets the embedding via trait
   loadings), and the data needed to build it is one cheap, already-available delegate call
   away — the exclusion precedent doesn't transfer.
2. **Require a companion `pca_analysis` run and consume its persisted result.** Rejected:
   couples `umap_analysis` to another tool's run existing first, which is fragile (which
   version? what if trait selections differ?) and contrary to the granular-tool philosophy
   of each tool being independently runnable against a cleaned experiment.
3. **Call `perform_pca_analysis` internally, in-memory, only to feed the plot — never
   persisted as its own run.** **Chosen.** The ranking math is still fully delegated (no
   PCA math owned by bloom-mcp); it doesn't create a second versioned run, doesn't appear in
   `outputs` except as the PNG, and it uses the same trait selection + `standardize`
   semantics the user already chose for the UMAP call itself. `explained_variance_threshold`
   for this internal call is fixed at the `perform_pca_analysis` default (0.95) since it only
   affects trait-ranking cutoff, not anything persisted or returned.

**Resolved for this implementation**: option 3 is what's implemented. This was originally
flagged as an open question pending Elizabeth's sign-off; it's treated as decided so the
tool isn't blocked on that answer, and can be revisited (dropping the plot key, or replacing
it with an explicit `pca_run_ref` input) if review feedback prefers a different option — the
rest of the tool (the embedding, seed provenance, persistence, the other plot key) does not
depend on this answer either way.

### Persistence shape

Mirrors `pca_analysis`/`clustering`: `tool_class="umap"`; `embedding_coords.csv` (metadata
identity columns prepended via `frame.metadata_cols`, exactly like `clustering`'s
`labels.csv`, so a row maps back to its plant rather than relying on positional alignment);
`umap_result.json` via `UMAPResult.to_json()`; `based_on_version = frame.source`;
`source_csv` snapshot for `input_sha256` lineage. Result model extends `RunLinks` (like
`PCAAnalysisResult`) rather than redeclaring the four link fields.

### Plots wiring

Identical shape to `pca_analysis`'s `include_plots`/`plots`: validate against a
`_UMAP_CATALOG_KEYS = frozenset({"create_umap_single_trait",
"create_umap_colored_by_top_traits"})` via `validate_plot_keys` **before** `create_run`;
build zero-arg callables in a local `_umap_plot_calls(...)` (not in `_plots.py`); generate
via `generate_figures`; the whole persistence region wrapped in `try/finally` with
`close_figures(figures)` in `finally`; PNGs merge into `outputs`. `_plots.py` needs no
changes — this was the explicit design intent of #426's `design.md`.

`create_umap_single_trait` needs a `trait_col` to color by; default to the first selected
trait column when `plots` requests it without further parameterization (no per-trait
selection param in this change — a future change can add one if needed, same spirit as
PCA's fixed `plot_type='loadings'` for the heatmap).

### Parameter bounds at the Pydantic layer

`perform_umap_analysis` raises plain `ValueError` for `n_neighbors <= 0`, `min_dist < 0`, and
`n_components <= 0` — genuine caller mistakes, not data degeneracy. `umap-learn` itself is
stricter still on `n_neighbors`: it hard-rejects `n_neighbors == 1` regardless of `n_samples`
("n_neighbors must be greater than 1", verified directly against the installed package).
Without a field constraint, `umap_analysis`'s generic `except (ValueError, ...)` handler
around the delegate call would catch these and mislabel them `assumption_violated`, the same
class of bug `pca_analysis` avoids for its own `n_components`/`explained_variance_threshold`
via `Field(ge=..., le=...)`. `UMAPAnalysisParams` therefore declares `n_neighbors: int =
Field(default=15, ge=2)`, `min_dist: float = Field(default=0.1, ge=0.0)`, `n_components: int =
Field(default=2, ge=1)` — each rejected as `invalid_input` by the contract's input-validation
layer before the tool function ever runs, so the delegate is never called for these.

`n_components` has no *upper* bound check: unlike PCA (variance-bounded, so a too-large
request is clamped, not rejected), UMAP has no natural clamp for `n_components` and
`perform_umap_analysis` does not clamp it either. A `n_components` close to or above
`n_samples` risks tripping an internal `umap-learn` spectral-initialization failure, which is
not guaranteed to be a clean `ValueError`. The delegate call is wrapped in `except
(ValueError, KeyError, RuntimeError, TypeError)` (broader than PCA's `except ValueError`,
matching `clustering`'s broader `except (ValueError, RuntimeError)` for the same reason:
multiple upstream failure modes, not one) — `TypeError` was added after review verified that
umap-learn's spectral-embedding eigensolver raises a bare `TypeError` (not `ValueError`) for
a legitimate small-sample-count combination (`n_samples=3`, `n_neighbors=2`); without it, that
case would have surfaced as an opaque `internal_error` instead of the intended
`assumption_violated`. Independent of which exception type fires, the contract wrapper's
blanket "any raised exception becomes a structured `BloomMCPError`, never leaked" guarantee
remains the backstop for any failure mode this `except` clause still doesn't name; an
upstream-side failure for an oversized `n_components` already surfaces safely that way.

`n_neighbors` has a lower bound of `ge=2`, not `gt=0`: umap-learn hard-rejects
`n_neighbors=1` ("n_neighbors must be greater than 1") independent of `n_samples` — verified
directly against the installed package on both a 2-sample and a 10-sample input, both fail
identically. Since this is a caller mistake that's never data-dependent, it's rejected at the
Pydantic layer before dispatch, the same reasoning as `min_dist`/`n_components` below.

### Non-finite embedding: checked before persistence begins

`UMAPResult.to_json()` defaults to `allow_nan=False` and raises `ValueError` on any
non-finite embedding value. `perform_umap_analysis` validates its *input* is NaN-free but
never checks its *output* embedding for finiteness, and umap-learn's spectral initialization
can produce non-finite coordinates on pathological inputs (e.g. a disconnected neighbor
graph) even when the input passed validation. If left unchecked, a non-finite embedding would
raise inside the `create_run`/`commit` region — after `store.create_run()` has already
allocated a `staging_dir` — surfacing as an unhandled `internal_error` with an orphaned
staging directory left behind (the store only cleans up the staging dir inside
`commit()`'s own success path). `umap_analysis` therefore checks
`np.isfinite(embedding).all()` immediately after the delegate call returns and before
`store.create_run()` is invoked, raising a structured `assumption_violated` if it fails — the
same "check before persistence begins" pattern the `n_neighbors` guard uses, applied to the
delegate's *output* rather than the caller's *input*.

### Real-delegate tests run in the fast (non-integration) lane

`bloommcp/tests/test_oracle.py` already exercises the real `perform_umap_analysis` under
`@pytest.mark.integration` (excluded from per-PR CI; run via `/pre-merge`) because full-fixture
statsmodels/UMAP oracle tests are slow and have intermittently stalled in CI containers.
`umap_analysis`'s own tests are a different shape: like `pca_analysis`/`clustering`, most
tests use a monkeypatched/spied delegate (`injected_ports` defaults `perform_umap_analysis`
to a fast, structurally-valid fake), and only a small, fixed number of tests restore the
genuine delegate because real UMAP numerics are the actual point of the test: the
structural/shape characterization test, the same-seed-determinism test, the
n_components-near-n_samples boundary test, the n_samples=3/n_neighbors=2
eigensolver-failure test, and one plot round-trip test (5 total) — mirroring how
`pca_analysis.py`'s and `clustering.py`'s own test suites already call their (non-numba) real
delegates in the fast per-PR lane without an `integration` marker. Given numba JIT-compiles once per
process (a few seconds, not a hang) and the real-delegate call count here is small and fixed
(not a full grid), these tests are **not** marked `@pytest.mark.integration` — a conscious
choice, made explicit here rather than left as a silent gap, given the fast lane's tightened
`timeout-minutes: 20` (added after a prior unrelated hang, #454). If this proves too slow or
flaky in practice, marking these specific tests `integration` is a low-cost follow-up.

### No `standardize` param

`perform_umap_analysis` has no `standardize` toggle (unlike PCA/clustering) — it always
fits a `StandardScaler`. `umap_analysis` therefore has no `standardize` field in its params;
`UMAPResult.standardized` will always read `True` for calls through this tool. Documented in
the param's absence rather than adding a no-op field.

## Risks / Trade-offs

- **Internal PCA call cost**: computing `perform_pca_analysis` in-memory just to color one
  plot adds a second (cheap, deterministic) fit when `create_umap_colored_by_top_traits` is
  requested. Acceptable — it only runs when that specific plot key is requested, not on the
  default `include_plots=False` path, and PCA on a handful of trait columns is fast.
- **Plotter API drift**: same class of risk as #426 — a smoke test exercising both plot keys
  guards against silent signature drift.
- **No golden embedding**: the structural + within-run-determinism oracle is weaker than a
  pinned golden. Accepted as inherent to UMAP's numba backend (documented cross-platform
  non-reproducibility), consistent with how the issue scopes the oracle.
- **Orphaned staging directory on partial write failure (pre-existing, not a regression
  here)**: if a `fig.savefig()` call raises between the two data artifacts being written and
  `store.commit()`, the `finally` block only closes in-memory matplotlib figures — nothing
  removes the already-allocated `run.staging_dir` from disk, since both `ResultStore`
  implementations only clean it up inside `commit()` itself. This is genuinely reachable, not
  hypothetical, but it is identical in `pca_analysis` and `clustering` today — not something
  this change introduces or could fix in isolation without touching all three tools' shared
  persistence shape. Flagged here as a cross-cutting follow-up rather than silently
  out-of-scope.

## Open Questions

- Should the internal PCA call for `create_umap_colored_by_top_traits` use
  `params.trait_columns`-selected traits with `standardize=True` unconditionally, or attempt
  to mirror some future `umap_analysis` standardize toggle? Given `umap_analysis` has no
  `standardize` param at all (see Decision above), this is moot for this change — revisit if
  a `standardize` toggle is ever added to the UMAP tool.
- (Resolved for this implementation, revisitable on review — see the "Resolved for this
  implementation" note under Decision #3 above.) Confirm with Elizabeth whether the internal,
  non-persisted PCA call for the top-traits plot is acceptable, or whether she'd prefer that
  plot excluded from this change's catalog and deferred to a follow-up that takes an explicit
  `pca_run_ref` input instead.
