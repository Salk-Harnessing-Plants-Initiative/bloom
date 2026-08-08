## Why

Every bloommcp analysis tool implicitly reads the **latest** trait source and the **latest**
cleaned version, with no way to override either — even though the underlying mechanisms for
both already exist and are simply unwired. `SourceSelectable.list_sources()`/`resolve_source()`
already exist on `SupabaseReader` (Tier 2, PR #557), backed by the `list_experiment_trait_sources`
RPC (Tier 1, #546), but zero MCP tools expose source selection. `ExperimentReader.load_experiment`
already accepts an explicit `version="v<N>"`, but none of the 6 `require_clean=True` tools pass
one. This was surfaced while investigating #625 and is tracked as issue #626.

## What Changes

- **Extend the `ExperimentReader` Protocol** (`ports.py`) to declare `source_id`/`run_id` on
  `load_experiment` (today only `SupabaseReader`'s concrete implementation has them — the
  Protocol lags behind). Add a new `SourcePinningUnsupportedError`; `LocalReader` and `FakeReader`
  gain the two kwargs and raise it immediately when either is non-`None` (today they don't accept
  the kwargs at all, so passing one is a bare `TypeError`).
- **New discovery tool `core_list_experiment_sources(experiment)`** — a thin, isinstance-gated
  wrapper over `SourceSelectable.list_sources()`. Returns each source's `source_id`/`source_name`/
  `pipeline_run_id`, a distinct message when the experiment has zero or one source (no meaningful
  choice), and a "not applicable for this backend" message (not an error) on `LocalReader`/
  `FakeReader`.
- **`qc_clean` accepts an explicit source pin** — optional `source_id`/`run_id` params, threaded
  into its existing raw-tier read. Omitting both is behavior-identical to today. When the
  experiment has more than one source and neither was pinned, the response now says so explicitly
  (e.g. "3 sources available, used latest (source_id=7); call core_list_experiment_sources to
  choose a different one").
- **`qc_inspect` and `load_experiment_data` accept the same source pin** — same params, same
  threading, same default-preserving guarantee. No response-text change for these two (the issue
  calls that out for `qc_clean` only).
- **The 6 `require_clean=True` tools** (`clustering`, `descriptive_stats`, `pca_analysis`,
  `umap_analysis`, `remove_outliers`, `cross_experiment_correlations`) each accept an optional
  cleaned-version selector, threaded to `load_experiment(..., require_clean=True, version=...)`.
  Omitting it reproduces each tool's **current** default exactly — `remove_outliers` defaults to
  `"latest_qc"` today (not the Protocol's `"latest"`), so its new field must preserve that, not
  silently switch defaults. `cross_experiment_correlations` reads two independent experiments, so
  it gets two independent fields (`version_1`/`version_2`), matching its existing `_1`/`_2`
  per-experiment field convention. `pca_analysis` already has an archived capability spec
  (`bloommcp-pca-analysis-tool`) — its selector lands there as a **MODIFIED** delta, not lumped in
  with the other 5 tools' brand-new capability (an earlier draft of this proposal mistakenly
  treated all 6 as baseline-less; caught in review).
- Passing both `source_id` and `run_id` anywhere raises the already-implemented
  `AmbiguousSourceSelectionError` — this change adds tool-layer test coverage for that, not new
  logic.

**Not changing**: the `SupabaseReader`/`SourceSelectable` pinning mechanism itself (PR #557,
already shipped) — this proposal only wires already-built mechanisms into tool surfaces. No DB
migration. No shared code or migration dependency with sibling issue #625 (already merged via PR
#628) — cross-reference only, sequenced independently.

## Impact

- **Affected specs**:
  - `bloommcp-experiment-read` (MODIFIED) — `ExperimentReader` Protocol signature + adapter
    rejection contract + the `SourcePinNotFoundError` scenario Tier 2 (PR #557) shipped without
    ever getting spec coverage.
  - `bloommcp-qc-clean-tool` (ADDED — new requirement inside the existing capability) —
    `qc_clean`'s new params + response text + a provenance-traceability scenario.
  - `bloommcp-pca-analysis-tool` (MODIFIED) — `pca_analysis` already has an archived baseline
    spec; its version selector modifies the existing "Requires a Cleaned Input" requirement
    rather than joining the new cross-cutting capability below.
  - `bloommcp-source-selection` (ADDED, new capability) — the new discovery tool, plus
    `qc_inspect`/`load_experiment_data` source pinning. (Both tools currently have no archived
    baseline spec of their own in `openspec/specs/` — their own change proposals,
    e.g. `add-bloommcp-qc-inspect-tool`, are still open/unarchived, a pre-existing repo-wide
    archive backlog unrelated to this change. Grouping their source-pinning behavior here avoids
    inventing a `MODIFIED` delta against a nonexistent baseline.)
  - `bloommcp-clean-version-selection` (ADDED, new capability) — the version-selector wiring
    across the **5** `require_clean=True` tools without an archived baseline
    (`clustering`, `descriptive_stats`, `umap_analysis`, `remove_outliers`,
    `cross_experiment_correlations` — `pca_analysis` is excluded, see above).
- **Affected code**: `bloommcp/src/bloom_mcp/data_access/ports.py` (including its `__init__.py`
  re-exports — the new `SourcePinningUnsupportedError` needs adding there too),
  `data_access/local_reader.py`, `data_access/fake_reader.py`, `sections/core/` (new module +
  `__init__.py` registration), `sections/sleap_roots/analysis/{qc_clean,qc_inspect,clustering,
  descriptive_stats,pca_analysis,umap_analysis,remove_outliers,cross_experiment_correlations}.py`,
  `tools/_ports.py`. `sections/phenotyping_segmentation/summarize_trait.py` also calls
  `_ports.load_frame` and is therefore a third consumer of the widened seam — it needs no code
  change (the new kwargs are additive and default to `None`) but is noted here so "affected code"
  is complete. `bloommcp/tests/test_devendor_invariants.py::test_expected_tool_surface` hardcodes
  the `core_*` tool enumeration and needs updating to include `core_list_experiment_sources`, or it
  silently stops verifying the live tool surface.
- **Test fixtures**: `FakeReader`'s test double has zero multi-source fixtures today, but it also
  deliberately does **not** implement `SourceSelectable` (locked in by
  `test_supabase_reader.py::test_fake_reader_is_not_source_selectable`) — this change does not
  change that. Multi-source tool-layer tests instead extend the existing
  monkeypatched-`SupabaseReader`-boundary fixture pattern `test_supabase_reader.py` already uses
  for Tier-2 multi-source coverage (see design.md Decision 5).
