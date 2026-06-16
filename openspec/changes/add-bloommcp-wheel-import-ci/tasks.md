## 1. Test-first: regression-guard for the gate (TDD red)

- [x] 1.1 Create `tests/unit/test_bloommcp_wheel_import_gate.py`. **Reuse** the `_iter_steps` and `_logical_lines` helpers from `tests/unit/test_ci_workflow_uv_conventions.py` (import them, or factor both files onto a shared `tests/unit/_workflow_helpers.py`) — logical-line joining matters because the import command is split across physical lines with backslash continuations. PyYAML is already in the root `test` extra (added by `harden-ci-uv-conventions`). Locate the `python-audit` job and assert it contains **a** step (match on presence, never a fixed index) whose joined `run:` body:
  - runs `uv build` with the working dir in `bloommcp` (`cd bloommcp` then a `uv build` line), AND
  - imports from a project-free env — a logical line contains `--no-project` AND names **all four** modules `import bloom_mcp`, `bloom_mcp.tools`, `bloom_mcp.storage`, `bloom_mcp.server` (so the gate can't be silently narrowed), AND
  - the step's `env:` sets `SUPABASE_URL` and `BLOOM_AGENT_KEY` to empty strings.
  Do NOT assert on `--isolated` (the step deliberately omits it). Emit a locating failure message (`f"pr-checks.yml: python-audit: <missing assertion>"`). The test MUST FAIL before task 2 (no such step exists yet).
- [x] 1.2 Run `cd ~/bloom && uv run --extra test pytest tests/unit/test_bloommcp_wheel_import_gate.py -v` and confirm red.

## 2. Add the build + clean-import step (TDD green)

- [x] 2.1 In `.github/workflows/pr-checks.yml`, add a step to the `python-audit` job, after the `Run bloom_mcp package tests` step:
  ```yaml
  # Build the wheel and import it from a clean, project-free env so a
  # packaging regression (bad module-name/module-root, dropped __init__.py,
  # empty-namespace wheel) fails the PR — the src/-on-sys.path import tests
  # and the editable Docker install can't catch that. The __file__ assert
  # proves the import resolved the wheel, not src/. --no-project gives the
  # src-free namespace; --isolated is omitted on purpose (it would cold-
  # download the whole heavy dep tree from PyPI every run). rm -rf dist +
  # the single-wheel test guard the glob. SUPABASE_URL/BLOOM_AGENT_KEY empty
  # keeps the lazy-validation contract load-bearing. `--with <wheel>` is
  # permitted by the uv-conventions guard (Invariant 3 only forbids --with
  # alongside pytest; this line runs `python -c`, not pytest). (uv build has
  # no --frozen flag — it builds in PEP 517 isolation, nothing to freeze.)
  - name: Build bloom_mcp wheel and import in a clean env
    run: |
      cd bloommcp
      rm -rf dist
      uv build --wheel
      wheel=$(echo dist/bloommcp-*.whl)
      test -f "$wheel" || { echo "expected exactly one wheel, got: $wheel"; exit 1; }
      uv run --no-project --with "$wheel" \
        python -c "import bloom_mcp, bloom_mcp.tools, bloom_mcp.storage, bloom_mcp.server; assert 'site-packages' in bloom_mcp.__file__, bloom_mcp.__file__"
    env:
      SUPABASE_URL: ""
      BLOOM_AGENT_KEY: ""
  ```
- [x] 2.2 Run the guard test again — confirm green: `uv run --extra test pytest tests/unit/test_bloommcp_wheel_import_gate.py -v`.
- [x] 2.3 Confirm the existing uv-conventions guard still passes (the new `--with` line co-occurs with `python`, not `pytest`): `uv run --extra test pytest tests/unit/test_ci_workflow_uv_conventions.py -v`.

## 3. Verify the step locally end-to-end

- [x] 3.1 Reproduce the CI step locally to prove a real wheel imports clean:
  ```bash
  cd bloommcp && rm -rf dist && uv build --wheel
  SUPABASE_URL="" BLOOM_AGENT_KEY="" \
    uv run --no-project --with ./dist/bloommcp-*.whl \
    python -c "import bloom_mcp, bloom_mcp.tools, bloom_mcp.storage, bloom_mcp.server; assert 'site-packages' in bloom_mcp.__file__, bloom_mcp.__file__; print('clean import OK:', bloom_mcp.__file__)"
  ```
- [x] 3.2 Sanity-check the negative path: confirm the import would fail if the wheel were misconfigured (e.g. temporarily build with a broken `module-name`, observe non-zero exit, then revert). Document the observation in the PR description; do not commit the broken config.
- [x] 3.3 Confirm `bloommcp/dist/` is gitignored (`.gitignore:20` lists `dist/`) and `git status` shows no `dist/` artifacts staged.

## 4. Validate + finalize

- [x] 4.1 `openspec validate add-bloommcp-wheel-import-ci --strict` passes.
- [x] 4.2 Full unit suite green: `uv run --extra test pytest tests/unit/ -v`.
- [ ] 4.3 Update `tasks.md` checkboxes via `/openspec:apply`; open the PR (base: `eberrigan/bloommcp-tier0-baseline`, stacked on #313) with `/pr-description`. Commit the new test and the `pr-checks.yml` step **together** (one `ci(bloommcp):` commit) so no pushed commit is red — the red→green cycle (tasks 1.2/2.2) is a local TDD checkpoint, not a commit boundary. Squash-merge. Suggested commits: `docs(bloommcp): OpenSpec proposal — CI-gate built-wheel import` (proposal docs) then `ci(bloommcp): build wheel + clean-import gate in python-audit` (test + workflow).
- [ ] 4.4 **After #313 merges to `staging`**: retarget this PR's base to `staging` in the GitHub UI, then rebase onto the new tip (do not rely on GitHub auto-retarget — #313 may squash/rebase-merge to new SHAs):
  ```bash
  git fetch origin staging
  git rebase --onto origin/staging eberrigan/bloommcp-tier0-baseline add-bloommcp-wheel-import-ci
  ```
  Expect a `pr-checks.yml` conflict (the `python-audit` hunk overlaps #313's `Run bloom_mcp package tests` addition) — resolve by keeping **both** steps. Re-run the new guard test + `test_ci_workflow_uv_conventions.py`, confirm CI green against `staging`, then `git push --force-with-lease`.
