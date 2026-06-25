## 0. Archive ordering (note — not a blocker for this implementation)

- [x] 0.1 `sleap-roots-analyze==0.1.0a3` is published on PyPI (talmolab/sleap-roots-analyze#163 / release #169 — live 2026-06-25)
- [x] 0.2 Ordering note: this change MODIFIES *Additive Dependency Set*, which enters `openspec/specs/` when `add-bloommcp-package-baseline` is archived (cleanup PR #355). PR #355 also archives `prune-bloommcp-analysis-deps`, which only ADDs requirements and does not touch *Additive Dependency Set*, so there is no content conflict. Action at archive time: archive PR #355 before this change. This does not block reviewing or merging the code change.

## 1. Bump + re-lock

- [x] 1.1 In `bloommcp/pyproject.toml`, change `sleap-roots-analyze>=0.1.0a2` to `>=0.1.0a3` (left `sleap-roots-contracts[pandas]>=0.1.0a1` unchanged)
- [x] 1.2 Run `uv lock` in `bloommcp/` to regenerate `bloommcp/uv.lock` with `0.1.0a3` + transitive deps
- [x] 1.3 Root `uv.lock` — N/A: root has no `tool.uv.workspace` and never references the package; `bloommcp/uv.lock` is independent, so the root lock is unchanged
- [x] 1.4 Verify `uv lock --check` passes for `bloommcp/` (and the other service locks via `scripts/check-uv-locks.py`: langchain, bloommcp, services/video-worker — root is not a checked service)

## 2. Verify green

- [x] 2.1 `uv sync --extra test` then confirm `import bloom_mcp` succeeds in a clean resolve
- [x] 2.2 Run the bloommcp test suite (`uv run --extra test pytest`) — 92 passed (also green in CI against the locked a3 wheel)
- [x] 2.3 Add `tests/test_analyze_result_types_importable.py` asserting `PCAResult`, `HeritabilityResult`, `KMeansResult`, `GMMResult` import from `sleap_roots_analyze` — backs the floor's purpose with a test
- [x] 2.4 The `heritability_mean` characterization in `tests/test_oracle.py` did **not** drift under `0.1.0a3` (suite green as-is, verified in CI); snapshot left untouched
- [x] 2.5 Stamp the golden fixture provenance (`tests/fixtures/turface_19_pca_golden.json`) to record re-verification through `0.1.0a3` (value unchanged from `0.1.0a2`)
- [x] 2.6 Confirmed no other test hardcodes an installed-version assertion that the bump breaks (the `"0.1.0a2"` literals in `tests/contract/` and `tests/result_store/` are fixed fixtures, not installed-version reads; `test_code_versions_installed_only` reads dynamically and passes)

## 3. Spec sync

- [x] 3.1 `bloommcp-packaging` delta (*Additive Dependency Set*, floor `>=0.1.0a3`) reflects the merged `pyproject.toml`; the "Lockfiles are in sync" scenario is worded to match `scripts/check-uv-locks.py` (langchain + bloommcp + video-worker; root independent and excluded)
- [x] 3.2 `openspec validate bump-analyze-pin-a3 --strict` passes
