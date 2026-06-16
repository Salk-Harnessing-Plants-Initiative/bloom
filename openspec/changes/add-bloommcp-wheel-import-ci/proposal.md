## Why

The `bloommcp-packaging` capability (added by `add-bloommcp-package-baseline`, PR #313) declares:

> **Scenario: Built wheel imports under the new namespace**
> WHEN the package is built with `uv build` and the resulting wheel is installed into a clean environment
> THEN `bloom_mcp`, `bloom_mcp.tools`, and `bloom_mcp.storage` import without error

**No automated check exercises the built wheel.** Every import test in `bloommcp/tests/test_package_baseline.py` loads `bloom_mcp` off `src/` on `sys.path`, so it still passes even if the `[tool.uv.build-backend]` packaging (`module-name` / `module-root` at `bloommcp/pyproject.toml`) or the `__init__.py` / `__main__.py` layout were misconfigured and the wheel shipped nothing importable. `uv build` was run once by hand (baseline task 4.5) but is not gated in CI, so a packaging regression — a renamed module root, a dropped `__init__.py`, a wheel that ships an empty namespace — would merge green.

This change closes that gap by building the wheel in CI and importing it from a clean, project-free environment that cannot see the source tree, so a packaging regression actually fails the PR. Tracked out of the review of #313 (refs #313, #305).

## What Changes

- **ADD** a build + clean-import step to the existing `python-audit` job in `.github/workflows/pr-checks.yml` (the job that already runs the bloommcp Supabase-free suite, the `Run bloom_mcp package tests` step). The step:
  - builds the wheel in `bloommcp/`, then imports it from a project-free env and asserts the import resolved the **wheel**, not the `src/` checkout:
    ```bash
    cd bloommcp
    rm -rf dist
    uv build --wheel
    wheel=$(echo dist/bloommcp-*.whl)
    test -f "$wheel" || { echo "expected exactly one wheel, got: $wheel"; exit 1; }
    uv run --no-project --with "$wheel" \
      python -c "import bloom_mcp, bloom_mcp.tools, bloom_mcp.storage, bloom_mcp.server; assert 'site-packages' in bloom_mcp.__file__, bloom_mcp.__file__"
    ```
  - `--no-project` + `--with <wheel>` resolves the wheel into an ephemeral env with **no** editable/`src` path, so the import exercises the shipped artifact, not the checkout. The `__file__` assertion makes that load-bearing: if `--no-project` ever stopped being honored and `src/` leaked back onto the path, the gate fails instead of passing green.
  - `--isolated` is deliberately **omitted**: `--no-project` already delivers the src-free namespace (the actual goal), whereas `--isolated` bypasses the uv cache and cold-downloads the entire heavy runtime closure (`sleap-roots-analyze`, `statsmodels`, `umap-learn`→`numba`/`llvmlite`, `scipy`, `fastmcp`, `supabase`…) from PyPI on every run — minutes of networked, PyPI-flaky cost for zero smoke-test benefit.
  - `rm -rf dist` + the single-wheel `test -f` guard defend against a stale/multi-match glob (and keep the CI step in parity with the local repro). `--wheel` builds wheel-only, so no sdist pollutes the glob. (`uv build` has no `--frozen` flag — unlike `uv run`/`uv export`, it builds in PEP 517 isolation and never syncs the project env, so there is no lockfile to freeze.)
  - runs with `SUPABASE_URL: ""` and `BLOOM_AGENT_KEY: ""` (matching the sibling test step), proving the lazy-validation contract — `import bloom_mcp.*` must succeed with no usable Supabase env. (Empty string, not literal unset, is the accurate description and matches the sibling step; for the import-only contract the two are equivalent because all env validation is deferred to `validate_env()` and never runs at import.)
- **ADD** a regression-guard unit test (`tests/unit/test_bloommcp_wheel_import_gate.py`) that parses `pr-checks.yml` and asserts the `python-audit` job contains a step which (a) runs `uv build` in `bloommcp`, (b) imports **all four** of `bloom_mcp`, `bloom_mcp.tools`, `bloom_mcp.storage`, `bloom_mcp.server` from a `--no-project` env, and (c) pins `SUPABASE_URL` / `BLOOM_AGENT_KEY` empty — so the gate cannot be silently deleted or quietly narrowed. It reuses the `_iter_steps` / `_logical_lines` helpers from `tests/unit/test_ci_workflow_uv_conventions.py` (backslash-continuation joining matters — the import line is multi-line) and matches on step *presence*, never a fixed index. This is the TDD vehicle: it fails (red) before the workflow step exists and passes (green) after. Runs inside the existing `Run Python unit tests` step (`uv run --extra test pytest tests/unit/`) — no new job.
- **EXTEND** the `bloommcp-packaging` spec with one ADDED requirement — "CI Gates the Built-Wheel Import" — so the wheel-import scenario gains a CI-enforcement clause. ADDED-only, so the delta composes cleanly with PR #313 regardless of merge order.
- **VERIFY** `bloommcp/dist/` is not committed (`.gitignore:20` already lists `dist/`); the task confirms it and the guard does not require committing artifacts.

## Impact

- **Affected specs**: `bloommcp-packaging` (ADDED only — composes with PR #313's in-flight capability).
- **Affected code**:
  - `.github/workflows/pr-checks.yml` — one step added to the `python-audit` job.
  - `tests/unit/test_bloommcp_wheel_import_gate.py` — new file.
- **Affected CI**: no new job. The build+import runs in `python-audit` (already has `setup-uv`); the guard test runs in the existing unit-test step. Adds one `uv build` (seconds) to that job.
- **uv-convention guard interaction**: the existing `tests/unit/test_ci_workflow_uv_conventions.py` forbids `--with` only on lines that contain BOTH `uv run` AND `pytest` (Invariant 3). The clean-import line uses `uv run ... --with <wheel> python -c` — no `pytest` — so it is permitted. Invariant 1 (no `pip install uv`) and Invariant 2 (no `setup-python` alongside uv) are also satisfied.
- **Dependency on #313**: the `src/bloom_mcp` layout and `[tool.uv.build-backend]` config this gate exercises exist only on PR #313's branch. This change is stacked on `eberrigan/bloommcp-tier0-baseline` and **cannot merge before #313** (the `uv build` config and the anchor step don't exist on `staging`).
- **`pr-checks.yml` conflict on rebase-onto-staging (expected)**: #313 edits the same `python-audit` hunk this change appends to. While stacked there is no conflict, but after #313 merges to `staging` (esp. via squash/rebase → new SHAs), retargeting + rebasing this branch onto `staging` will conflict in `pr-checks.yml`. Resolution is mechanical: keep **both** the merged `Run bloom_mcp package tests` step and the new wheel-import step. Do not rely on GitHub auto-retarget; assume a manual rebase (see tasks.md §4). The spec delta itself is ADDED-only and composes cleanly regardless of merge order — only the YAML hunk overlaps.

## Non-goals

- Not building or scanning a Docker image — the `docker-build` job already builds/Trivy-scans the bloommcp image; this is a pure wheel-import gate.
- Not editing `bloommcp/pyproject.toml` packaging config — that is #313's deliverable; this change only exercises it.
- Not editing `openspec/project.md` or `CLAUDE.md` (the latter is managed by `openspec update`).
