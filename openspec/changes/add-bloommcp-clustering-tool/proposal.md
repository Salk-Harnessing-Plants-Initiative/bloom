## Why

bloom-mcp Phase 2 has landed the contract layer (Tier 1 / #306), the persistence ports (Tier 2 /
#307), the QC **producer** `qc_clean` (Tier 3 / #338), and the **first granular consumer**
`pca_analysis` (Tier 4 / #308): a deterministic tool that runs PCA on a `require_clean=True`
cleaned run. Every granular tool shipped so far is **deterministic** — `qc_clean`, `remove_outliers`
(its seed drives a detector but the result is a trimmed table, not a stochastic fit), and
`pca_analysis` all record either a detector seed or `seed = None`. **No tool yet exercises the
contract's seed-resolution path for a genuinely stochastic _fit_ whose output depends on the seed.**

Tier 5 (#309, renumbered from Tier 4 by #339) adds `clustering`: a **polymorphic** consumer that
partitions a _cleaned_ experiment into clusters by delegating to `sleap-roots-analyze`'s tested
`perform_kmeans_clustering` / `perform_gmm_clustering` → the typed `KMeansResult` / `GMMResult`
(`from_kmeans_dict` / `from_gmm_dict`). It proves two things the tier sequence is built to prove:

1. **The contract surface generalizes past a single result shape.** PCA has one `PCAResult`;
   clustering dispatches on a `method` to **two distinct** typed results — `KMeansResult` carries
   `cluster_centers` / `inertia`, `GMMResult` carries `bic` / `aic` / `weights` / `converged` /
   `covariance_type` — over a shared `ClusterResult` base (`cluster_labels`, `cluster_sizes`, the
   three internal-validation scores). One tool, one contract envelope, polymorphic payloads.
2. **The seed path is real here.** k-means and GMM both consume `random_state`, so — unlike
   `pca_analysis` (which records `seed = None`) — `clustering` **declares a `random_state` parameter**,
   the contract resolves the requested `seed` into it, and the stamped `Provenance` records the
   **resolved seed** that produced the labels. The oracle is **determinism**: same seed → identical
   `cluster_labels`.

This closes the composition `qc_clean` → cleaned versioned run → `clustering` consumes it, alongside
the parallel `qc_clean → pca_analysis` path.

## Scope: kmeans + gmm now; hierarchical is a fast-follow (upstream-gated)

The #309 scope update asks the tier to cover **all three** algorithms `sleap-roots-analyze` exposes
(kmeans, gmm, hierarchical) and recommends designing for all three but **shipping kmeans + gmm
first**. Verified against the currently pinned+locked **`0.1.0a4`** (`bloommcp/pyproject.toml`
already pins `>=0.1.0a4`; `bloommcp/uv.lock` resolves `0.1.0a4`):

| method | status (verified `0.1.0a4`) | evidence |
|---|---|---|
| **kmeans** | ✅ ready | `KMeansResult.from_kmeans_dict(perform_kmeans_clustering(X, n_clusters=3, random_state=42), random_state=42)` → 3 clusters, silhouette `0.417`, deterministic |
| **gmm** | ✅ ready — **the #309 body's "blocked" note is stale** | `GMMResult.from_gmm_dict(perform_gmm_clustering(X, n_components=3, random_state=42), random_state=42)` **works** in `0.1.0a4` (3 clusters, silhouette `0.316`, `converged=True`, deterministic). The issue's claim that `from_kmeans_dict` `KeyError`s on the gmm dict is true — but `0.1.0a4` ships a real **`from_gmm_dict`** that handles the gmm dict's `n_components` key. GMM is **not** blocked. |
| **hierarchical** | ❌ genuinely blocked | `perform_hierarchical_clustering(X)` returns **only** `{linkage_matrix, cophenetic_correlation, linkage_method, distance_metric, feature_names, …}` — **no `cluster_labels`, no `cluster_sizes`, no scores**, and there is **no `from_hierarchical_dict`**. The thin-delegation pattern (one `perform_*` → one `from_*_dict`) has nothing to wrap into a labeled `ClusterResult`. |

So **kmeans + gmm ship now on the current pin with no upstream work and no pin bump**; **hierarchical
is deferred** to a fast-follow gated on an upstream `sleap-roots-analyze` release (`0.1.0a5`) that adds
a public **labeled** entry point (compose `perform_hierarchical_clustering` →
`calculate_optimal_clusters_hierarchical` → cut-tree → `calculate_cluster_quality_metrics` **upstream**,
returning a `ClusterResult`) and makes `ClusterResult.random_state` `Optional[int]` (hierarchical is
deterministic). This change **files/reopens** that upstream issue (`talmolab/sleap-roots-analyze`
#129 is closed-but-incomplete — it shipped working only for kmeans) but does **not** block on it. The
`method` param and dispatch are designed so hierarchical drops in as one `Literal` member + one
dispatch arm with **no retrofit** — including the deterministic `seed = None` path (see design).

## Why require a cleaned input (consume, don't re-clean)

Like `pca_analysis`, `clustering` is a **consumer**: the k-means/GMM delegates standardize and fit
over whatever numeric columns they are handed, dropping NaN-bearing rows internally (their returns
carry `data_indices` / `data_processed` — an uncontrolled sample loss). The whole point of Tier 3
was to clean **first**. So `clustering` **must not** re-cluster raw data: it loads with
`require_clean=True` (the reader satisfies this only from a committed cleaned version) **and selects
only from that version's certified-clean trait set** (`frame.trait_cols`), rejecting any requested
column outside it — the same defense `pca_analysis` uses to make the delegate's internal row-drop a
genuine no-op, so the sample set the fit sees is exactly the one `qc_clean` certified. As
defense-in-depth the tool asserts the selected subset is finite (`np.isfinite(...).all()`) before
fitting.

## What Changes

- **ADD** a granular, polymorphic `clustering` MCP tool: Pydantic input/output models, a tool
  function wrapped by `@as_mcp_tool`, that
  - reads the experiment frame through the injected `ExperimentReader` port with
    **`require_clean=True`**. An experiment with no committed cleaned version raises
    `CleanedVersionRequiredError`, which the tool **catches and re-raises** as a structured
    `BloomMCPError` whose remedy is "run `qc_clean` first" — never a raw backend message and never a
    silent cluster over raw, NaN-bearing data;
  - is **stochastic**: it **declares a `random_state` parameter** and a `seed` input field (default
    `42`, `ge=0`). Per the Tier-1 contract, the resolved seed is injected into `random_state` and
    **recorded in `Provenance`** (`seed = <resolved>`, not `None`) — this is the first tier where the
    recorded seed is the one that produced the result. The **determinism oracle** asserts same
    seed → identical `cluster_labels`;
  - **dispatches on `method`** (`Literal["kmeans", "gmm"]`) to the matching upstream entry point and
    typed wrapper — `perform_kmeans_clustering` → `KMeansResult.from_kmeans_dict` or
    `perform_gmm_clustering` → `GMMResult.from_gmm_dict`. The MCP contains **no clustering math** — no
    standardization, distance computation, EM/Lloyd iteration, or metric computation of its own — and
    does **not** call the vendored `bloom_mcp.clustering`;
  - validates a caller-supplied `trait_columns` subset up front against the reader's
    **certified-clean trait set** (`frame.trait_cols`) and numeric dtype — reusing the shared
    `_qc_shared._validate_trait_subset` helper rather than adding a fourth private copy (a #309
    carry-over) — rejecting empty/duplicate/out-of-set/non-numeric selections with `invalid_input`
    naming the offenders. As defense-in-depth the selected subset is asserted finite before fitting;
  - forwards the per-method cluster-count controls with validated ranges: kmeans `n_clusters`
    (`ge=2`; omit → auto-select up to `max_clusters` by silhouette) and gmm `n_components` (`ge=1`;
    omit → auto-select up to `max_components` by BIC) plus gmm `covariance_type`. A cluster-count
    control set for the wrong method is rejected (`invalid_input`), mirroring `remove_outliers`'
    per-method threshold guard;
  - **persists a versioned run via the `ResultStore` port** under tool class `clustering` — the
    per-sample cluster labels **with sample identity** (`labels.csv`, prefixed with the frame's
    `metadata_cols` so a label row maps back to its plant by a shared key, mirroring
    `pca_analysis`' `scores.csv`) + the serialized typed result (`cluster_result.json`, via
    `to_json`) + provenance — and records **`based_on_version` = the cleaned source version**
    (`frame.source`, e.g. `v3_cleaned`) so the `qc_clean` → `clustering` lineage is recoverable from
    the manifest. It content-addresses the consumed frame via `source_csv` (so `input_sha256` pins
    the exact input bytes, parity with `pca_analysis`). It returns the small cluster summary inline
    (n_clusters, cluster_sizes, the three internal-validation scores, method-specific scalars like
    `inertia` / `bic` / `aic` / `converged`) with `resource_link`s to the artifacts — **never** the
    N-length label vector inline;
  - carries the contract-stamped `Provenance` (`seed = <resolved>`) into the persisted manifest.
- **REGISTER** the tool in `bloommcp/src/bloom_mcp/server.py` so it appears in MCP `tools/list`, and
  add it to the module docstring's "Direct tools (granular)" list.
- **NO pin bump required** — `perform_kmeans_clustering` / `perform_gmm_clustering` are public and the
  typed `KMeansResult` / `GMMResult` + `from_kmeans_dict` / `from_gmm_dict` all work in `0.1.0a4`,
  which is **already pinned and locked** (`>=0.1.0a4`). This tier adds no dependency.
- **LEAVE** the existing `run_clustering_workflow` and the vendored `bloom_mcp.clustering` in place —
  this **adds granularity alongside**; retirement of `source/*` + the bespoke workflow tools is
  **deferred to after Stage 1** (deleting `clustering.py` now breaks the booting server, whose
  `tools/workflows/clustering.py` module-level-imports it).
- Tests cover the **5 contract patterns + the determinism oracle + a per-method characterization
  snapshot through the tool**: determinism (same seed → identical labels — the north star),
  `tools/list` presence, schema round-trip, provenance presence (**seed recorded as the resolved
  value**; same seed → identical), property/invariant (`require_clean` consumption + certified-set
  restriction), the polymorphic-dispatch contract (kmeans vs gmm route to the right delegate + typed
  result), and the structured error envelope.
- **EXTEND** the live persistence smoke (`make bloommcp-smoke`) with a **Tier-5 `clustering` leg**
  that clusters a committed `qc_clean` cleaned version through the **real** `SupabaseReader` /
  `SupabaseResultStore`, proving the cross-tier composition end-to-end.

### Oracle honesty (no independent clustering oracle exists)

Unlike `pca_analysis` (whose cumulative explained variance + `n=3` are the **independent** #120 / PR
#146 oracle recorded in upstream `viz_pca_metadata.json`), **no externally-recorded clustering oracle
exists** for turface_19. So the oracle is:

- **Determinism (the hard invariant):** same seed → **identical** `cluster_labels` for each method.
  This is a genuine correctness property, not a snapshot.
- **A characterization snapshot (a drift gate, not an independent oracle):** a new
  `tests/fixtures/turface_19_clustering_golden.json` records the cluster metrics each method produces
  **through the tool** at a pinned seed on the 8 recorded golden trait columns — kmeans (`n_clusters=3,
  seed=42`): silhouette `0.4170820373`, davies_bouldin `0.7982630887`, calinski_harabasz
  `200.2918882831`, sizes `[40, 85, 28]`; gmm (`n_components=3, covariance_type="full", seed=42`):
  silhouette `0.3156523270`, davies_bouldin `1.0737231775`, calinski_harabasz `134.9785471342`, sizes
  `[32, 38, 83]`, `converged=true`. Each carries a `_source` field stating it is **re-derived from
  `perform_*==0.1.0a4`, a drift gate, not independently recorded** — matching how the existing PCA
  golden honestly frames its `pca_explained_variance_ratio` / `heritability_mean` / `umap_trustworthiness`
  keys. It guards drift; it does not independently corroborate the partition.

## Impact

- **Affected specs:** `bloommcp-clustering-tool` (new capability). Builds on (does not modify) the
  existing `bloommcp-tool-contract`, `bloommcp-experiment-read`, `bloommcp-result-store` (Tiers 1–2),
  `bloommcp-qc-clean-tool` (Tier 3), and `bloommcp-pca-analysis-tool` (Tier 4) capabilities — it
  **consumes** the cleaned version `qc_clean` produces, in parallel with `pca_analysis`.
- **Affected code:**
  - new `bloommcp/src/bloom_mcp/tools/clustering_tool.py` (tool + I/O models + dispatch + `register`);
  - `bloommcp/src/bloom_mcp/server.py` (register the tool; update the module docstring);
  - new `bloommcp/tests/tools/test_clustering_tool.py` (5 patterns + determinism + polymorphic
    dispatch) + the per-method snapshot through the tool;
  - new `bloommcp/tests/fixtures/turface_19_clustering_golden.json` (characterization snapshot,
    honestly framed) **emitted by a committed generator** (no hand-transcribed metric literal) + a
    `tests/fixtures/README.md` note; reuses the post-QC `turface_19_final_data.csv` and the 8 golden
    `trait_cols` from `turface_19_pca_golden.json`;
  - **`bloommcp/src/bloom_mcp/tools/_qc_shared.py`** — promote the certified-set / empty-list /
    duplicate-name rejections (today private to `pca_analysis_tool.py`) into
    `_validate_trait_subset` behind a new **`require_certified: bool = False`** opt-in flag. The default
    preserves `qc_inspect`'s current "in-frame + numeric" behavior **byte-identically** (a regression
    test pins this); `pca_analysis` and `clustering` pass `require_certified=True` + `frame.trait_cols`.
    One validator, no fourth copy, no `qc_inspect` behavior change (the #308 §6 / #309 carry-over);
  - `bloommcp/scripts/live_persistence_smoke.py` + `tests/scripts/` — extend the live smoke with the
    `clustering` composition leg + its pure-helper unit tests;
  - **live-smoke enumeration docs that go stale when a 4th leg lands** (caught by the `remove_outliers`
    sibling; do not drop it here): `bloommcp/README.md` (the "drives clustering, `qc_clean`, and
    `remove_outliers`" sentence, ~L33), `DEV_SETUP.md` (~L225, same sentence), and
    `bloommcp/docs/local-validation.md` (the "runs three legs" count + the `SMOKE PASSED` summary block
    + a new **"Leg 4 — clustering (granular tool)"** section, disambiguated from the existing **"Leg 1 —
    clustering (legacy workflow)"** so the file does not carry two identically-titled legs). Prefer
    rewording the enumerations to a non-exhaustive phrase so future tiers need no doc churn;
  - the `server.py` "Direct tools (granular)" docstring line is phrased to disambiguate the granular
    `clustering` tool from the legacy `run_clustering_workflow` (e.g. "k-means / GMM on a cleaned
    experiment (require_clean; delegates to perform_kmeans_clustering / perform_gmm_clustering)"), not a
    bare "clustering";
  - **no** category line is added to `bloommcp/README.md`'s tool-category sentence — it already lists
    "clustering" (per the `pca_analysis` precedent, which likewise left the README category untouched);
    only the smoke-leg sentence above changes;
  - no change to `bloom_mcp.clustering`'s logic, `run_clustering_workflow`, or the discovery tools;
    **no** edit to `bloommcp/docs/roadmap.md` (its tier-number reshape is owned by #339).
- **Dependencies:** `sleap_roots_analyze.{perform_kmeans_clustering, perform_gmm_clustering,
  KMeansResult, GMMResult}` — all in `0.1.0a4`, already pinned and locked. **No new pin.**
- **Deferred (hierarchical), upstream-gated:** a fast-follow adds a `"hierarchical"` `method` member
  once `sleap-roots-analyze` `0.1.0a5` ships (a) a public labeled-clustering entry point returning a
  `ClusterResult` (labels + sizes + scores, not just a dendrogram) and (b) `Optional`
  `ClusterResult.random_state`. Tracked by a new/reopened `talmolab/sleap-roots-analyze` issue filed
  by this change. The deterministic `seed = None` path is designed for now (see design) so it drops in
  with no contract change.
- **Carry-overs (#309):** inherits the same "latest cleaned" resolution as `pca_analysis`, so the
  artifact-handoff caveats **#419 / #420** apply here too; consumes `_qc_shared` rather than adding a
  fourth helper copy.
- **Composition dependency & merge order:** the unit determinism oracle and `require_clean` property
  test seed a cleaned version directly via `FakeReader.add_cleaned_version` (the reader and store
  fakes are disjoint), so they do **not** block on a live Tier 3. Only the live-smoke composition leg
  needs a real `qc_clean` run; it rides `staging` (where #356 has merged).
- **Branch/PR:** branches off `origin/staging` (`egao28/bloommcp-tier5-clustering`); PR targets
  `staging`, linking #309 + the roadmap Tier 5 row.
