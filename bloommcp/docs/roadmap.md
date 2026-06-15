# bloom-mcp Phase 2 — vertical-slice roadmap

**Status: APPROVED (Elizabeth, 2026-06-15) — adversarially reviewed; tracking issues filed (#305–#309). Implement tier-by-tier via `/new-feature`.**

**Design (source of truth):** vault `docs/superpowers/specs/2026-06-15-bloom-mcp-phase2-design.md` (copy/pointer in this repo at `bloommcp/docs/`). Master spec: `2026-05-11-metcalf-2026-evelyn-bloom-mcp-design.md`.

**Program:** harden the `bloommcp` prototype into a thin, validated MCP server that **delegates all analysis to `sleap-roots-analyze`**, proven via a **depth vertical slice** (contract pattern end-to-end on 1–2 fast tools). One **OpenSpec change + PR per tier**, TDD, tier-by-tier per the `roadmap-driven-pipeline` workflow.

**How each tier is built:** run the repo's **`/new-feature`** command (staging-first): branch off `origin/staging` → `/openspec:proposal` (`tasks.md` TDD-ordered, **oracle test first**) → `/review-openspec` (reconcile BLOCKING/IMPORTANT literally) → **Elizabeth approval** → `/openspec:apply` (TDD) → `/pre-merge` → PR **to `staging`** (link the tier issue + this roadmap) → `/openspec:archive`. Each tier issue (#305–#309) carries these steps.

**Oracle, generally:** bloom-mcp tools are validated by **reproducing `sleap-roots-analyze`'s golden values *through* the MCP tool** (the #120 wheat-EDPIE fixtures) + the contract's 5 test patterns. Tracking: **one issue per tier** (in `Salk-Harnessing-Plants-Initiative/bloom`).

## Live-state facts (verified 2026-06-15)
- **The delegated functions are ALREADY public** in `sleap-roots-analyze` **v0.1.0a2**: `perform_pca_analysis`, `perform_kmeans_clustering`, `perform_gmm_clustering` are in `__all__`. So Tiers 3–4 are **not** blocked on a new release.
- **The result *types* are in-flight, additive adapters:** `PCAResult` (#149), `HeritabilityResult` (#150), `ClusterResult`/`KMeansResult`/`GMMResult` (#151) are **open PRs** (a reviewed, ready stack) over the dict the `perform_*` functions already return. Consuming them is a **preferred upgrade** (avoids duplicating a ~30-line adapter), **not** a hard gate.
- `sleap-roots-contracts 0.1.0a1` **is** on PyPI (exports `validate_analysis_input` + `canonicalize_role_dtypes`). analyze wires contracts via a PyPI pin in **open PR #155** (not a git ref; not merged).
- **bloommcp today has NO `sleap-roots-analyze` or `sleap-roots-contracts` dependency** (the `source/` files are self-contained vendored sklearn). Tier 0 **introduces** them.
- salk-bloom is **staging-first**: feature work branches off `origin/staging` and PRs target **`staging`**; `main` is the deploy/prod branch (only promotion PRs target it). The bloom agent is **not live**.

## Hard constraints / decisions (design)
Thin delegation (re-orchestrate nothing) · small flat fast surface (Qwen3.5-9B + Claude) · flat `src/bloom_mcp/tools/` (no `v1/`, no `generated/manual`) · package-SemVer + stable names (no `_v2`) · auto provenance (`package_version` + `source_version`) · auth/transport unchanged. **Deferred (design §8):** URL-namespace, `find_tools`/RAG, async-pipeline tools, api-diff gate, Phase-3 generator — **and the retirement of `source/*` + the bespoke `run_X_workflow` tools** (pending Benfica).

## Tiers

Status: ✅ done · 🔵 in progress · ⬜ not started.

| Tier | Goal | Oracle / validation | Depends on | Tracking | Status |
|---|---|---|---|---|---|
| **0 — baseline + test stack** | Restructure `bloommcp/source/`+`tools/` → `src/bloom_mcp/` (uv package); **introduce** deps `sleap-roots-analyze>=0.1.0a2` + `sleap-roots-contracts[pandas]>=0.1.0a1`; add dev-test stack (`pytest`, `hypothesis`, `syrupy`, FastMCP `Client`) + `tests/` layout; **source the #120 turface_19 fixture + recorded golden values into `tests/fixtures/`**; decide bloommcp's own `openspec/` scope vs root (`web/openspec` precedent) | `uv` build + `import bloom_mcp` clean; existing `server.py` boots + `/health` ok; `uv run pytest` **collects** + dev group resolves; full existing suite green | — | [#305](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/305) | ⬜ |
| **1 — contract layer** | `src/bloom_mcp/contract/`: `@as_mcp_tool` (Pydantic I/O validation, exceptions → `BloomMCPError`, auto `Provenance` = `package_version` + `source_version`) | unit tests: every wrapped call stamps a complete `Provenance`; declared errors → `BloomMCPError`, never raw | Tier 0 | [#306](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/306) | ⬜ |
| **2 — data-access layer** | `src/bloom_mcp/data_access/`: storage-agnostic `load_experiment`/`list_experiments`/artifact+plot helpers/versioned writer; CSV-backed (relocated from `source/experiment_utils.py`) | unit tests: `load_experiment(fixture)` returns the expected frame; interface stable so the Bloom-DB impl (gated on integration sub-project #2) drops in unchanged | Tier 0 | [#307](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/307) | ⬜ |
| **3 — first granular tool `pca_analysis`** | **ADD** a `pca_analysis` tool delegating to the already-public `sra.perform_pca_analysis` → `PCAResult`, wrapped by the contract, using data-access; **register it in `src/bloom_mcp/server.py`**; 5 test patterns. *Leaves the existing `dimred_workflow` + `source/pca.py` in place* (retirement deferred — see note). Prefer consuming upstream `PCAResult` once #149 releases; interim local adapter possible but avoid duplicating it | **Oracle:** reproduce the #120 turface_19 golden PCA (PC1≈86.1%/PC2≈5.8%/PC3≈4.0%) **through the MCP tool** within tolerance; tool **appears in `tools/list`** (FastMCP `Client`); schema round-trip + provenance + property + error-envelope; validated on **Claude Desktop** | Tiers 1, 2 (soft: upstream `PCAResult` #149) | [#308](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/308) | ⬜ |
| **4 — second granular tool `clustering`** | **ADD** a `clustering` tool delegating to public `sra.perform_kmeans_clustering`/`perform_gmm_clustering` → `KMeansResult`/`GMMResult` (polymorphic), wrapped; register in `server.py`; 5 patterns + determinism. *Leaves `clustering_workflow` + `source/clustering.py` in place* (retirement deferred) | **Oracle:** determinism (same seed → identical labels) + golden cluster metrics through the tool; appears in `tools/list`; round-trip + provenance + error-envelope; Claude Desktop | Tiers 1, 2 (soft: upstream `ClusterResult`/`KMeansResult`/`GMMResult` #151) | [#309](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/309) | ⬜ |

## Sequencing
Tiers **0 → 1 → 2 → 3 → 4 can all proceed now** — the delegated `perform_*` functions are already public in v0.1.0a2. Tiers 3–4 *prefer* the result-types release (#149/#151, reviewed + ready) to consume `PCAResult`/`KMeansResult` upstream rather than duplicate the additive adapter; this is a soft dependency, not a block.

## Deferred (out of slice scope — triggers in design §8)
**Retirement of `source/*` + the bespoke `run_X_workflow` tools** (pending Benfica; deleting `source/pca.py`/`source/clustering.py` now would break the booting server via the workflow tools' module-level imports — so retire only when the workflow tools are removed/repointed, as the surface strategy settles) · async/long-running pipeline tools · `find_tools` + RAG-MCP (Phase 3) · URL-namespace versioning · api-diff gate (at first publish) · Phase-3 generator.

## Related dependencies (not slice-gating)
- **#116** (expose `statistics.py`/heritability public API, open, priority:high, self-labeled bloom-mcp blocker) + **#150** (`HeritabilityResult`) gate a *future* `heritability` tool — **not** Tiers 3/4 (PCA/clustering are already public).
- analyze↔contracts wiring is **open PR #155** (analyze-side); bloom-mcp depends on `sleap-roots-contracts` **directly**, so this doesn't gate the slice.

## Reconciliation log (adversarial roadmap review, 2026-06-15)
- **[BLOCKING] Server-break on retirement** (completeness/safety lens): `server.py` registers `dimred_workflow`/`clustering_workflow`, which module-level-import `source.pca`/`source.clustering`; deleting those modules breaks server boot (Tier 0 oracle) and touches Benfica's tools pre-input. → **Reconciled:** Tier 3/4 ADD tools alongside; `source/*` + workflow-tool retirement moved to Deferred (post-Benfica).
- **[BLOCKING] Tier 3/4 not release-gated** (dependency lens): `perform_*` already public in v0.1.0a2; result types are additive. → **Reconciled:** ⛔→⬜, soft dep; "introduce" deps at Tier 0; corrected interim option (no git-ref precedent — analyze used a PyPI pin in PR #155).
- **[BLOCKING] Design line 11 stale** ("result types have landed"): they're open PRs. → **Reconciled:** design doc corrected (separate edit).
- **[IMPORTANT]** result-type names → `ClusterResult`/`KMeansResult`/`GMMResult`; **#116 mis-scoped** (heritability only, not PCA/clustering) → moved to "related, not slice-gating"; **no tool-registration step** → added to Tier 3/4 goals + oracle (`tools/list`); **no test-stack/fixtures step** → added to Tier 0; **Tier 0 is "introduce" not "bump"** deps. All reconciled above.
