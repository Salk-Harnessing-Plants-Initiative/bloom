## Context

`plot_trait_histograms`, `plot_trait_boxplots`, and `plot_correlation_matrix` are the last 3 of
the original 5 relocated viz tools not yet on the `@as_mcp_tool` contract (the other 2,
`plot_heritability_bar`/`plot_variance_decomposition`, are being retired into
`heritability_analysis` per #462 and are out of scope). They currently:

- register via bare `mcp.tool()` (`sections/sleap_roots/__init__.py`);
- take `filename: str, traits: str = ""` and return a plain formatted string;
- call `bloom_mcp.experiment_utils.load_experiment_data` (the legacy 4-tuple adapter), not the
  `ExperimentReader` port;
- save PNGs directly under `BLOOM_PLOTS_DIR`, returning a `BLOOM_PLOTS_URL`-prefixed string via
  `_viz_shared.save_plot`/`save_plot_or_plots` — no versioned run, no provenance, no discovery
  entry;
- guard path traversal via `_viz_shared.validate_filename`, which **returns** an error string
  rather than raising, because (per its own docstring) "these 5 tools register via bare
  `mcp.tool()` and return plain strings end-to-end... nothing here catches [`BloomMCPError`]".

The constraints are fixed by the shipped code (mirrors `add-bloommcp-qc-inspect-tool/design.md`,
the closest architectural precedent — a read-only, pre-clean EDA tool built on this exact
contract):

- `@as_mcp_tool(input_model=, output_model=, errors=)` validates Pydantic I/O, maps exceptions to
  `BloomMCPError`, resolves the seed, and stamps one `Provenance`. `contract/wrap.py`
- `ExperimentReader.load_experiment(name, *, version="latest", require_clean=False)` returns an
  `ExperimentFrame` exposing `df`, `trait_cols`, the detected role columns, and a `source` label.
  `data_access/ports.py`
- `ResultStore.create_run(*, experiment, tool_class, provenance, …) -> RunHandle` then
  `commit(run, outputs) -> StoredRun`, which hashes and uploads each staged file and populates
  one `OutputLink` (signed/served URL) per output key — content-agnostic, so PNGs persist exactly
  like `pca_analysis`'s optional `include_plots` figures do. `result_store/ports.py`
- `_qc_shared._validate_experiment_name` / `_validate_trait_subset` / `_role_kwargs` are already
  shared, tested helpers `qc_clean`/`qc_inspect` use for exactly this shape of validation.
- `manifest.CANONICAL_TOOL_CLASSES` already reserves a `"viz"` slot from the pre-#438 retired
  plotting workflow, currently unclaimed by any live writer.
- `SupabaseReader`'s raw tier is **DB-only** (bloom#551): `ExperimentReader.load_experiment(name,
  ...)` resolves `name` as `str(experiment_id)` against Postgres, with no local-CSV fallback.
  `tests/smoke/conftest.py` documents this split explicitly today: the 7 already-`@as_mcp_tool`
  granular tools use its `db_experiment_id` fixture (a real numeric id already seeded in
  Postgres); the 5 plot tools (including our 3) still use `seeded_experiment` (a filename copied
  into the local, bind-mounted `BLOOM_TRAITS_DIR`) because they call
  `experiment_utils.load_experiment_data` directly, a separate local-CSV raw tier bloom#551 never
  touched. Converting our 3 tools onto `_ports.reader()` moves them into the DB-only group too —
  see the "Read Path Migration" decision below.
- `sleap_roots_analyze.visualization` does a bare `import matplotlib.pyplot` with no backend pin
  of its own — whichever module imports it first wins the active backend.
  `add-bloommcp-qc-inspect-tool/design.md` documents a *verified* bug from this: importing it
  before `matplotlib.use("Agg")` resolves to interactive `TkAgg` and crashes `savefig` headless.
  `qc_inspect.py` fixes this by pinning `Agg` inline, before its own `from sleap_roots_analyze
  import (...)`. All 3 files being converted here currently import
  `sleap_roots_analyze.visualization` (their own top-level delegate import) *before* their
  `from ._viz_shared import (...)` line, which is what pins `Agg` today — the same ordering
  `qc_inspect` found buggy, currently masked only by import ordering elsewhere in the process
  (whichever module the test/server process happens to import first). See the matplotlib
  Decision below.

## Goals / Non-Goals

- **Goals:** all 3 tools contract-wrapped, reading raw (no `require_clean`) via the
  `ExperimentReader` port, delegating 100% of figure *rendering* to `sleap_roots_analyze`,
  persisting a versioned run each with linked (not inline) figure outputs, discoverable via
  `list_existing_analyses`, covered by contract tests mirroring `qc_inspect`'s suite, with every
  existing in-repo caller of the old shape (unit tests + `tests/smoke/`) updated in step.
- **Non-Goals:** any change to `sleap_roots_analyze` delegate logic; any change to
  `plot_correlation_matrix`'s existing (non-delegated) strong-correlation summary computation
  beyond carrying it over unchanged; `plot_heritability_bar`/`plot_variance_decomposition`
  (#462's scope); `require_clean=True` (wrong fit for pre-clean EDA tools); adding new
  column-override parameters (e.g. an explicit `genotype_column` for boxplots) — this is a
  wrapper-layer convergence, not new capability; extending `tests/smoke/live_persistence_smoke.py`
  (its deep, hand-written `ResultStore` round-trip coverage per tool) to also cover these 3 tools
  — a separate, larger follow-up, tracked as an Open Question below, not a blocker here; fixing
  #669's pre-existing `list_existing_analyses.TOOL_CLASSES` omissions for `pca`/`umap`/
  `qc_inspect`.

## Decisions

- **Decision: read raw, do not set `require_clean`.** These 3 tools are pre-clean EDA, the same
  category as `qc_inspect` — a histogram/boxplot/correlation view is exactly what an agent uses
  *before* deciding `qc_clean`'s thresholds. Forcing a cleaned input would be backwards (and is
  explicitly called out as the wrong fix in the issue).

- **Decision: read via the `ExperimentReader` port (`_ports.reader().load_experiment(experiment,
  version="raw")`), accepting that this changes what `experiment` means, not just how it's
  spelled.** This is the one genuinely load-bearing consequence of "converge onto the same
  contract `pca_analysis`/`qc_clean`/`qc_inspect` already use": those 7 tools all read through
  this port, whose shipped `SupabaseReader` raw tier is DB-only (see Context). Today's 3 tools
  instead call `experiment_utils.load_experiment_data(filename)` directly — a *different*,
  local-`BLOOM_TRAITS_DIR` raw tier bloom#551 never touched. Converting means, in the default
  Supabase deployment, `experiment` stops accepting an arbitrary local CSV filename and starts
  requiring a DB-registered experiment id — exactly the same migration the other 7 tools already
  made, not a new class of behavior. (A `LocalReader` exists for fully-local deployments, where
  filename-style reads keep working — same as for every other already-converged tool.) This is
  **not** cosmetic and the proposal's Migration Plan below says so explicitly; treating it as
  "just a wrapper" would be the mistake an earlier draft of this proposal made.
  - *Alternative considered:* keep calling `experiment_utils.load_experiment_data` directly (as
    today) while still adding the Pydantic/`@as_mcp_tool`/`ResultStore` wrapping around it.
    Rejected: it would leave these 3 tools on a different, bespoke read path from every other
    tool in the folder — the opposite of convergence, and it would mean `ExperimentFrame`'s
    already-detected role columns (`genotype_col`, `trait_cols`) aren't available, forcing a
    second, parallel column-detection path to stay alive alongside `_qc_shared`'s.

- **Decision: pin `matplotlib.use("Agg")` inline at the top of each converted module, before
  importing any `sleap_roots_analyze` delegate** — mirrors `qc_inspect.py`'s own fix for the
  documented import-order bug (see Context). Cheap, and removes today's implicit dependency on
  *some other* module happening to import first and setting `Agg` before these 3 modules'
  existing `from sleap_roots_analyze.visualization import (...)` line runs.

- **Decision: an explicit empty `trait_columns: []` is `invalid_input`, not silently "all
  traits."** Mirrors `qc_inspect`'s own handling (which distinguishes omitted/`None` — "all
  detected traits" — from an explicit `[]` — a caller mistake, rejected) rather than `qc_clean`'s
  looser `params.trait_columns or resolved.trait_cols` fallback. Chosen because these 3 tools are
  explicitly "the same category as `qc_inspect`" per the issue, and `qc_inspect` is the nearer
  precedent for a raw-frame EDA read.

- **Decision: stamp `source_csv`/`source` on `ResultStore.create_run`, matching `qc_inspect`.**
  Each `create_run` call passes `source_csv=_ports.raw_source_for(params.experiment)` and
  `source=frame.resolved_source` (not just `provenance`), so the committed manifest
  content-addresses the exact frame each plot was rendered from (`input_sha256` /
  `based_on_version`) — the same reproducibility guarantee every other persisting tool in the
  family provides. Omitting this would silently produce a citable-looking but under-traceable
  run.

- **Decision: `tests/smoke/live_plot_tool_smoke.py` retargets to a tool that still writes to the
  bind-mounted `PLOTS_DIR`.** It exists specifically to prove a plotting tool's PNG lands on the
  real, bind-mounted `BLOOM_PLOTS_DIR` (issue #472) — a guarantee that no longer holds for these
  3 tools once their PNGs become `ResultStore` outputs instead. Retarget it to
  `plot_heritability_bar` (one of the 2 tools staying on the direct-`PLOTS_DIR` path) so it keeps
  testing the thing it was written to test, rather than silently testing something that no
  longer matches its own docstring's stated purpose.

- **Decision: mint 3 new tool classes (`trait_histograms`, `trait_boxplots`,
  `correlation_matrix`) rather than reactivating the shared, already-reserved `"viz"` slot.**
  `"viz"` is a single `CANONICAL_TOOL_CLASSES` bucket; `AnalysisDir`/`ResultStore` version an
  experiment's runs **per `(experiment, tool_class)` pair**, not per tool. If all 3 independent,
  non-composing producers wrote under one shared `"viz"` class, their version history would
  interleave (v1 = a histogram run, v2 = a boxplot run, v3 = a correlation run, v4 = another
  histogram run, …), making `"latest"` resolution meaningless and `list_existing_analyses`
  confusing — there is no sense in which a boxplot run supersedes a histogram run the way an
  `outliers`-class trim supersedes a `qc`-class clean. This is unlike `descriptive_stats`
  reactivating `"stats"` (one tool, its own historical 1:1 slot) or `cross_experiment_correlations`
  reactivating `"correlation"` (also 1:1, and a conceptually matching analysis).
  - *Precedent:* `umap_analysis` minted its own `"umap"` class rather than reactivating the also
    -unclaimed `"dimred"` slot, even though UMAP is a dimensionality-reduction tool and `"dimred"`
    is its closest conceptual predecessor — this repo's convention is that a new tool is free to
    mint a class matching its own identity, and an old generic bucket can stay permanently
    unclaimed for historical read-back only.
  - *Alternative considered:* reuse `"viz"` for all 3 (the literal reserved slot). Rejected as
    the default for the reason above, but flagged here as the one decision a reviewer may want to
    flip before implementation — it does reactivate an otherwise-dead `CANONICAL_TOOL_CLASSES`
    entry, which has some appeal for cleanliness even though it costs per-tool version identity.
  - The legacy `"viz"` entry stays listed in `CANONICAL_TOOL_CLASSES` / `list_existing_analyses
    .TOOL_CLASSES`, unclaimed, for historical read-back — same treatment as `"dimred"` today.

- **Decision: persist-and-link the figures (never inline blobs), same reproducibility contract
  as every other tool in the family.** Writing into `run.staging_dir` and committing via
  `ResultStore` gets signed/served `OutputLink`s "for free" and makes a rendered plot a citable,
  versioned artifact — exactly `qc_inspect`'s own persist-vs-transient decision, made the same
  way here.

- **Decision: a batched (paginated) render persists one output entry per page**, keyed
  `f"{name}_page{i}.png"` — mirrors `pca_analysis`'s multi-figure `include_plots` handling
  (`outputs`/`output_links` naturally carry one entry per figure). This replaces today's
  `save_plot_or_plots`-produced `"N pages: url1, url2, ..."` summary string; the structured result
  reports a plotted-trait/page count instead, and every page is independently linked.

- **Decision: `trait_columns: Optional[list[str]]` replaces `traits: str` (comma-separated),
  validated (via the shared `_viz_shared.resolve_trait_columns`, see below) at the same
  non-certified strictness level `qc_clean`/`qc_inspect` use for a raw (not cleaned-consumer)
  frame: existence + numeric.** This is a **behavior change** from `_viz_shared.parse_traits`,
  which silently drops an unknown/typo'd trait name rather than rejecting it — the new behavior
  surfaces the mistake instead of quietly plotting fewer traits than requested. An explicit empty
  list is additionally rejected (see above) and a duplicate name is additionally rejected (see the
  `resolve_trait_columns` Decision below) — two strictnesses `_qc_shared._validate_trait_subset`'s
  shared non-certified branch does not itself enforce, since it stays permissive for
  `qc_clean`/`qc_inspect`.

- **Decision: the bare-filename guard moves to `_qc_shared._validate_experiment_name`
  (raises `BloomMCPError`, code `invalid_input`), not `_viz_shared.validate_filename`
  (returns a string).** The string-returning guard exists specifically because these tools had no
  exception-catching wrapper; `@as_mcp_tool` is exactly that wrapper, so raising is now the
  correct (and consistent-with-siblings) shape. `_viz_shared.validate_filename` itself is
  untouched — `plot_heritability_bar`/`plot_variance_decomposition` still need it.

- **Decision: `plot_trait_boxplots` keeps today's behavior of requiring an auto-detected
  genotype column, no new override parameter.** A frame with no detectable genotype column now
  raises `BloomMCPError(code="assumption_violated")` (naming the experiment) instead of returning
  a message string. Adding an explicit `genotype_column` override (the way `qc_clean` has one) is
  deliberately out of scope — this change is a wrapper-layer convergence, not new capability.

- **Decision: register the 3 new classes in both `manifest.CANONICAL_TOOL_CLASSES` and
  `list_existing_analyses.TOOL_CLASSES`.** Without this, a run these tools persist would be
  invisible to `list_existing_analyses` — a regression relative to every other persisting tool in
  the folder, and a dead end for an agent trying to find a prior plot. Also add each to
  `_TOOL_CLASS_TO_PUBLIC_NAME` (bloom#671's mapping, guarded by
  `test_every_non_legacy_tool_class_has_a_public_name_mapping`) so a `list_runs` failure for one
  of these 3 names the public tool, not the raw tool_class string.

- **Decision: declare `errors=(ExperimentReadError, CommitFailedError, ManifestReadError)`,
  matching every sibling tool's post-#640 shape.** `store.create_run()`/`commit()` can genuinely
  raise either of the latter two; a tool that declares only `ExperimentReadError` swallows them
  into a bare `internal_error` correlation ref instead of the store's own already-actionable
  `tool_error` message — exactly the bug #640 fixed on the other 8 tools. Each of the 3 files gets
  its own `test_commit_failure_surfaces_as_tool_error` / `test_manifest_read_failure_surfaces_as_
  tool_error`, mirroring `qc_inspect`'s regression tests.

- **Decision: extract the 3 tools' near-identical trait-resolution logic into one shared
  `_viz_shared.resolve_trait_columns(frame, trait_columns, experiment)`, and reject duplicate
  names there.** Each tool independently reimplemented "validate + fall back to all detected
  traits + reject an empty result" as its own private `_resolve_trait_cols` — drift risk the same
  `_qc_shared` extraction pattern already exists to prevent. Duplicates are additionally rejected
  here (not in `_qc_shared._validate_trait_subset`'s shared non-certified branch, which stays
  permissive for `qc_clean`/`qc_inspect`, where a duplicate is harmless): for
  `plot_correlation_matrix` specifically, a duplicated trait name produces a self-correlation
  (r=1.0) that would silently count as a "strong positive correlation" in a permanent,
  provenance-stamped `ResultStore` artifact — not a transient string, so a silent miscount here is
  worse than in the tools that tolerate it today.

- **Decision: `plot_correlation_matrix` reports `zero_variance_traits` in its result.** Pearson
  correlation against a constant or all-NaN trait is `NaN`, and `NaN > 0.7` is `False`, so such a
  trait's pairs silently drop out of both `strong_positive_correlations`/
  `strong_negative_correlations` with no signal — realistic specifically because this tool reads
  raw, uncleaned data (no QC has removed a zero-variance trait yet). Naming the affected traits
  explicitly is cheap (one `std()` pass already available) and turns a silent undercount into a
  disclosed one.

- **Deferred: `source_id`/`run_id` pinning (bloom#626) is NOT threaded through these 3 tools,**
  even though `qc_clean`/`qc_inspect`/the other raw-frame readers gained it on `staging` while
  this change was in flight. `ExperimentReader.load_experiment`'s `source_id`/`run_id` kwargs
  default to `None`, so omitting them is fully valid — these 3 tools simply use the reader's
  default single-source resolution. Threading them through is a genuine follow-up (matching
  `qc_clean`/`qc_inspect`'s shape exactly), left out here to keep this change a wrapper-layer
  convergence rather than also picking up an unrelated, still-progressing feature. **Concretely
  disclosed cost of deferring it** (#466 review): an agent visualizing a multi-source experiment
  with any of these 3 tools gets no `source_note`-style advisory about which source was actually
  used (unlike `qc_clean`) — it silently gets the reader's default resolution with no signal that
  more than one source exists. Real, but no worse than the equivalent gap already existed for all
  8 granular tools before bloom#626, and each of the 5 tools bloom#626 already migrated took it
  as its own dedicated PR — following the same one-tool(-family)-at-a-time precedent here rather
  than folding it into this already-large convergence.

- **Decision: `plot_correlation_matrix` requires at least 2 resolved trait columns.** A
  correlation view of one trait has no pair to correlate — a single-trait selection (whether via
  an explicit `trait_columns=[t]` or an experiment with only one detected trait) now raises
  `invalid_input` before any run is persisted, rather than silently committing a degenerate 1×1
  masked heatmap as a normal artifact (#466 review). This check lives in `plot_correlation_matrix`
  itself, not in the shared `resolve_trait_columns` — a 1-trait selection is perfectly meaningful
  for `plot_trait_histograms`/`plot_trait_boxplots` (a single histogram/boxplot), so the
  minimum-2 rule must not leak into the shared helper both of those also call.

- **Decision: `plot_correlation_matrix.corr()` uses `min_periods=_CANONICAL_MIN_SAMPLES_PER_TRAIT`
  (reusing `qc_clean`/`qc_inspect`'s existing "10" convention) and reports
  `low_overlap_trait_pairs`.** Raw, uncleaned data can have disjoint per-trait missingness, so two
  traits can overlap in as few as 2 non-null rows — and any 2 points are *always* perfectly
  (anti)correlated, producing a spurious exact ±1.0 "strong correlation" from a near-empty sample
  (#466 review; the same silent-mislead failure mode already fixed for duplicate/zero-variance
  traits, via a third mechanism). `min_periods` makes pandas return `NaN` (excluded from the counts
  the same way a zero-variance trait already is) instead of a numerically valid but statistically
  meaningless coefficient; `low_overlap_trait_pairs` names exactly which pairs this affects
  (excluding any pair a zero-variance trait already explains, so a `NaN` cell isn't reported under
  two reasons at once).

## Risks / Trade-offs

- **Breaking request AND response shape** for the 3 tools (`filename`/`traits` kwargs → one
  `params` object; plain string → structured object). Mitigated: every in-repo caller of the old
  shape is updated in this same change (unit tests in `test_viz_tools.py`, plus
  `tests/smoke/test_plot_trait_histograms_smoke.py`/`test_plot_trait_boxplots_smoke.py`/
  `test_plot_correlation_matrix_smoke.py`/`conftest.py`/`live_plot_tool_smoke.py`); no consumer
  outside `bloommcp/` was found (`langchain/`, `apps/` checked). This brings the 3 tools in line
  with the other 10 tools in the same folder, which is the explicit point of #466.
- **The read path becoming DB-only (in the default Supabase deployment) is a real capability
  change, not just a rename.** A caller who today visualizes an arbitrary local CSV via
  `plot_trait_histograms(filename="my_upload.csv")` cannot do the same against a
  non-DB-registered file after this change (same limitation the other 7 granular tools already
  have). Mitigated by disclosure (this is the explicit point of "converge onto the same
  contract") and by the existing `LocalReader` escape hatch for fully-local deployments — not a
  new gap this change introduces, but real enough that it must not be described as "just a
  wrapper" (an earlier draft of this proposal did, incorrectly).
- **Three new tool classes instead of one** adds three `CANONICAL_TOOL_CLASSES`/`TOOL_CLASSES`
  entries rather than reactivating one. Traded deliberately for correct version-history semantics
  per tool (see the Decision above) — the cost is symbolic (a few more constants), not
  architectural.
- **New storage writes per plot call** (previously: one PNG write to a shared plots dir; now: a
  versioned run per call, growing storage over repeated exploratory calls). No different from
  the storage growth every other consumer tool (`pca_analysis`, `qc_inspect`, …) already accepts;
  no retention/pruning policy exists for any of them today, so this does not introduce a new gap.
- **`ResultStore` persistence has no per-caller ownership scoping — any caller with the same
  `experiment` id can discover and read any prior run's outputs via `list_existing_analyses`,
  including another user's.** This is an existing, architecture-wide characteristic already
  shared by the 7 already-converged granular tools, not introduced by this change — but this
  change meaningfully increases its blast radius: these 3 tools' outputs move from ephemeral,
  locally-written PNGs (visible only via a filesystem path, not enumerable through any MCP tool)
  to permanent, `list_existing_analyses`-discoverable artifacts, for what is likely the
  highest-call-volume tool family (ad hoc exploratory plotting). No mitigation is proposed here —
  it is the same trust boundary every other persisting tool already operates inside — but it is
  worth naming explicitly given the 3x increase in exposed surface, rather than leaving it
  implicit.
- **A matplotlib figure-handle leak is possible if a batched delegate
  (`create_trait_histograms_batched`/`create_trait_boxplots_by_genotype_batched`) raises
  partway through internally**, having already created (but not returned) figures for earlier
  pages — this tool's own `finally: for fig in figures: plt.close(fig)` cannot reach them, since
  the delegate call is a single all-or-nothing expression. This is **not** the same situation as
  `pca_analysis`'s multi-plot `include_plots` path, which avoids exactly this class of bug via
  `tools/_plots.py::generate_figures` populating its `figures` dict one key at a time so a
  mid-generation exception still leaves every already-successful figure reachable — `pca_analysis`
  is safer here, not merely "no worse." These 3 tools can't reuse that pattern because the
  vendored `sleap-roots-analyze` batch functions return `list[Figure]` all-or-nothing with no
  per-page hook exposed, not because bloommcp chose not to. Tracked as
  [#725](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/725) rather than fixed
  here — pre-existing (this PR did not introduce the batched delegate call shape), and any real
  fix needs either an upstream `sleap-roots-analyze` change or a coarser figure-registry-diffing
  workaround, both out of scope for a wrapper-layer convergence.

## Migration Plan

No data migration for storage/manifests: these are net-new tool classes with no prior committed
runs to reconcile, and the 3 tools' old direct-to-`BLOOM_PLOTS_DIR` output was never versioned
or manifest-tracked, so there is nothing to backfill.

Callers DO need to migrate, on three axes:
1. **Request shape**: `plot_trait_histograms(filename="x.csv", traits="a,b")` becomes
   `plot_trait_histograms({"experiment": "x", "trait_columns": ["a", "b"]})` (one `params`
   object, matching every `@as_mcp_tool` tool's MCP schema).
2. **Response shape**: a plain "Plot saved: ..." string becomes a structured result with
   `run_ref`/`outputs`/`output_links`/etc.
3. **Identifier semantics** (default Supabase deployment only): `experiment` must now name a
   DB-registered experiment, not an arbitrary `BLOOM_TRAITS_DIR` filename — see the Decision
   above. Every in-repo caller on this axis is updated in this change (see Risks); there is no
   known external caller to migrate.

## Open Questions

- Whether to reactivate the shared `"viz"` `CANONICAL_TOOL_CLASSES` slot instead of minting 3 new
  ones (see the flagged alternative above) — resolved here in favor of 3 new classes, but callable
  out during review.
- Whether `tests/smoke/live_persistence_smoke.py` (the hand-written, per-tool `ResultStore`
  round-trip smoke script covering `qc_clean`/`remove_outliers`/`clustering`/`descriptive_stats`
  today) should eventually grow equivalent coverage for these 3 tools. Left as a follow-up, not
  in this change's scope — it is a large, bespoke script, and #466 does not ask for new
  live-persistence smoke coverage.
