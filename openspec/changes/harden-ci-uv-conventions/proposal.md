## Why

The `validate-env-defaults` job in `.github/workflows/pr-checks.yml:89-104` violates three uv-conventions established by the `pin-python-deps` change (#126, merged to main 2026-04-21):

1. Installs `uv` via `pip install uv` — explicitly forbidden by the "any CI job needs uv" scenario in the `python-dependency-management` spec.
2. Redundantly invokes `actions/setup-python@v5` alongside `pip install uv`. Per the spec, `setup-uv` is the single Python manager per job (it reads `.python-version` automatically).
3. Runs pytest via `uv run --with pytest` instead of `uv run --extra test`, bypassing the root `pyproject.toml`'s `test` extra — the single source of truth used by `python-audit` and `compose-health-check`.

The regression slipped past review on PR #144 (merged into `staging` 2026-04-24). The proximate cause was the `Merge main` sync commit in #144 on 2026-04-24 — which landed AFTER #126 had already standardized the rest of `pr-checks.yml` on main — that re-resolved the `pr-checks.yml` conflict by retaining #144's pre-standardization four-step block instead of converging on the SHA-pinned `setup-uv` pattern. Nothing in the existing CI pipeline actively tests workflow files for spec compliance, so the divergence was invisible during review.

This change closes the gap by adding an automated regression-guard test, fixing the offending job, and making the conventions explicit as ADDED requirements so they appear in `openspec show python-dependency-management` after archive.

## What Changes

- **ADD** a unit test (`tests/unit/test_ci_workflow_uv_conventions.py`) that parses every `.github/workflows/*.yml` and `*.yaml` file using PyYAML and asserts three invariants:
  - No `run:` step (anywhere) installs uv via pip (`pip install uv`, with any flags or prefixes — but not packages whose names start with `uv-`).
  - No job that uses uv (any step `uses: astral-sh/setup-uv@...` OR a `run:` command whose first token is `uv`) also contains an `actions/setup-python@` step.
  - Any `run:` block that contains BOTH `uv run` AND `pytest` MUST use `--extra test` and MUST NOT pass `--with` (which would bring in pytest or other test deps outside the locked test environment).
- **EXTEND** the `python-dependency-management` spec with two new ADDED requirements (no MODIFIED, so the delta composes cleanly with PR #160 regardless of merge order):
  - "CI workflow uv conventions SHALL be enforced by an automated test"
  - "Test dependencies in CI SHALL come from the root pyproject.toml test extra"
- **FIX** `.github/workflows/pr-checks.yml:89-104` (`validate-env-defaults` job) by replacing the four-step `setup-python` + `pip install uv` + `uv run --with pytest` block with the SHA-pinned 2-step pattern already used by `python-audit` (`pr-checks.yml:114-123`) and `compose-health-check` (`pr-checks.yml:292-293`).
- **ADD** `pyyaml>=6` to the root `pyproject.toml` `test` extra (one line) — required by the new test for robust YAML-tree traversal of workflow files.

## Impact

- **Affected specs**: `python-dependency-management` (ADDED only — composes with PR #160's canonical-spec archive).
- **Affected code**:
  - `.github/workflows/pr-checks.yml` — 4 steps replaced with 2 steps in one job.
  - `tests/unit/test_ci_workflow_uv_conventions.py` — new file.
  - `pyproject.toml` — add `pyyaml>=6` to the `test` extra.
- **Affected CI**: the new test runs inside the existing `Run Python unit tests` step in `python-audit` (`uv run --extra test pytest tests/unit/`); no new job, no new step.
- **Test-driven**: the three test cases must FAIL on the unfixed workflow before any production-code edit, then PASS after the workflow fix. See `tasks.md`.
- **Enforcement**: the `python-audit` job IS in `staging`'s required status checks, so the regression-guard test will block non-admin merges. Note that `staging` branch protection has `enforce_admins: false` (only `main` enforces against admins), so an admin can technically merge a violating PR to staging — this proposal does not change that posture; it makes the violation visible to reviewers.

## Non-goals

- Not editing `openspec/project.md` — the project-context doc is largely stale; the spec is authoritative. A separate `/docs-review` pass should refresh it.
- Not editing `CLAUDE.md` — managed by `openspec update`.
- Not refactoring other workflow files — `deploy.yml` already conforms; the regression-guard test will catch any future drift across all workflows.
- Not adding a root `.python-version`. The four uv-using jobs all rely on uv's default Python resolution (`>=3.11` from `pyproject.toml`) and could in principle drift across patch versions between CI runs. This was true before this PR and remains true after; converging on a root pin is a separate cross-cutting change, not a regression-fix.
- Not folding in #156 (Trivy SHA + pip-audit transitive deps). Related supply-chain hardening, intentionally separate proposal.
- Not in-scope: composite/reusable workflow actions (`uses: ./.github/actions/...` or `uses: org/repo/.github/workflows/...`). The regression-guard test scans top-level workflow files only; if a composite action internally runs `pip install uv`, this test will not catch it. There are no composite actions in the repo today; if one is added, expanding the scan is a follow-up.
