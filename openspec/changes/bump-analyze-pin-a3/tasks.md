## 0. Preconditions (blocked until satisfied)

- [x] 0.1 Confirm `sleap-roots-analyze==0.1.0a3` is published on PyPI (talmolab/sleap-roots-analyze#163 / release #169 — live as of 2026-06-25)
- [ ] 0.2 Confirm `add-bloommcp-package-baseline` is archived (so the *Additive Dependency Set* requirement exists in `openspec/specs/` for this delta to apply cleanly) — still 28/29 tasks, not archived; affects only the eventual spec archive, not this implementation

## 1. Bump + re-lock

- [x] 1.1 In `bloommcp/pyproject.toml`, change `sleap-roots-analyze>=0.1.0a2` to `>=0.1.0a3` (left `sleap-roots-contracts[pandas]>=0.1.0a1` unchanged)
- [x] 1.2 Run `uv lock` in `bloommcp/` to regenerate `bloommcp/uv.lock` with `0.1.0a3` + transitive deps
- [x] 1.3 Root workspace lock — N/A: root has no `tool.uv.workspace` and `bloommcp/uv.lock` is independent; root `uv.lock` is unchanged (`uv lock` at root resolved 72 pkgs, no diff)
- [x] 1.4 Verify `uv lock --check` passes (bloommcp + root both in sync)

## 2. Verify green

- [x] 2.1 `uv sync --extra test` then confirm `import bloom_mcp` succeeds in a clean resolve
- [x] 2.2 Run the bloommcp test suite (`uv run --extra test pytest`) — 92 passed
- [x] 2.3 The `heritability_mean` characterization in `tests/test_oracle.py` did **not** drift under `0.1.0a3` (suite green as-is); snapshot left untouched
- [x] 2.4 Confirmed no other test hardcodes an installed-version assertion that the bump breaks (the `"0.1.0a2"` literals in `tests/contract/` and `tests/result_store/` are fixed fixtures, not installed-version reads; `test_code_versions_installed_only` reads dynamically and passes)

## 3. Spec sync

- [x] 3.1 Confirm the `bloommcp-packaging` delta (*Additive Dependency Set*, floor `>=0.1.0a3`) reflects the merged `pyproject.toml`
- [x] 3.2 `openspec validate bump-analyze-pin-a3 --strict` passes
