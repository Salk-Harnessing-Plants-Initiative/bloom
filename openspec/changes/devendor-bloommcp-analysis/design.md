# Design

## Context

bloom-mcp accreted three tool-registration styles over Phase 1 → Phase 2:

1. **Legacy workflows** (`tools/workflows/*`, `mcp.tool()`) over **vendored** copies of
   `sleap-roots-analyze`.
2. **Granular contract tools** (`tools/*_tool.py`, `@as_mcp_tool`) that delegate to
   upstream — the correct pattern.
3. **Sections sub-servers** (`sections/*`, Benfica's `2026-06-29` design) — a per-package
   FastMCP instance, one file per tool, auto-mounted namespaced into the combined `/mcp`
   surface and also served at its own URL.

This change deletes generation (1) and its vendored backing, and converges the survivors
of (2) onto (3). The evidence base is a three-agent audit on `origin/staging`:
per-file classification, upstream symbol-parity, and test-coverage mapping.

## Goals / Non-Goals

**Goals:** one analysis source of truth (`sleap-roots-analyze`); zero vendored analysis
code in bloom-mcp; one tool-organization standard (`sections/`); provable no-functionality-
loss; behavior-preserving for the surviving tools.

**Non-Goals:** building `sleap-roots` extraction tools; reorganizing plumbing; re-adding
the intentionally dropped capabilities; changing the contract layer, ports, or storage.

## Key Decisions

### D1 — Delete vendored copies rather than repoint-and-keep

The parity audit proves upstream `0.1.0a4` is a strict superset of every vendored module,
with no bloom-mcp-specific logic in any of them. Keeping repointed shims would preserve
the duplication risk (drift, filename collisions). Deletion + repointing consumers to
`sleap_roots_analyze` is both simpler and an *upgrade* (picks up `#118` fixes and later
plotting features). **Trade-off:** consumers that pulled a symbol from the submodule (not
`__all__`) must use the submodule path — specifically `convert_to_json_serializable` via
`sleap_roots_analyze.data_utils`.

### D2 — `cross_experiment_correlations` + `correlation_tools` deleted together, not rewired

This is the **one** module that is *not* drop-in compatible: upstream
`cross_experiment_analysis.py` shares function names with the vendored
`cross_experiment_correlations.py` but has a different contract (columns, `min_samples`
semantics, significance flags, sort order). Rewiring `correlation_tools` to upstream would
silently change numeric output. Since `correlation_tools` is a known duplicate and its
capability is being intentionally dropped, deleting both is the safe, honest move — no
re-port, no silent regression. Documented so a future re-add uses upstream's contract
deliberately.

### D3 — `sleap_roots` as a family *umbrella* section (bends the one-section-per-package rule)

Benfica's design frames a section as wrapping "one package." These tools wrap
`sleap-roots-analyze`, but the user's model is the root-phenotyping *pipeline*
(extraction via `sleap-roots` → analysis via `sleap-roots-analyze`). Naming options were:

- `sleap_roots_analyze` — accurate but narrow (excludes future extraction).
- `sleap_roots_traits` — **rejected**: collides with the separate `sleap-roots-traits`
  pipeline repo, which these tools do **not** wrap.
- `sleap_roots` (chosen) — the family umbrella. Honest *because* it is explicitly an
  umbrella spanning the family, resolving the "implies one package" objection.

Structure:

```
sections/sleap_roots/
├── __init__.py            # section sub-server + register()
├── analysis/              # wraps sleap-roots-analyze  (populated here)
│   ├── pca_analysis.py  qc_clean.py  qc_inspect.py  remove_outliers.py
│   └── plot_*.py         # 5 surviving plotting tools, one file each
└── extraction/            # reserved slot for future sleap-roots tools (empty here)
```

Tool prefix becomes `sleap_roots_<tool>`. **This bends the convention, so it requires
Benfica's sign-off** (folded into the required heads-up). Fallback if he objects:
package-scoped `sleap_roots_analyze` section now + a separate `sleap_roots` extraction
section later.

### D4 — Cross-cutting discovery tools live in a `core` section, not `sleap_roots`

`list_available_experiments`, `load_experiment_data`, and `list_existing_analyses` are
discovery/IO over the experiment store + result store — **not** `sleap-roots-analyze`
wrappers. Putting them in `sleap_roots` would misrepresent them. A small `core` section
holds them (they remain always-on / `ALWAYS_INCLUDE_MCP_TOOLS`). Their logic already lives
in `experiment_utils` + the result store; the section files are thin registration shims.
`inspect_data_quality` is dropped as redundant with `qc_inspect`.

### D5 — Viz survivors: 5 standalone plots, 2 dropped

`viz_tools` exposes 7 plotting tools. Five are standalone plots delegating to
`sleap_roots_analyze.visualization`/`statistics` (histograms, boxplots, correlation matrix,
heritability bar, variance decomposition) → they survive, repointed to upstream. Two do not:
`plot_dendrogram` *computes* hierarchical clustering (a dropped capability, and its lazy import
at `viz_tools.py:347` must be caught in the repoint), and `plot_outlier_comparison` reads JSON
that only the retired outlier workflow produced (no input source post-retirement). Both are
**dropped** and return later co-located with the granular clustering / outlier tool that owns
them — keeping "clustering/outlier dropped" consistent rather than shipping orphan tools that
quietly do clustering/outlier work. (`plot_pca_scree`/`plot_pca_biplot` never existed — a stale
comment — and vanish with the dimred workflow.)

### D6 — Dependency prune + oracle's test-only scikit-learn

After deletion, `scipy`, `scikit-learn`, and `seaborn` have no shipped importer (only the
deleted vendored modules used them); `matplotlib` survives (the 5 viz tools use it). So all
three are pruned from runtime `dependencies` and `test_retained_heavy_deps_are_each_imported`
is cut to `{matplotlib}`. `test_oracle.py`'s UMAP-trustworthiness layer imports
`sklearn.manifold.trustworthiness` to *measure* upstream `perform_umap_analysis` output — a
test-only use — so `scikit-learn` moves to the `[project.optional-dependencies].test` extra
(kept out of the prod image via `uv sync --no-dev`, per the repo convention enforced by
`test_ci_workflow_uv_conventions.py`) rather than being deleted outright. The k-means and
cross-experiment-correlation *shipped-code* oracle tests are deleted (capabilities dropped;
upstream correlation contract differs, so its literal would not reproduce); the PCA shipped
assertion folds into the existing cross-tier PCA test.

### D7 — Two phases, one change, same PR discipline

Phase 1 (delete + repoint + retire) is behavior-preserving except the approved capability
drops and is independently valuable — it removes the broken workflows and the drift risk.
Phase 2 (migrate to sections) is a pure reorganization. Splitting into two PRs on the same
OpenSpec change keeps each review tractable and lets Phase 1's safety land first. The
proposal + Phase 1 code go in the first PR (never proposal-only).

## Risks / Mitigations

- **Silent numeric change from correlation rewire** → mitigated by D2 (delete, don't rewire).
- **`convert_to_json_serializable` import-path trap** (not in upstream `__all__`) →
  call sites use the `.data_utils` submodule path; asserted by the delegation test.
- **Server boot break mid-deletion** (a registered tool importing a just-deleted module) →
  Phase 1 deletes vendored modules and their consumers *atomically* (delete + deregister in one
  commit); the wheel-import gate (`import bloom_mcp.server` + `build_app()`) and a
  `test_server_boots_after_devendor` subprocess test catch any dangling import. Delegation-guard
  spies must drop the *import binding* (not just the assertion) in the same commit as the delete.
- **Oracle collapse** — `test_oracle.py` imports the vendored modules at collection time, so
  deletion breaks the whole file → remove those imports; **delete** the k-means + correlation
  shipped-code tests (dropped capabilities; upstream correlation contract differs so `0.97894…`
  would not reproduce); fold the PCA shipped assertion into the cross-tier PCA test (cumulative
  `0.95991`/`n=3` retained); keep the UMAP cross-tier test via a test-only `scikit-learn`.
- **False-green `--strict`** — validation does not cross-check that deleted code is still
  referenced by *other* live specs. Deleting workflows + `correlation_tools` invalidates live
  requirements in `bloommcp-result-store` and `bloommcp-experiment-read` → this change ships
  explicit REMOVED/MODIFIED deltas under both, or the archived spec set self-contradicts.
- **Namespacing breaks name-matching lists** — `ALWAYS_INCLUDE_MCP_TOOLS` and `HIDDEN_TOOLS`
  match tool names verbatim and hold *unprefixed* names + `inspect_data_quality`; Phase-2 `core_*`
  namespacing would silently break the always-on / hidden guarantees → update them (drop the
  dropped tool, make matching prefix-aware) and add a drift-guard test asserting the lists match
  the live registry.
- **Lost coverage on repointed viz tools** → add through-the-MCP delegation + golden tests for
  the 5 survivors *before/with* the repoint. Fixture wiring is `monkeypatch TRAITS_DIR`/`PLOTS_DIR`
  + `fake_supabase_storage` (the established reader-test pattern) — **not** `BLOOM_STORAGE_BACKEND=local`,
  which only reroutes the write path, not viz's `load_experiment_data` read. Include figure-leak +
  heritability-tolerance guards and pin one correlation-matrix cell.
- **Coordination / bending Benfica's convention** → this doc is the heads-up; his review is
  required; D3 names a fallback if he objects.

## Migration / Rollout

No data migration. Tool-surface change only. The intentionally dropped tools disappear from
`tools/list`; the LangChain agent discovers dynamically so no agent code change is required
(the stale `CONTEXT_MCP` hand-list is trimmed as hygiene). Existing persisted analysis runs
are unaffected — `storage_tools`/result-store keep the historical tool-class names.
