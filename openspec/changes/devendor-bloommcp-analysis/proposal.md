## Why

_(Change-ID note: `devendor-bloommcp-analysis` names Phase 1's de-vendoring; the ID
covers Phase 2's `sections/` reorganization too, by the deliberate one-PR-pair,
one-change choice in `design.md`'s D7 — not a scope mismatch.)_

bloom-mcp currently ships **two tool generations side by side**, both duplicating
`sleap-roots-analyze`:

1. **A vendored copy of the analysis/plotting library.** Eight modules under
   `src/bloom_mcp/` (`pca.py`, `clustering.py`, `cluster_visualization.py`,
   `outlier_detection.py`, `outlier_visualization.py`, `visualization.py`,
   `data_cleanup.py`, `cross_experiment_correlations.py`) are near-verbatim copies of
   `sleap_roots_analyze` modules. A symbol-parity audit against the pinned upstream
   (`0.1.0a5`, re-confirmed live against the installed package after this branch was
   rebased onto `staging`) confirms **every symbol these files define exists upstream by
   the same name**, upstream is a strict superset (the vendored copies are an older
   snapshot, missing the `#118` `random_state` fixes and later plotting improvements),
   and **none of them contain any bloom-mcp-specific logic** (no file paths, plot URLs,
   or MCP envelopes — those all live in the `tools/` wrappers). They are pure
   duplication. (This vendored `clustering.py` module is distinct from the **granular**
   `clustering` tool added since by #309/#422 — see below; the vendored copy has no
   surviving importer and is deleted same as the other seven.)

2. **A legacy "workflow" tool layer** (`tools/workflows/{qc,outlier,stats,dimred,
   clustering}.py`) registered via `mcp.tool()`. These are the *sole consumers* of the
   vendored modules, they predate the contract layer, and two of them are **documented
   as broken**: `local-validation.md` records that `run_dimensionality_reduction_workflow`
   (PCA) raises a bare `KeyError: 'explained_variance_ratio'` and
   `run_outlier_workflow(method="pca")` crashes on a bad `threshold_percentile`
   pass-through.

Meanwhile Phase 2 already established the right pattern: granular contract tools
(`qc_clean`, `qc_inspect`, `pca_analysis`, `remove_outliers`, and — landed on `staging`
after this proposal was first drafted — `clustering`, #309/#422) that delegate to
`sleap-roots-analyze` and carry `Provenance`, and Benfica's new `sections/` sub-server
layout (design doc `2026-06-29-bloom-mcp-contributor-namespacing.md`). The de-vendoring
of heritability/UMAP already landed under #315 — this change finishes the job for the
remaining analysis/plotting surface. **Branch note:** this change's branch was rebased
onto `staging` before implementation started, incorporating `clustering` (and the
`_consumer_utils` refactor, #434) as a fifth surviving granular tool — it was not part
of the original audit and needs no Phase-1 work (it already delegates to
`sleap_roots_analyze` with zero vendored imports), but Phase 2 must migrate it into
`sections/sleap_roots/analysis/` alongside the other four.

The result today is confusing (three registration styles, filename collisions like
`bloom_mcp/clustering.py` vs `tools/workflows/clustering.py`), unsafe (untested vendored
copies drift from the tested upstream, and some are already broken), and violates the
intended architecture: **bloom-mcp should be a thin MCP surface over `sleap-roots-analyze`,
never a second home for analysis code.**

## What Changes

Landed as **one OpenSpec change in two phases** (Phase 1 with this proposal; Phase 2 a
code-only PR on the same branch/change).

**Phase 1 — de-vendor + retire (behavior-preserving except intentional drops):**

- **DELETE the eight vendored analysis/plotting modules** (`pca`, `clustering`,
  `cluster_visualization`, `outlier_detection`, `outlier_visualization`, `visualization`,
  `data_cleanup`, `cross_experiment_correlations`) **plus the partially-vendored
  `data_utils.py`** — nine files total — and repoint their surviving consumers to
  `sleap_roots_analyze`. The only helper a surviving tool still needs,
  `convert_to_json_serializable`, exists upstream — repoint its call sites to
  `from sleap_roots_analyze.data_utils import convert_to_json_serializable`.
- **DELETE `tools/correlation_tools.py` together with `cross_experiment_correlations.py`.**
  This is the one module whose upstream twin (`cross_experiment_analysis.py`) has a
  **different contract** (different columns, `min_samples` semantics, no significance
  flags, different sorting), so a naive rewire would silently change numbers. Deleting
  both — rather than rewiring — is the safe move, and `correlation_tools` is a known
  duplicate slated for removal.
- **RETIRE the Phase-1 workflow tools** (`tools/workflows/*` incl. `_helpers.py`,
  `_response.py`) and their `server.py` registrations.
- **REPOINT** `viz_tools` and the surviving discovery tools off the vendored modules onto
  `sleap_roots_analyze` (all imported symbols verified present upstream). `viz_tools` keeps
  its **5 standalone plotting tools** (histograms, boxplots, correlation matrix, heritability
  bar, variance decomposition), now backed by tested code; **`plot_dendrogram` and
  `plot_outlier_comparison` are dropped** (the first computes clustering, the second reads the
  retired outlier workflow's output — each returns later with the granular clustering / outlier
  tool that owns it).
- **Prune** the runtime dependencies that become unimported after deletion: remove `scipy`,
  `scikit-learn`, and `seaborn` from runtime `dependencies` (their only importers were the
  deleted modules), move `scikit-learn` to the `[test]` extra (only the oracle imports it), and
  retain `matplotlib` (the 5 viz tools use it). Re-sync both lockfiles. This completes the
  documented **non-blocking follow-up** that #315 recorded when it closed (its closing note
  states #305 AC5 was already reconciled and the heavy viz deps were carried forward as a
  follow-up, not a blocker) — it does not reopen #315.

**Phase 2 — converge tool organization on `sections/`:**

- **MIGRATE** the surviving granular tools — `pca_analysis`, `qc_clean`, `qc_inspect`,
  `remove_outliers`, and `clustering` (the last landed on `staging` via #309/#422 since
  this proposal was first drafted; it already delegates to `sleap_roots_analyze` with no
  vendored import, so it needs no Phase-1 work, only the Phase-2 move) — into a new
  **`sleap_roots` umbrella section** (`sections/sleap_roots/analysis/…`, one file per
  tool), splitting the 5 surviving plotting tools into one-file-per-tool. Reserve a
  `sections/sleap_roots/extraction/` slot for future `sleap-roots` trait-extraction tools
  (**not built here**).
- **MOVE** the cross-cutting discovery tools (`list_available_experiments`,
  `load_experiment_data`, `list_existing_analyses`) into a small **`core` section**; they
  are not `sleap-roots-analyze` wrappers. **DROP `inspect_data_quality`** (redundant with
  `qc_inspect`).
- **LAND the #412 fix** (report `dropped_constant_traits` on `pca_analysis` instead of
  raising `assumption_violated`) as its own commit **before** `pca_analysis_tool.py`
  moves, so Phase 2 relocates the corrected file verbatim (see "#412 same-file
  dependency" below — its original "land the #412 PR to staging first" premise no longer
  applies; the fix must be implemented directly in this change).

**Intentionally dropped capabilities** (user-approved; re-add later as thin section tools
if wanted): UMAP embedding, cross-method outlier-comparison plot, descriptive stats
tables, cross-experiment correlations. (Clustering is **no longer** in this list — #309/
#422 shipped it as a surviving granular tool while this proposal was in flight; see the
branch note above.)

## Non-Goals

- **No new trait-extraction tools.** The `sleap_roots` section is an umbrella; this change
  populates only its `analysis/` subgroup. Wrapping the `sleap-roots` extraction library
  is future work (and a new dependency bloom-mcp does not yet declare).
- **No plumbing reorganization.** `server.py`, `auth.py`, `supabase_client.py`,
  `storage_backend.py`, `experiment_utils.py`, and the `contract/` `data_access/`
  `result_store/` `storage/` subpackages are genuine bloom-mcp infra (not vendored) and
  stay as-is. (`experiment_utils` alone has 15 importers; moving it is churn without
  payoff here.) The `storage_backend.py` vs `storage/` name collision is noted for a
  future cleanup, not fixed here.
- **No re-implementation of the dropped capabilities** in this change.

## Impact

- **Specs:** `bloommcp-packaging` (extend the delegation invariant to *all* analysis/
  plotting; update the cross-tier oracle). New capability `bloommcp-tool-sections` (sections
  organization, `sleap_roots` umbrella, `core` section, Phase-1 workflow retirement).
  **`bloommcp-result-store`** — REMOVE "Workflows Repointed to the ResultStore Port" and
  MODIFY the "Write consumers" scenario + the Live Supabase Persistence Smoke (it drove a
  workflow; repoint to a surviving tool). **`bloommcp-experiment-read`** — MODIFY the
  consumer-inspection scenarios that name `correlation_tools` / `tools/workflows/*`. (These
  cross-capability deltas are required: `--strict` does not cross-check that deleted code is
  still referenced by other live specs.)
- **Tool surface (agent-visible):** removes 5 workflow tools + 8 correlation tools +
  `inspect_data_quality` + 2 viz plots (`plot_dendrogram`, `plot_outlier_comparison`); the
  granular tools become namespaced under their sections (e.g. `sleap_roots_pca_analysis`).
  The LangChain agent discovers tools dynamically (`chat.py` filters the live `mcp_tools`), so
  tools vanish automatically — **but** the two *name-matching* code lists
  (`ALWAYS_INCLUDE_MCP_TOOLS` in `chat.py`, `HIDDEN_TOOLS` in `web/…/mcp-chat-client.tsx`) hold
  unprefixed names + `inspect_data_quality` and must be updated (drop the dropped tool; make
  matching prefix-aware) or the Phase-2 `core_*` namespacing silently breaks the always-on /
  hidden guarantees. The `CONTEXT_MCP` prompt list is trimmed to routing guidance.
- **DRY / drift:** the tool catalog is hand-duplicated across ~5 places (`server.py` docstring,
  `CONTEXT_MCP`, `ALWAYS_INCLUDE_MCP_TOOLS`, `HIDDEN_TOOLS`, wikis). This change does the
  **correctness-only** fixes and adds a **drift-guard test** (hand-lists must match the live
  registry); the deeper single-source-of-truth refactor (tag tools at definition + derive the
  sets prefix-aware) is filed as a fast-follow, not bundled into this PR.
- **Dependencies:** `scipy`, `scikit-learn`, `seaborn` pruned from runtime deps;
  `scikit-learn` → `[test]` extra; `matplotlib` retained. `test_retained_heavy_deps_are_each_imported`
  reduced to `{matplotlib}`; dangling `per-file-ignores` removed; both lockfiles re-synced.
- **Docs:** `roadmap.md` reconciled — this performs the vendored-`source/*` + `run_*_workflow`
  retirement the roadmap gates on **"after Stage 1 (Tiers 0–4) lands"** (confirm Stage 1 has
  landed before relying on the trigger; update the roadmap's stale ⬜ status). The **vendored**
  clustering path is removed; the **granular** clustering tool that was to be #309's re-add path
  has since **landed** (#309/#422, merged onto `staging`) and is folded into this change's Phase-2
  migration (see above) rather than staying a future tracker — mark #309/#422 done in the roadmap,
  not "stays open." `local-validation.md` + `bloommcp/scripts/live_persistence_smoke.py` repointed
  off `run_clustering_workflow`; `server.py` docstring + `_WIKI` + top-level `README.md` catalogs
  updated; the missing `2026-06-29-…-namespacing.md` design doc created (or its references
  repointed); golden-fixture `_reproduced_by` provenance strings updated to `0.1.0a5` (the
  branch's actual current pin post-rebase, bumped by the unrelated #445).
- **Tests:** shipped-code oracle layer folded/deleted (PCA folds into cross-tier; k-means +
  correlation shipped tests deleted with their capabilities; UMAP cross-tier kept via a
  test-only `scikit-learn`), 4 delegation-guard spies lose their vendored-import half,
  workflow-presence assertions replaced by positive absence + exact-surface tests, new
  through-the-MCP golden tests for the 5 viz tools + discovery tools, server-boot-after-devendor
  test. **"No functionality lost" is a first-class acceptance criterion:** the parity test
  freezes the pre-deletion imported-symbol set and asserts each exists upstream.
- **Coordination:** deletes Benfica's (`blm3886`) vendored + workflow layer and bends his
  one-section-per-package convention (the `sleap_roots` umbrella spans the family). This
  proposal doubles as the heads-up artifact; his non-author review is required before merge,
  and he may know an off-repo caller (Claude Desktop config, demos) to account for.
- **#412 same-file dependency (resolved by this change, not by waiting):** #412 (report
  `dropped_constant_traits` instead of raising `assumption_violated`) is closed, but **not** by a
  bloom-mcp PR — it was closed by the upstream fix `talmolab/sleap-roots-analyze#178` (merged
  2026-07-10, released in `0.1.0a5`, 2026-07-14). The upstream fix's own "Incorporation into
  bloom-mcp" checklist (surface `reason=zero_variance`/report instead of raise, reconcile the
  now-possibly-unreachable guard, update tests) was never started on the bloom-mcp side:
  `pca_analysis_tool.py` still unconditionally raises `assumption_violated` on a dropped constant
  trait (verified directly against the merged `staging` tip). There is **no pending #412 PR to
  land** — the original "land #412 first, then move the file" sequencing assumed one existed.
  Corrected sequencing: **this change implements the fix itself**, as its own commit in Phase 2
  (before `pca_analysis_tool.py` moves — see P2.0 in `tasks.md`), so Phase 2's move relocates the
  already-corrected file verbatim. A golden/drift test asserts the `dropped_constant_traits` field
  and the no-raise behavior survive the migration.
- **Related issues:** #308 (pca_analysis, aligned), #338/#356 (qc_clean, aligned), #309/#422
  (granular clustering — **CLOSED/MERGED** onto `staging` via PR #427/#445 while this proposal was
  in flight; `clustering_tool.py` already ships and is folded into this change's Phase-2 migration
  scope, not a future re-add path), #315/#305 AC5 (dep follow-up completed), #412 (above), #406
  (per-user identity + sections/usage — Phase 2 touches the sections surface), #426 (open — adds
  scree/biplot/variance plots to `pca_analysis`; same-file touch on `pca_analysis_tool.py`/its
  Phase-2 destination alongside the #412 fix and the P2.2 move — no conflict expected since #426 is
  unstarted, but worth a heads-up to whoever picks it up next).
- **Dropped-capability re-adds — reference existing trackers, don't duplicate:** UMAP has **#425**
  (`umap_analysis` granular tool). (Clustering is no longer a dropped capability — #309/#422 already
  shipped it; see above.)
- **Follow-up issues to file (genuinely new):** the DRY single-source-of-truth tool-catalog refactor;
  the `storage_backend.py` vs `storage/` name-collision cleanup; and trackers for re-adding the
  **cross-method outlier-comparison plot**, **descriptive-stats tables**, and **cross-experiment
  correlations against upstream's contract** (per D2) — so "dropped" leaves a paper trail and does
  not silently become "lost." (A bloommcp-architecture follow-up such as #344/#434 may host these.)
