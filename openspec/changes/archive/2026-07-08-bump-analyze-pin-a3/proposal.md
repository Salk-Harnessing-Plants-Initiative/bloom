## Why

bloom-mcp Tiers 3/4 (`pca_analysis` / `clustering` tools, #308/#309) will consume
`sleap-roots-analyze`'s **serializable result types** directly
(`from sleap_roots_analyze import PCAResult, HeritabilityResult, KMeansResult, GMMResult`).
Those types ship in the analyze `0.1.0a3` release. A `pip`/`uv` install resolves
**released** versions, not `main`, so until the pin floor is raised to `>=0.1.0a3` those
tiers would have to write a throwaway dict→type adapter against `0.1.0a2`'s untyped
`perform_*` returns. Raising the floor + re-locking lets Tiers 3/4 consume the upstream
typed results and removes the need for that interim adapter.

This is Tier 3 pre-work tracked by **#327**.

`0.1.0a3` is **published on PyPI** (release talmolab/sleap-roots-analyze#169, uploaded
2026-06-25), so the bump + re-lock are done and CI-green — see What Changes / Impact.

## What Changes

- `bloommcp/pyproject.toml`: raised `sleap-roots-analyze>=0.1.0a2` → `>=0.1.0a3`.
  `sleap-roots-contracts[pandas]>=0.1.0a1` is **unchanged** — `main` == `0.1.0a1` ==
  released; no new contracts release is needed.
- Re-locked `bloommcp/uv.lock` so it resolves `0.1.0a3` and its transitive deps. The root
  `uv.lock` is **untouched**: the root has no `tool.uv.workspace` and never references the
  package; `bloommcp/uv.lock` is independent.
- Added a one-line import guard
  (`bloommcp/tests/test_analyze_result_types_importable.py`) asserting the four typed
  result classes resolve from the released package — so the floor's purpose is verified by
  a test, not merely asserted in the spec.
- `import bloom_mcp` and the existing 92-test suite stay green. The `0.1.0a2` heritability
  characterization in `tests/test_oracle.py` did **not** drift under `0.1.0a3` (verified in
  CI against the locked a3 wheel), so the snapshot is left untouched; its fixture
  provenance stamp is updated to record re-verification through `0.1.0a3`.

This is a non-breaking dependency floor bump; no application code or public API changes.

## Impact

- Affected specs: `bloommcp-packaging` (MODIFIED: *Additive Dependency Set* — floor
  `>=0.1.0a3`, + a scenario asserting the typed result classes resolve).
- Affected code: `bloommcp/pyproject.toml`, `bloommcp/uv.lock`,
  `bloommcp/tests/test_analyze_result_types_importable.py`,
  `bloommcp/tests/fixtures/turface_19_pca_golden.json` (provenance stamp only).
- Unblocks: bloom-mcp Tier 3 (#308) / Tier 4 (#309) consuming upstream typed results.
- **Archive ordering — this change MODIFIES *Additive Dependency Set*, defined (at the
  `>=0.1.0a2` floor) by `add-bloommcp-package-baseline`.** That base requirement is
  archived into `openspec/specs/` by the cleanup change (PR #355), which also archives
  `prune-bloommcp-analysis-deps`. `prune` shares the `bloommcp-packaging` capability but
  only **ADDs** new requirements (*Necessary-and-Sufficient Declared Dependencies*,
  *Heritability and UMAP Analysis Delegated…*) — it does **not** touch *Additive
  Dependency Set* — so there is no content conflict with this MODIFY. Sequencing: archive
  PR #355 before archiving this change, so the MODIFY has its base requirement to apply
  against (see tasks §0). Per `openspec/AGENTS.md` this is otherwise a non-breaking
  dependency update; no `design.md` is warranted.
