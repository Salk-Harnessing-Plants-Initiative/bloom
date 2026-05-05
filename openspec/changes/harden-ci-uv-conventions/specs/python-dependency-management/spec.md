## ADDED Requirements

### Requirement: CI workflow uv conventions SHALL be enforced by an automated test

An automated unit test (`tests/unit/test_ci_workflow_uv_conventions.py`) SHALL parse every `.github/workflows/*.yml` AND `*.yaml` file in the repository using a YAML parser (e.g., PyYAML's `safe_load`) and fail the test run if any of the following invariants are violated. The test SHALL run as part of the existing `Run Python unit tests` step in the `python-audit` CI job, so a violation surfaces in `python-audit`'s required status check on every PR.

The three invariants are:

1. **No pip-installed uv.** No `run:` step in any workflow job MAY contain a `pip install` invocation whose target is the package `uv`. Token-aware matching (NOT raw substring) — `uv-helper`, `pyuv`, and other hyphenated/prefixed names MUST NOT trigger the rule. Installing uv via pip is forbidden by the existing "CI installs uv via setup-uv action" scenario in this spec; this requirement makes the prohibition machine-checkable.
2. **No setup-python paired with uv.** If a workflow job contains a step whose `uses:` starts with `astral-sh/setup-uv@`, OR a step whose `run:` command's first token (on any non-comment line) is exactly `uv` (i.e., excluding `uvx`, `uv-helper`, comments, and absolute paths), the same job MUST NOT contain a step whose `uses:` starts with `actions/setup-python@`. `setup-uv` reads `.python-version` and provides Python; pairing it with `setup-python` is redundant and creates the exact ambiguity that allowed PR #144's regression to land.
3. **Pytest comes from the test extra.** Any `run:` block that contains BOTH `uv run` AND `pytest` (on the same logical line) MUST contain `--extra test` AND MUST NOT contain `--with`. The `--with` clause in this rule applies only when co-occurring with `pytest` — non-pytest `--with` invocations (e.g., `uvx pip-audit@2.10.0`, or `uv run --with somepkg python ...`) are out of scope for this invariant.

The test SHALL emit failure messages that name the offending workflow file, job name, step index, and step `name:` so the regression is actionable from the CI log alone.

**Scope cut**: the test scans top-level files in `.github/workflows/` only. Composite actions (`.github/actions/*/action.yml`) and reusable workflows referenced via `uses: ./.github/...` or `uses: org/repo/...` are not traversed; if a composite action internally runs `pip install uv`, the regression-guard test will NOT catch it. There are no composite actions in this repository today; this scope cut is intentional and revisable when one is added.

#### Scenario: A workflow step installs uv via pip

- **WHEN** any `run:` step in a `.github/workflows/*.yml` or `*.yaml` file contains, on a non-comment line, the tokens `pip` and `install` followed by the package name `uv` as a standalone token (not `uv-helper` or other hyphenated/prefixed neighbours)
- **THEN** the unit test fails
- **AND** the failure message identifies the workflow file path, the job name, the step index, and the step `name:`
- **AND** the `python-audit` CI job's `Run Python unit tests` step exits non-zero, surfacing the violation in the required status check

#### Scenario: A job pairs actions/setup-python with uv usage

- **WHEN** a single job in `.github/workflows/*.yml` or `*.yaml` contains a step whose `uses:` starts with `actions/setup-python@` AND ALSO contains either a step whose `uses:` starts with `astral-sh/setup-uv@`, or a step whose `run:` has a non-comment line whose first token is exactly `uv`
- **THEN** the unit test fails
- **AND** the failure message identifies the workflow file and the offending job

#### Scenario: A pytest invocation uses --with instead of --extra test

- **WHEN** a `run:` step contains, on the same logical line, the substrings `uv run` AND `pytest` AND `--with`
- **THEN** the unit test fails
- **AND** the failure message identifies the file, job, step index, and offending line

#### Scenario: A non-pytest --with invocation is allowed

- **WHEN** a `run:` step contains `uvx pip-audit@<ver>`, `uv run --with <pkg> python <script>`, or any other `--with` usage that does NOT co-occur with `pytest` on the same line
- **THEN** the unit test passes for that step (the test does not police non-pytest `--with` usage)

#### Scenario: All workflow files conform

- **WHEN** every `.github/workflows/*.yml` and `*.yaml` file satisfies all three invariants
- **THEN** the unit test passes
- **AND** the `python-audit` CI job's `Run Python unit tests` step succeeds

### Requirement: Test dependencies in CI SHALL come from the root pyproject.toml test extra

All CI jobs that invoke `pytest` via `uv run` SHALL use `uv run --extra test pytest <targets> ...`. The `--with <pkg>` pattern SHALL NOT be used to install pytest or any other test-time dependency in CI. The root `pyproject.toml`'s `test` extra is the single source of truth for the project's test toolchain. Using `--with pytest` produces a parallel, unmanaged dependency surface (no lock entry, no audit coverage) that drifts silently from the locked test environment used by `python-audit` and `compose-health-check`.

This requirement applies to pytest specifically; `--with` for non-test toolchain (e.g., `uvx pip-audit@<ver>`) remains permitted.

#### Scenario: A new CI job runs pytest

- **WHEN** a workflow author adds a job whose responsibilities include running `pytest`
- **THEN** the job invokes pytest as `uv run --extra test pytest <targets> ...`
- **AND** the regression-guard test from the previous requirement does not fail

#### Scenario: A workflow author introduces `--with pytest`

- **WHEN** a PR adds or modifies a `run:` step containing `uv run --with pytest`
- **THEN** the regression-guard unit test fails in the `python-audit` job
- **AND** the violation surfaces in the required status check
