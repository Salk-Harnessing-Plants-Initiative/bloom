## 1. Commit the OpenSpec proposal

- [ ] 1.1 Commit `openspec/changes/update-python-audit-ci-reliability/**` on its own (`docs(#454): openspec proposal — CI reliability for python-audit job`)

## 2. Tier the full-fixture oracle tests as integration tests

- [x] 2.1 Add a `markers` entry to `bloommcp/pyproject.toml`'s `[tool.pytest.ini_options]` with the exact description: `"integration: full-fixture statsmodels/umap oracle tests over turface_19 — slow, intermittently stalls in CI containers; excluded from per-PR CI, run via /pre-merge or pytest -m integration"` (deliberately not copied from `bloomcli`'s or the root's `integration` marker text — those describe different, infra-gated reasons that don't apply here)
- [x] 2.2 Mark these four tests in `bloommcp/tests/test_oracle.py` with `@pytest.mark.integration`:
  - `test_external_library_heritability_matches_recorded_oracle`
  - `test_delegated_heritability_returns_wrapper_consumed_keys`
  - `test_external_library_umap_is_deterministic_and_structural`
  - `test_umap_trustworthiness_floor_rejects_wrong_parameters`
- [x] 2.3 Prove the partition locally, before CI depends on it: run `cd bloommcp && uv run --extra test pytest tests/ -m integration --collect-only -q` and confirm exactly the 4 tests above are collected (no fewer — a typo'd marker would silently leave a stalling test in the per-PR bucket — and no more). **Verified**: `4/421 tests collected (417 deselected)`, exactly the 4 named tests.
- [x] 2.4 Run `cd bloommcp && uv run --extra test pytest tests/ -m "not integration" --collect-only -q` and confirm the same 4 tests are absent and no other test in the suite is affected. **Verified**: `417/421 tests collected (4 deselected)` — 4 + 417 = 421, no other test affected.
- [ ] 2.5 Commit `bloommcp/pyproject.toml` + `bloommcp/tests/test_oracle.py` together (`test(#454): tier bloommcp oracle heritability/UMAP tests as integration`)

## 3. CI: timeout + exclude the marker from per-PR CI

- [x] 3.1 Add `timeout-minutes: 20` to the `python-audit` job in `.github/workflows/pr-checks.yml` (alongside `runs-on:`, before `steps:`, matching `docker-build`/`compose-health-check`/`dev-stack-smoke`'s placement)
- [x] 3.2 Update the `Run bloom_mcp package tests` step's `run:` line from
      `cd bloommcp && uv run --frozen --extra test pytest tests/ -v --tb=short`
      to
      `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration" -v --tb=short`
      — preserved `--frozen --extra test` and the existing `SUPABASE_URL`/`BLOOM_AGENT_KEY` env vars on the step unchanged.
- [ ] 3.3 Commit `timeout-minutes: 20` on its own (`ci(#454): cap python-audit job at timeout-minutes: 20`) so it can be reverted independently of the marker-exclusion change below if the value ever proves too tight
- [ ] 3.4 Commit the `-m "not integration"` change on its own (`ci(#454): exclude integration-marked tests from python-audit per-PR run`)

## 4. Wire into all three `/pre-merge` checklist surfaces

- [x] 4.1 "Step 2: Python Audit" section: added `cd bloommcp && uv run --extra test pytest tests/ -m integration -v --tb=short`
- [x] 4.2 "Quick Pre-Merge (Minimum)" section: added the same command so a developer following only the fast path still sees it
- [x] 4.3 Final "Pre-Merge Checklist": added a distinct checklist item, separate from "All CI jobs pass", since CI no longer runs these
- [ ] 4.4 Commit `.claude/commands/pre-merge.md` on its own (`docs(#454): run bloommcp integration oracle tests in /pre-merge`)

## 5. Validate

- [x] 5.1 Hand-verified against `openspec/AGENTS.md` format rules (`openspec` CLI is unavailable in this environment — checked PATH, npm/npx, pipx, common install dirs; none found, so `openspec validate --strict` could not be run)
- [x] 5.2 Confirmed `tests/unit/test_ci_workflow_uv_conventions.py` still passes (3 passed) — the new `run:` line keeps `--extra test` and does not introduce `--with`
- [x] 5.3 Confirmed unaffected: ran the full bloommcp suite with `-m "not integration"` (417 passed, 4 deselected, ~62s) and with `-m integration` (4 passed, 417 deselected, ~13s) — `bloomcli`'s pre-existing `-m "not integration"` step and the wheel-build step (no pytest invocation) are untouched by this change
