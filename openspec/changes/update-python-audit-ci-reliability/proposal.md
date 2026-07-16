## Why

The `python-audit` job in `.github/workflows/pr-checks.yml` has no `timeout-minutes`, unlike every other slow job in the same workflow (`docker-build`: 30m, `compose-health-check`: 30m, `dev-stack-smoke`: 20m). When a step inside it hangs, the job blocks for GitHub Actions' default 360-minute (6-hour) job cap before failing, with zero output in between — CI looks stuck rather than clearly failing (#454).

This has already happened twice on PR #405 (runs `29353244406` and `29441421235`): `bloommcp/tests/test_oracle.py`'s heritability/UMAP tests — which run `statsmodels` MixedLM and `numba`-JIT UMAP over the full 19-genotype `turface_19` fixture — intermittently stall in CI containers (thread-pool contention between OpenBLAS/numba, or MixedLM optimizer non-convergence). A related fix already landed for the same class of problem (`3a700fd`, swapping the full fixture for a 15-row synthetic one in `test_local_mode.py`); `test_oracle.py`'s heritability/UMAP tests were added later (`e4d8015`) and never got the same treatment. These tests are numeric-drift oracles, not per-PR-relevant correctness checks, so they don't need to run on every PR.

## What Changes

- Add `timeout-minutes: 20` to the `python-audit` job so a stall fails fast and visibly instead of silently burning hours.
- Add an `integration` pytest marker to `bloommcp/pyproject.toml` (matching the existing convention in `bloomcli/pyproject.toml` and the root `pyproject.toml`) and mark the four full-fixture heritability/UMAP tests in `bloommcp/tests/test_oracle.py` with `@pytest.mark.integration`.
- Exclude the marker from the `Run bloom_mcp package tests` step in `python-audit` (`pytest tests/ -m "not integration"`), matching how the `Run bloomctl package tests` step in the same job already excludes its own `integration` marker.
- Add a step to `/pre-merge` (`.claude/commands/pre-merge.md`) that runs the integration-marked bloommcp tests locally, referenced from all three of its checklist surfaces (Step 2, the "Quick Pre-Merge (Minimum)" path, and the final "Pre-Merge Checklist"), so numeric drift is still caught before merge without blocking every PR's CI.

## Impact

- Affected specs: `python-dependency-management` (owns the `python-audit` CI job's behavior)
- Affected code: `.github/workflows/pr-checks.yml`, `bloommcp/pyproject.toml`, `bloommcp/tests/test_oracle.py`, `.claude/commands/pre-merge.md`
- Trade-off: the four full-fixture oracle tests no longer run on every PR — only when a developer follows `/pre-merge` (any of its three checklist surfaces reference the step) or manually runs `pytest -m integration`. A PR that skips `/pre-merge` entirely could merge with undetected numeric drift in the delegated `statsmodels`/`umap` calls until the next `/pre-merge` run catches it.
