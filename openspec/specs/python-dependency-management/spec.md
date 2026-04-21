# python-dependency-management Specification

## Purpose
TBD - created by archiving change pin-python-deps. Update Purpose after archive.
## Requirements
### Requirement: Python services SHALL use pyproject.toml and uv.lock for dependency management

Each Python service (`langchain/`, `bloommcp/`, `services/video-worker/`) SHALL declare dependencies in `pyproject.toml` using uv-compatible format with minimum version floors. A committed `uv.lock` file SHALL pin all direct and transitive dependency versions.

#### Scenario: Developer adds a new dependency

- **WHEN** a developer runs `uv add <package>` in a service directory
- **THEN** `pyproject.toml` is updated with the new dependency
- **AND** `uv.lock` is regenerated with the resolved version and all transitive deps

#### Scenario: Lockfile is out of sync with pyproject.toml

- **WHEN** `pyproject.toml` declares a dependency not present in `uv.lock`
- **THEN** `uv sync --frozen` SHALL fail, preventing silent version drift

### Requirement: Python version SHALL be pinned via .python-version file

Each Python service directory SHALL contain a `.python-version` file specifying `3.11` as the required Python version.

#### Scenario: uv respects pinned Python version

- **WHEN** `uv lock` or `uv sync` is run in a service directory
- **THEN** resolution targets the Python version specified in `.python-version`

### Requirement: Docker builds SHALL install from a digest-pinned uv image via lockfile

Each Python service Dockerfile SHALL copy the `uv` binary from the official image pinned to **both a version tag and an immutable SHA256 digest** (`ghcr.io/astral-sh/uv:<version>@sha256:<digest>`) — matching the digest-pin pattern already used for the Python base image. Dependencies SHALL be installed via `uv sync --frozen --no-dev --no-cache`. The venv SHALL be installed to `/opt/venv` (via `UV_PROJECT_ENVIRONMENT`) to avoid conflicts with dev compose bind-mounts on `/app`. `PATH` SHALL include `/opt/venv/bin` so CMD resolves to the venv Python. The venv SHALL be chowned to the non-root application user (`bloom:bloom`) so runtime libraries can write cache files (e.g., numba JIT cache, `__pycache__`). No `pip install` commands SHALL remain for application dependencies. The dependency layer SHALL be cached independently of source code changes.

#### Scenario: Reproducible Docker build

- **WHEN** `docker build` is run for a Python service
- **THEN** the exact versions from `uv.lock` are installed
- **AND** no network resolution occurs (frozen install)

#### Scenario: uv image pinned to immutable digest

- **WHEN** `docker build` runs the `COPY --from=ghcr.io/astral-sh/uv:<version>@sha256:<digest>` instruction
- **THEN** the image layer is fetched by content hash, not by mutable tag
- **AND** two builds separated in time produce bit-identical uv binaries as long as the digest in the Dockerfile is unchanged

#### Scenario: Unused inline dependencies removed from langchain

- **WHEN** the langchain Docker image is built
- **THEN** the 7 data-science packages (pandas, matplotlib, scipy, numpy, scikit-learn, seaborn, statsmodels) that were previously installed inline SHALL NOT be present

#### Scenario: bloom user can access installed packages

- **WHEN** a Docker container runs as the `bloom` user
- **THEN** Python can import all declared dependencies from `/opt/venv`
- **AND** the `python` and `uvicorn` commands resolve to `/opt/venv/bin/`
- **AND** libraries that write cache files next to package files (e.g., numba via umap-learn) succeed without permission errors

#### Scenario: Dev compose bind-mount does not shadow venv

- **WHEN** docker-compose.dev.yml mounts host source to `/app`
- **THEN** the venv at `/opt/venv` is unaffected and all packages remain importable

### Requirement: CI SHALL validate lockfile freshness and audit transitive dependencies

The `python-audit` CI job SHALL:
1. Install uv via the `astral-sh/setup-uv` GitHub Action, pinned per the "CI actions SHALL be pinned to immutable commit SHAs" requirement below (not via `pip install uv`)
2. Export pinned versions from `uv.lock` via `uv export --frozen --no-hashes` and pipe to `uvx pip-audit` for vulnerability scanning, with `pip-audit` version-pinned per the "CI security-scanning tools SHALL be pinned to specific versions" requirement below
3. Audit all three services: `langchain/`, `bloommcp/`, and `services/video-worker/`

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

### Requirement: Pre-commit hook SHALL detect lockfile drift cross-platform

The `.pre-commit-config.yaml` SHALL include a local `uv-lock-check` hook that runs `uv lock --check` in each Python service directory whenever `pyproject.toml`, `uv.lock`, or `.python-version` is modified. The hook SHALL NOT depend on `bash` being available on the contributor's `PATH` (which is not guaranteed on Windows without Git Bash). The hook SHALL invoke a checked-in Python script via `language: python` so it runs under pre-commit's managed Python environment on all platforms.

#### Scenario: Contributor on Windows commits a pyproject.toml change

- **WHEN** a contributor on a Windows machine with no `bash` in PATH runs `git commit` after editing `langchain/pyproject.toml` without regenerating `langchain/uv.lock`
- **THEN** the `uv-lock-check` hook SHALL run successfully via pre-commit's Python runner
- **AND** the hook SHALL fail the commit with a clear error pointing at the drifted service

#### Scenario: Hook passes on a clean branch state

- **WHEN** `uvx pre-commit run uv-lock-check --all-files` is invoked on a branch where all three services' lockfiles match their `pyproject.toml`
- **THEN** the hook reports `Passed` and exits 0

### Requirement: Python service directories SHALL have .dockerignore files

Both `langchain/` and `bloommcp/` SHALL contain `.dockerignore` files that exclude development artifacts (`.git`, `__pycache__`, `.venv`, `.env*`, `*.pyc`, `.mypy_cache`, `.pytest_cache`) from Docker build context. `services/video-worker/` does not need a `.dockerignore` (no Dockerfile exists).

#### Scenario: Development artifacts excluded from Docker context

- **WHEN** `docker build` is run for a Python service
- **THEN** `.venv/`, `__pycache__/`, and `.env*` files are NOT copied into the image

### Requirement: Makefile SHALL use uv with an explicit preflight check

All `pip install` calls in the Makefile SHALL be replaced with `uv run --with <packages>` to maintain consistency with the uv-based workflow. Each Makefile target that invokes `uv` SHALL perform a preflight check that produces an actionable error message if `uv` is not installed, so developers get a clear install hint rather than a generic `command not found` message.

#### Scenario: Developer loads test data with uv installed

- **WHEN** a developer runs `make load-test-data` with `uv` on PATH
- **THEN** uv is used to install and run the script, not pip
- **AND** the script executes successfully

#### Scenario: Developer runs a make target without uv installed

- **WHEN** a developer runs `make load-test-data` (or any uv-dependent target) with `uv` NOT on PATH
- **THEN** the target fails early with a clear error message that includes the `uv` install URL (`https://docs.astral.sh/uv/getting-started/installation/`)
- **AND** no cryptic `uv: command not found` leaks from an inner shell invocation

### Requirement: Dependabot SHALL use uv ecosystem for Python dependency updates

The `.github/dependabot.yml` file SHALL use `package-ecosystem: "uv"` (not `"pip"`) for all Python service directories so Dependabot can parse `uv.lock` and propose updates for transitive dependencies.

#### Scenario: Dependabot creates PR for outdated transitive dependency

- **WHEN** a transitive dependency in `uv.lock` has a newer version available
- **THEN** Dependabot SHALL create a PR updating `uv.lock` with the new resolution

### Requirement: Claude commands SHALL reference uv-based audit and dependency workflows

All `.claude/commands/` files that reference `requirements.txt` or `pip-audit` SHALL be updated to reference `pyproject.toml`, `uv.lock`, and `uvx pip-audit` with `uv export`.

#### Scenario: Developer runs CI debug command

- **WHEN** a developer invokes the `ci-debug` Claude command
- **THEN** the audit instructions reference `uv export --frozen --no-hashes | uvx pip-audit -r /dev/stdin`, not `pip-audit -r requirements.txt`

### Requirement: Pre-commit hook script SHALL have unit test coverage

The `scripts/check-uv-locks.py` script SHALL have unit tests in `tests/unit/test_check_uv_locks.py` that cover every distinct control-flow branch: `uv` missing from PATH, a service directory with no `pyproject.toml` (skip), lockfile drift detected, clean pass, and a subprocess timeout. Tests SHALL run in the `python-audit` CI job (which already installs uv via `astral-sh/setup-uv` and has no `continue-on-error`), placed before or alongside the pip-audit steps so unit-test failures block the PR and return feedback within the audit job's runtime rather than waiting on the slower `compose-health-check` pipeline.

#### Scenario: uv missing exits with install-hint code

- **WHEN** `shutil.which("uv")` returns `None`
- **THEN** the script prints an error that includes the uv install URL
- **AND** exits with code 127 (standard "command not found")

#### Scenario: Service directory missing pyproject.toml is skipped

- **WHEN** one of the service directories has no `pyproject.toml`
- **THEN** the script logs a skip message for that service
- **AND** does not fail the hook

#### Scenario: Lockfile drift is detected

- **WHEN** `uv lock --check` returns non-zero for any service
- **THEN** the script exits with code 1
- **AND** surfaces the drifting service name in the error message

#### Scenario: Clean tree passes

- **WHEN** all service lockfiles are in sync with their `pyproject.toml`
- **THEN** the script exits with code 0 and produces no error output

#### Scenario: Hung subprocess times out

- **WHEN** `uv lock --check` runs longer than the configured timeout (120 seconds) for one service
- **THEN** the `subprocess.TimeoutExpired` is caught, the timed-out service name is appended to the `failed` list, and processing continues with the next service so one stuck service does not block the rest
- **AND** the script exits with code 1 after all services are processed
- **AND** the timed-out service name appears in the final error message alongside any other drifted services

### Requirement: CI actions SHALL be pinned to immutable commit SHAs

GitHub Actions tags are mutable — a compromised maintainer can retag `v7` to point at a malicious commit. Any third-party GitHub Action used by `pr-checks.yml` or `deploy.yml` for Python dependency management (including `astral-sh/setup-uv`) SHALL be pinned to a full 40-character commit SHA with a version comment. This matches the existing SHA-pinning pattern for `gitleaks` in `.pre-commit-config.yaml`.

#### Scenario: setup-uv pinned by SHA, not tag

- **WHEN** a CI job references `astral-sh/setup-uv`
- **THEN** the reference is of the form `astral-sh/setup-uv@<40-char-sha>  # v7.x.y`
- **AND** the tag `@v7` alone is NOT used

#### Scenario: Dependabot proposes SHA bumps

- **WHEN** `astral-sh/setup-uv` releases a new version
- **THEN** Dependabot's `github-actions` ecosystem proposes a PR that updates both the SHA and the trailing version comment
- **AND** the bump is reviewed explicitly rather than absorbed silently

### Requirement: CI security-scanning tools SHALL be pinned to specific versions

`uvx pip-audit` without a version specifier resolves the latest published version at CI run time, meaning CI behavior can change without a code change. The CI pip-audit invocation in `pr-checks.yml` and `deploy.yml` SHALL pin to a specific version (`uvx pip-audit@<version>`). Bumping the pin SHALL be a deliberate, reviewed workflow-file change rather than an implicit resolution-time decision.

#### Scenario: pip-audit version pinned in CI

- **WHEN** a CI job invokes `pip-audit` via `uvx`
- **THEN** the invocation is `uvx pip-audit@<version>` (e.g., `@2.10.0`)
- **AND** bare `uvx pip-audit` is NOT used

#### Scenario: pip-audit pin bumps are reviewed

- **WHEN** a developer wants to pick up a newer `pip-audit` release
- **THEN** the version is changed in the workflow file via an explicit PR
- **AND** the change appears in the PR diff (not silently absorbed at CI run time)

_Note: the one-time alignment of `aquasecurity/trivy-action@0.28.0` in `deploy.yml` with `@v0.35.0` in `pr-checks.yml` is handled as a tasks.md config fix (12.6). It is not elevated to a SHALL requirement because "alignment" is a point-in-time state, not a recurring behavior; the pinning discipline above covers `pip-audit` where the recurring risk actually lives._

### Requirement: Claude command temp-file usage SHALL use trap-based cleanup

Any Claude command that creates a temporary file during a loop (e.g., the `pre-merge.md` `/tmp/reqs.txt` pattern used for per-service pip-audit) SHALL register a `trap "rm -f <path>" EXIT` at the start of the block so the file is cleaned up on both success and failure paths. The prior pattern of placing `rm -f` only on the success path leaked temp files when a loop iteration exited early.

#### Scenario: Audit loop exits early on failure

- **WHEN** the pre-merge audit loop fails on the first service
- **THEN** the EXIT trap fires and removes the temp file
- **AND** no stale `/tmp/reqs.txt` remains on the developer's machine

### Requirement: Dockerfiles SHALL install dependencies as the non-root application user

Each Python service Dockerfile SHALL create the `bloom` user, pre-create `/opt/venv` and any `/app/data/...` directories with bloom ownership, and switch to `USER bloom` BEFORE running `uv sync` and BEFORE copying application source. `COPY` steps that bring `pyproject.toml`, `uv.lock`, `.python-version`, and application source into the image SHALL use `COPY --chown=bloom:bloom` so ownership is set at copy time. No trailing `RUN chown -R bloom:bloom /app /opt/venv` layer SHALL remain. System-level `apt-get` steps that require root MAY precede the `USER bloom` switch.

#### Scenario: Source-only change does not invalidate the venv chown

- **WHEN** a Python source file changes but `pyproject.toml` and `uv.lock` do not
- **THEN** `docker build` reuses the cached `uv sync --frozen` layer
- **AND** the build does NOT walk and rewrite ownership of the ~hundreds-of-MB site-packages tree
- **AND** only the final `COPY --chown=bloom:bloom . .` and subsequent layers are rebuilt

#### Scenario: uv sync runs as non-root during build

- **WHEN** `docker build` executes the `RUN uv sync --frozen --no-dev --no-cache` step
- **THEN** the step runs under `USER bloom`, not as root
- **AND** the installed venv at `/opt/venv` is owned by `bloom:bloom` at the moment it is written

#### Scenario: No trailing recursive chown layer exists

- **WHEN** `docker history` is inspected for a built image
- **THEN** no standalone `RUN chown -R bloom:bloom /opt/venv` or `RUN chown -R bloom:bloom /app` layer exists
- **AND** the venv and app source are nonetheless owned by `bloom:bloom` at runtime

