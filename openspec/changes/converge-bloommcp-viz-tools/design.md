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
  `ExperimentReader` port, delegating every chart-element rendering decision to
  `sleap_roots_analyze` (a disclosure annotation drawn onto a delegate-returned `Figure`
  afterward, e.g. `heatmap_caveat`'s footnote, is not chart-element rendering — see design.md's
  round-6 correction), persisting a versioned run each with linked (not inline) figure outputs,
  discoverable via
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
  two reasons at once). The `min_periods` boundary itself (overlap `== 10` not flagged, `== 9`
  flagged) is pinned by a dedicated parametrized test — the round-3 test only exercised
  overlap `== 2`, deep inside the flagged region, so an off-by-one in the comparison operator
  (`<=` vs. the correct `<`) or in the constant itself would have sailed through the full suite
  undetected (#466 review round 4).

- **Decision: `plot_correlation_matrix` additionally requires at least 2 *non-zero-variance*
  trait columns, not merely at least 2 columns.** The plain column-count guard above doesn't
  catch a selection where all-but-one (or all) resolved columns are constant/all-NaN — every
  cell of the correlation matrix would then be `NaN`, a meaningless artifact the guard's own
  stated purpose ("no degenerate result committed") should also cover (#466 review round 3).
  Raised as `assumption_violated` (discovered only after reading the data), not `invalid_input`
  (the plain count check, a pure input-shape fact knowable before any read) — mirrors the same
  distinction `plot_trait_boxplots`'s missing-genotype-column check already draws.

- **Decision: the rendered PNG is explicitly disclosed as unmasked — in the image itself, not
  only in JSON — and the disclosure is stamped into the manifest, not only the live
  response.** `plot_correlation_matrix`'s own `.corr(min_periods=...)` call only feeds the JSON
  summary (`strong_positive_correlations`/`zero_variance_traits`/`low_overlap_trait_pairs`); the
  persisted image is rendered by a *separate*, independent call to the vendored
  `create_correlation_heatmap`, which runs its own unguarded `.corr()` with no `min_periods` and
  no way to accept a precomputed/masked matrix (#466 review round 3 — a real gap that survived
  two rounds of the author's own review, since both rounds fixed the summary without checking
  whether the image agreed with it). A flagged pair's cell can still render as a solid,
  confidently-colored ±1.0 square — genuinely re-coloring it is out of scope (patching the
  vendored delegate, or re-implementing heatmap rendering in bloommcp against this file's own
  no-vendored-plotting-logic principle; tracked at
  [#747](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/747)), but round 3's
  first version of this fix was **JSON-only** — a caller who only ever downloads/opens the saved
  PNG (never reads the JSON response) got zero signal (#466 review round 4). Two round-4 fixes,
  both cheap and in-scope:
  - `heatmap_caveat`, whenever non-empty, is drawn as a footnote directly onto the already-
    rendered `Figure` via `fig.text(...)` *before* `savefig` — not a per-cell hatch/marker on
    the specific flagged pair(s): that would require reverse-engineering the vendored delegate's
    exact cell geometry (row/column orientation, any axis flip it applies), and getting that
    wrong would mislabel a *different* cell as flagged, which is worse than no annotation.
  - `heatmap_caveat` is now also stamped into the persisted run's `params` (mirroring
    `resolved_trait_columns`, same `provenance.model_copy` call) — round 3's fix stamped
    `resolved_trait_columns` there but left `heatmap_caveat` live-response-only, undercutting the
    very motivation ("a later reader of the manifest…") the `resolved_trait_columns` fix itself
    states.
  - Wording tightened (`heatmap_caveat`'s field description and its message text) to lead with
    the consequence ("some cell(s)… still colored as if genuine") rather than internals
    ("not masked… unguarded correlation"), since the field is meant to reach a domain scientist
    (or an LLM agent relaying it to one) who may not already know what `min_periods` means.
  - **Known, disclosed, narrow taxonomy gap, not fixed here:** a pair that is globally
    non-constant and clears `min_periods` overlap can still be *locally* constant within that
    shared overlap, producing a `NaN` cell named in neither `zero_variance_traits` nor
    `low_overlap_trait_pairs` (not a false-positive risk — the vendored heatmap independently
    produces the same `NaN` — just an incompleteness in *why* a blank cell is blank).

  **Round 6: round 4's footnote itself didn't deliver on its stated purpose.** It named a
  *count* of flagged cells and pointed to `zero_variance_traits`/`low_overlap_trait_pairs` for
  specifics — but those fields exist only in the JSON response/manifest, never in the PNG. A
  caller who only ever opens the saved image (exactly the audience the round-4 fix was *for*,
  per its own commit message) was told a problem exists with no way to tell *which* cell to
  distrust — the same failure pattern this PR's review history had already caught twice (round
  3: no PNG signal at all; round 4: signal present but didn't reach the PNG/manifest), one layer
  softer. Fixed cheaply and safely: `create_correlation_heatmap` draws its axis tick labels from
  this same `trait_cols` list, in this same order, so the footnote now interpolates the actual
  flagged trait/pair names (not just a count) — a PNG-only viewer can cross-reference a name
  here against a label they can already see on the image, with none of the "wrong cell" geometry
  risk that ruled out per-cell hatching in round 4. Capped at 10 names (`", +N more"` beyond
  that) so a cylinder-scale selection with many flagged pairs doesn't produce an unreadably long
  footnote — the full, uncapped lists remain in `zero_variance_traits`/`low_overlap_trait_pairs`.

- **Decision: `FIGURE_REGISTRY_LOCK` — a process-wide lock around each of the 3 tools' figure-
  creating delegate call — landed in this PR rather than deferred, to reconcile an unresolved
  conflict with sibling PR #726/#721.** #726 (also in flight, developed in parallel with no
  awareness of this PR) wraps every matplotlib-figure-creating call site in bloommcp — including
  the *pre-#466* versions of these same 3 files — in a lock defined in `bloom_mcp.tools._plots`,
  specifically because FastMCP's thread-pool dispatch means two figure-creating tool calls can
  genuinely interleave, and (once #726 lands) its own `generate_figures` allocate-then-raise
  cleanup diffs matplotlib's *shared, process-wide* figure registry — a diff that cannot tell its
  own orphaned figure apart from one a different, unrelated concurrent call just allocated,
  unless nothing else can create a figure while that diff's window is open (#466 review round 6).
  #683's structural rewrite of these 3 files means #726's diff to them cannot reapply cleanly,
  and whichever PR merges second either eats that conflict or (worse, if unresolved) ships with
  these 3 newly-converged tools as the only matplotlib call sites left unprotected. Since
  `FIGURE_REGISTRY_LOCK` doesn't exist on `staging` yet (#726 hasn't merged), it is defined here
  too — matching #726's exact design/rationale, so the two PRs' additions of the same constant
  become a trivial, easily-resolved duplicate-definition conflict rather than a silent gap.
  Scope is narrow (only the delegate call that actually allocates a figure, not the surrounding
  save/commit/persist span), matching #726's own scoping rationale exactly. Flagged on #726
  itself so its author/reviewer isn't surprised by the conflict at merge time.

- **Decision: `resolved_trait_columns` is recorded — in the result and stamped into the
  persisted run's `params` — on all 3 tools, not just reported as a count.** When
  `trait_columns` is omitted, auto-detection resolves the actual list used to render/persist the
  artifact, but only its count (`n_traits`/`n_traits_plotted`) was previously recorded anywhere
  — a manifest read months later couldn't answer "exactly which traits produced this artifact"
  if source columns had drifted since (#466 review round 3). Stamped via
  `provenance.model_copy(update={"params": {**provenance.params, "resolved_trait_columns":
  trait_cols}})` — extending the tool-call's own `params` dict rather than adding a new
  `Provenance` schema field, the same "additive, caller-merged, no schema-version bump" pattern
  `input_validation` already uses (see `Provenance`'s own docstring). Mirrors `pca_analysis`'s
  existing `feature_names` field for the same underlying need.

- **Decision: `page_traits` maps each committed output filename to the trait columns rendered
  on that page**, for the 2 batching-capable tools. A batched (paginated) render's structured
  result previously said only *how many* pages existed, not *which traits* landed on which one —
  discoverable only by opening an image and reading its axis labels (#466 review round 3). Computed
  directly from `trait_cols` chunked by `_DELEGATE_BATCH_SIZE` (the vendored batch delegate's own
  default, not overridden by this tool's call) — a plain slice, not a re-derivation of anything
  the delegate itself decided. `_DELEGATE_BATCH_SIZE` is pinned against the live delegate
  signature by a dedicated test (mirrors `TRAIT_BATCH_THRESHOLD`'s own existing live-signature
  pin) so a future `sleap-roots-analyze` bump that changes the default is caught, not silently
  desynced. **Verified against the delegate's own rendered content, not re-derived from the
  same formula twice** (#466 review round 4): the round-3 test only recomputed
  `trait_cols[start:start+batch_size]` and compared it to the identical production expression,
  which pins the batch *size* (via the separate live-signature test) but nothing pinned the
  batching *order* — a future delegate version that reorders/groups traits before chunking
  (independent of batch size) would mislabel `page_traits` with no test failing. The round-4
  test instead spies on the batched delegate call, reads each returned `Figure`'s own subplot
  titles (`create_trait_histograms_batched` titles each axis `f"{trait}\n(n={count})"`;
  `create_trait_boxplots_by_genotype_batched` titles it the bare trait name — both confirmed
  against the live delegate, not assumed), and asserts `page_traits` matches what was actually
  rendered. Also now covers `n_traits=64` (an exact multiple of `batch_size=16`) alongside the
  existing `n_traits=60` — the boundary case a non-multiple never exercises. **Round 5** adds
  `n_traits=65` (one leftover trait alone on the last page) — closing a second boundary case
  the review round 4 asked for. Writing it surfaced a real bug in the round-4 test *helper*
  itself (not the production code): `create_trait_histograms_batched` pads a not-exactly-full
  page to its fixed `n_cols=4` grid with extra, invisible, blank-titled axes (confirmed against
  the live delegate) — the helper was reading `fig.axes` unconditionally, so it picked up 3
  spurious empty-string "titles" alongside the 1 real one. Fixed by filtering to
  `ax.get_visible()`; the same filter was applied to `create_trait_boxplots_by_genotype_
  batched`'s helper too, at the time believed (wrongly — see round 6 below) to be defensive
  only, since that delegate's `n_traits=65` case (1 leftover trait) happened to land on an
  exact 1×1 grid with no padding.

  **Round 6 corrects that claim: `create_trait_boxplots_by_genotype_batched` DOES pad,
  same as the histograms delegate — its grid sizing is just adaptive, not fixed at 4
  columns, so it only avoids padding when a remainder happens to fit its chosen grid
  exactly** (confirmed directly against the live delegate across several remainders: 1, 2,
  and 3 leftover traits fit exactly with no padding; 5 and 8 leftover traits pad to an 8-slot
  grid). A committed test docstring had asserted the "doesn't pad" claim as a general fact
  from that one `n_traits=65` case alone — factually wrong, caught by review, corrected, and
  closed with a real test: `n_traits=69` (5 leftover traits, a remainder confirmed to pad) is
  now parametrized alongside `65` so the visible-axes filter is genuinely exercised for this
  tool too, not merely applied "just in case."

- **Decision: the 3 new Params models declare `model_config = ConfigDict(extra="forbid")`.**
  An unknown field isn't currently exploitable — `@as_mcp_tool`'s Pydantic validation already
  means an unrecognized kwarg never reaches the tool body regardless — but silently *accepting*
  one (Pydantic's default) masks a caller typo (e.g. `trait_column` instead of `trait_columns`)
  that would otherwise be a hard, immediate schema error (#466 review round 5, matching the
  recommendation already made on sibling PR #726). Not backported to the other 8 tools' Params
  models in this PR — out of scope, a separate, larger-blast-radius change.

- **Decision: added direct test coverage for `resolve_trait_columns` against an all-NaN trait
  column** (#466 review round 5 — this exact "computed but not surfaced" bug class had
  slipped through two prior review rounds once already, for `plot_correlation_matrix`'s
  zero-variance handling). Confirms the shared helper does NOT reject an all-NaN column —
  intentional: the all-zero-variance guard lives in `plot_correlation_matrix` alone (a
  histogram/boxplot of an all-NaN trait is a legitimate, if uninformative, plot; a correlation
  matrix cell needs variance to mean anything at all). Writing this test surfaced an authoring
  mistake, not a production bug: constructing the all-NaN column as `[None] * 6` makes pandas
  infer `dtype=object` (not numeric), which fails `_validate_trait_subset`'s numeric check for
  an unrelated reason before ever reaching the variance question the test was about — fixed by
  using `[float("nan")] * 6` instead, which pandas correctly infers as `float64`.

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
  implicit. Tracked at
  [#769](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/769) (#466 review
  round 4 — flagged that this risk, unlike #725/#747/#748, had no linked tracking issue and so
  risked quietly becoming permanent institutional debt with no visible tracker). **Escalated to
  `priority: high` in round 5**, which independently confirmed this as a concrete cross-caller
  read exposure (any caller holding the shared MCP credential can enumerate/read another lab's
  results for a known experiment id via `list_existing_analyses`/`get_download_links`), not
  merely a theoretical architecture note.
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
- `plot_trait_histograms`/`plot_trait_boxplots` still delegate raw-data NaN/outlier handling
  silently (no "N rows excluded" or per-trait missingness disclosure), asymmetric with the
  rigor now applied to `plot_correlation_matrix` (`zero_variance_traits`/`low_overlap_trait_
  pairs`/`heatmap_caveat`). Filed as
  [#748](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/748) (#466 review
  round 3, originally suggestion-tier). **Re-scoped and priority raised in round 5**: `plot_
  trait_boxplots` specifically discloses no sample size *anywhere*, not even in the subplot
  title (unlike `plot_trait_histograms`'s delegate-provided `f"{trait}\n(n={count})"`) — a
  genotype group reduced to a handful of points by missingness renders as a normal-looking
  box with zero signal to the researcher. Concluded to be more consequential than
  documentation polish, not fixed in this PR. **Round 6** re-confirmed this as real and
  unmitigated and added a comment nudging for a concrete remediation owner, since the issue
  had priority but no owner; both `plot_trait_histograms.py`/`plot_trait_boxplots.py` now
  disclose the asymmetry directly in their module docstrings (previously only noted in a test
  comment) and confirm the delegates render a literal `"No data"` panel for an all-NaN trait
  (verified against the live delegate) rather than a silent blank.
- `ResultStore`'s no-per-caller-ownership-scoping gap ([#769](https://github.com/Salk-
  Harnessing-Plants-Initiative/bloom/issues/769), see the Risks entry above) was independently
  re-confirmed real in round 6 (a shared `bloom_agent` DB/Storage role, no scoping filter in
  `list_available_experiments`, `USING (true)` RLS) but, being pre-existing across 8 tools
  that shipped before #466, correctly not treated as blocking this PR's merge. Added a comment
  nudging for a concrete remediation owner rather than leaving it to sit as a filed-but-
  unowned `priority: high` issue indefinitely — not assigned unilaterally, since triage isn't
  this PR's call to make.

## Test Count Verification

Round 4 of PR review reported a self-reported "1464 passed, 0 failed" claim from round 3 did
not reproduce in the reviewer's isolated export (`1428 selected / 1461 total`, plus ~59
unrelated UMAP failures and one failure the review itself attributed to a partial export
missing a sibling `web/` directory). Re-ran the **exact** CI invocation
(`cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration and not
live_smoke" -v --tb=short`, from `.github/workflows/pr-checks.yml`) against a freshly
recreated worktree of this branch, with `uv lock --check` confirmed in sync first: **1464
passed, 33 deselected** (1497 total via `--collect-only` with no marker filter), 0 failed —
reproduced exactly at the time. The discrepancy against the reviewer's number was not (and is
still not) explained on this side; their own disclosed environment issues (partial export,
locale-dependent CSV artifacts in an unrelated test file) are the more likely source.

**Round 5 caught that this section itself had gone stale — a self-inflicted repeat of the
exact accuracy problem this section exists to guard against.** Round 4's own fix commit added
8 tests *after* the number above was recorded, bringing the true count to 1472 passed / 1505
total — which is exactly what the round-4 PR description correctly reported. But this
design.md section was never updated to match, so by round 5 it still said "1464/1497" while the
PR description said "1472 passed... 33 deselected" — two numbers that don't arithmetically
reconcile (`1472 + 33 = 1505 ≠ 1497`) if read together, which is exactly what round 5 flagged.
Neither number was fabricated; the PR description was current, this section was stale.

Re-ran the exact CI invocation again for round 5, after round 5's own additions (all-NaN
`resolve_trait_columns` coverage, the `n_traits=65` pagination boundary, `extra="forbid"`
tests): **1479 passed, 33 deselected, 1512 total** (`1479 + 33 = 1512` — checked), 0 failed.
This section will need updating again if a future round adds tests without updating it here —
the fix going forward is to update this number in the *same commit* that changes the test
count, not to treat it as a one-time snapshot.

**Round 6: the full-suite claim itself doesn't reproduce reliably across environments, for
reasons outside this PR's control — the claim needs hedging, not just a fresh number.** An
independent fresh-clone run of the exact stated invocation showed 47 failures, all in
`test_umap_analysis_tool.py` (a file this PR does not touch), matching the same
environment-drift signature already flagged once before (round 4: a different reviewer
environment showed ~59 UMAP-unrelated failures the review itself attributed to a locale/CSV
artifact). Re-running `test_umap_analysis_tool.py` in isolation here gives a clean 69/69 — this
environment cannot reproduce the failure either way, consistent with it being genuine
cross-environment nondeterminism (most plausibly BLAS/LAPACK backend or thread-count
differences affecting `scipy.sparse.linalg.eigsh`'s eigensolver fallback path, which
`test_degenerate_small_n_neighbors_eigensolver_failure_is_assumption_violated`'s own captured
warning already shows this suite exercises) rather than anything this diff introduces or could
fix — UMAP code is untouched by #466.

Given that, the **portable, reliably-reproducing claim is the isolated suite of files this diff
actually touches** (the 4 tool-specific files + `test_viz_tools.py` + `test_plots_helpers.py` +
`test_pca_analysis_tool.py`/`test_clustering_tool.py`, the latter two covering the
`_qc_shared`/`resolve_trait_columns` dedup backport): **313 passed, 0 failed**, reproduced
across every round of this review. The full-suite number is still recorded below for
completeness, but should be read as "clean in this environment as of this commit," not as an
unconditional, environment-independent guarantee the way earlier rounds implicitly presented
it: **1488 passed, 33 deselected, 1521 total** (`1488 + 33 = 1521` — checked).

## Incidental Fix

`_qc_shared._validate_trait_subset`'s `require_certified=True` duplicate check (used by
`pca_analysis`/`clustering`) was backported from O(n²) (`.count()` inside a comprehension over
the same list) to O(n) (`collections.Counter`) — the identical fix this change already made in
the new `_viz_shared.resolve_trait_columns` for the same cylinder-scale (~846-trait) motivation
(#466 review round 3 suggestion). Behavior-preserving (same duplicate set, same error), covered
by `pca_analysis`/`clustering`'s existing `test_duplicate_trait_columns_is_invalid_input_naming_
them` tests — no new test needed.

## Round 6: Documentation-Accuracy Corrections

Three more corrections round 6 found, none behavior-affecting:

- **"Delegates 100% of figure rendering" overstated `plot_correlation_matrix`'s actual
  behavior.** It calls `Figure.text(...)` directly on the delegate's returned `Figure` to draw
  the `heatmap_caveat` footnote (round 4). Softened in both the module docstring and this
  change's spec.md requirement to distinguish *chart-element* rendering (fully delegated, as
  claimed) from a plain text annotation added afterward (not delegated, and never was claimed
  to be — the blanket "100%"/"all figure rendering" wording just didn't draw that line).
- **A committed test docstring made a factually wrong claim about
  `create_trait_boxplots_by_genotype_batched`** ("does not pad incomplete pages with blank
  axes, unlike `create_trait_histograms_batched`"). Verified directly against the live delegate
  across several remainders: it DOES pad, identically in spirit to the histograms delegate —
  its grid sizing is just *adaptive* rather than fixed at 4 columns, so remainders of 1, 2, or 3
  happen to fit its chosen grid exactly (no padding), while remainders of 5 or 8 do not (padded
  to an 8-slot grid). The wrong claim came from generalizing off the single `n_traits=65` case
  (remainder 1, an exact fit) tested in round 5. Corrected, and `n_traits=69` (remainder 5, a
  case confirmed to pad) added to the boxplots test's parametrization so the invisible-axes
  filter is genuinely exercised for this tool, not merely applied "just in case."
- **The PR description's CI-failure count needs to track current state, not a stale
  snapshot.** At round 5, 2 checks were failing (both traced to a pre-existing runner
  disk-headroom regression, #334, unrelated to this diff); by round 6 only 1 still was — the
  description is corrected to match current state each time it's updated, not left describing
  an earlier snapshot.

Also renamed `test_delegates_rendering_and_never_calls_vendored_cleanup` (in
`test_plot_correlation_matrix_tool.py`) to `test_delegates_rendering_exactly_once`: its body
only ever asserted the delegate call count, never anything about vendored cleanup — the
"never calls the vendored `bloom_mcp.data_cleanup`" guarantee is structural (this module has
no import of it at all) rather than something a runtime spy on an unrelated module would
meaningfully test here, so the name is corrected rather than the test body padded out to match
a claim it was never really positioned to verify.
