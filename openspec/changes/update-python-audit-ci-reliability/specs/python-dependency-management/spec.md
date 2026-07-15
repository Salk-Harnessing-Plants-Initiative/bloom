## MODIFIED Requirements

### Requirement: CI SHALL validate lockfile freshness and audit transitive dependencies

The `python-audit` CI job SHALL:
1. Install uv via the `astral-sh/setup-uv` GitHub Action, pinned per the "CI actions SHALL be pinned to immutable commit SHAs" requirement below (not via `pip install uv`)
2. Export pinned versions from `uv.lock` via `uv export --frozen --no-hashes` and pipe to `uvx pip-audit` for vulnerability scanning, with `pip-audit` version-pinned per the "CI security-scanning tools SHALL be pinned to specific versions" requirement below
3. Audit all three services: `langchain/`, `bloommcp/`, and `services/video-worker/`

The job SHALL set `timeout-minutes: 20`, matching the cap already used by `docker-build` (30m), `compose-health-check` (30m), and `dev-stack-smoke` (20m) in the same workflow, so a hang in any step — the unit tests, the bloommcp/bloomcli suites, the wheel build, or the pip-audit calls themselves — fails visibly within a bounded window instead of running until GitHub Actions' 360-minute default job cap.

The `compose-health-check` CI job SHALL also use the SHA-pinned `astral-sh/setup-uv` action instead of `pip install uv`.

#### Scenario: Transitive dependency with known CVE

- **WHEN** a transitive dependency in `uv.lock` has a known vulnerability
- **THEN** `uvx pip-audit` SHALL report it in the CI output

#### Scenario: CI detects lockfile drift

- **WHEN** a PR modifies `pyproject.toml` without regenerating `uv.lock`
- **THEN** `uv sync --frozen` SHALL fail and the CI job SHALL not pass

#### Scenario: CI installs uv via setup-uv action

- **WHEN** any CI job needs uv
- **THEN** uv is installed via the `astral-sh/setup-uv` action, pinned to a 40-char commit SHA with a trailing `# v7.x.y` comment
- **AND** pip is NOT used to install uv
- **AND** the bare `@v7` tag reference is NOT used

#### Scenario: A step inside python-audit hangs

- **WHEN** any step within the `python-audit` job stalls and produces no further output
- **THEN** GitHub Actions cancels the job after 20 minutes
- **AND** the job is reported as failed rather than left running for up to 6 hours

## ADDED Requirements

### Requirement: bloommcp full-fixture oracle tests SHALL be tiered as integration tests and excluded from per-PR CI

`bloommcp/pyproject.toml` SHALL declare a `markers` entry for `integration` under `[tool.pytest.ini_options]` with the description `"integration: full-fixture statsmodels/umap oracle tests over turface_19 — slow, intermittently stalls in CI containers; excluded from per-PR CI, run via /pre-merge or pytest -m integration"`. This description is specific to bloommcp — it is deliberately NOT copied verbatim from the root `pyproject.toml`'s (`"marks tests that require the compose stack to be running"`) or `bloomcli/pyproject.toml`'s (`"requires live staging credentials..."`) `integration` markers, since bloommcp's reason for the marker (slow, intermittently-stalling numeric-drift oracle; no external infra dependency) is unrelated to either of those. The following tests in `bloommcp/tests/test_oracle.py` — which run `statsmodels` MixedLM heritability and `numba`-JIT UMAP over the full 19-genotype `turface_19` fixture, and are known to intermittently stall in CI containers — SHALL be marked `@pytest.mark.integration`:
- `test_external_library_heritability_matches_recorded_oracle`
- `test_delegated_heritability_returns_wrapper_consumed_keys`
- `test_external_library_umap_is_deterministic_and_structural`
- `test_umap_trustworthiness_floor_rejects_wrong_parameters`

The `Run bloom_mcp package tests` step in the `python-audit` CI job SHALL invoke `uv run --frozen --extra test pytest tests/ -m "not integration" -v --tb=short` (preserving the existing `--frozen --extra test` flags and `SUPABASE_URL`/`BLOOM_AGENT_KEY` env vars), matching the exclusion pattern already used by the `Run bloomctl package tests` step in the same job.

#### Scenario: Per-PR CI skips the full-fixture oracle tests

- **WHEN** a PR triggers the `python-audit` job
- **THEN** the `Run bloom_mcp package tests` step excludes tests marked `integration`
- **AND** the four full-fixture heritability/UMAP tests do not run

#### Scenario: Marked tests still run when explicitly requested

- **WHEN** a developer runs `cd bloommcp && uv run --extra test pytest tests/ -m integration -v --tb=short`
- **THEN** the four integration-marked oracle tests execute normally

#### Scenario: Exactly the intended four tests carry the marker

- **WHEN** a developer runs `cd bloommcp && uv run --extra test pytest tests/ -m integration --collect-only -q`
- **THEN** exactly the four named tests are collected — no fewer (which would indicate a typo'd or dropped marker silently leaving a stalling test in the per-PR `not integration` bucket) and no more (which would indicate marker scope creep)

### Requirement: The Pre-Merge command SHALL run the bloommcp integration-marked oracle tests

`.claude/commands/pre-merge.md` SHALL reference a step that runs the bloommcp tests marked `integration` (the full-fixture heritability/UMAP oracle tests excluded from per-PR CI) from all three of its checklist surfaces — the "Step 2: Python Audit" section, the "Quick Pre-Merge (Minimum)" section, and the final "Pre-Merge Checklist" — so numeric drift in the delegated `statsmodels`/`umap` calls is still caught before merge regardless of which checklist path a developer follows, even though the tests no longer block every PR's CI.

#### Scenario: Developer follows the full /pre-merge Step 2

- **WHEN** a developer follows the `/pre-merge` checklist's "Step 2: Python Audit" section
- **THEN** the section includes running `cd bloommcp && uv run --extra test pytest tests/ -m integration -v --tb=short`
- **AND** following it exercises the four full-fixture oracle tests that per-PR CI skips

#### Scenario: Developer follows the Quick Pre-Merge (Minimum) path

- **WHEN** a developer follows only the "Quick Pre-Merge (Minimum)" section (the sanctioned fast path for small changes)
- **THEN** that section also references the bloommcp integration-marked test run
- **AND** the developer is not silently left believing the oracle tests were covered by CI when they were excluded from it

#### Scenario: Final Pre-Merge Checklist reflects the exclusion

- **WHEN** a developer reviews the final "Pre-Merge Checklist" before merging
- **THEN** it includes a checklist item for the bloommcp integration-marked oracle tests, distinct from the general "All CI jobs pass" item (since CI no longer runs them)
