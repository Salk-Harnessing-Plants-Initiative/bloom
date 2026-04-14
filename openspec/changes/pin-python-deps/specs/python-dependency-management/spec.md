## ADDED Requirements

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

### Requirement: Docker builds SHALL install from lockfile using uv

Each Python service Dockerfile SHALL copy the `uv` binary from a pinned version of the official image (`ghcr.io/astral-sh/uv:0.11`) and install dependencies via `uv sync --frozen --no-dev --no-cache`. The venv SHALL be installed to `/opt/venv` (via `UV_PROJECT_ENVIRONMENT`) to avoid conflicts with dev compose bind-mounts on `/app`. `PATH` SHALL include `/opt/venv/bin` so CMD resolves to the venv Python. The venv SHALL be chowned to the non-root application user (`bloom:bloom`) so runtime libraries can write cache files (e.g., numba JIT cache, `__pycache__`). No `pip install` commands SHALL remain for application dependencies. The dependency layer SHALL be cached independently of source code changes.

#### Scenario: Reproducible Docker build

- **WHEN** `docker build` is run for a Python service
- **THEN** the exact versions from `uv.lock` are installed
- **AND** no network resolution occurs (frozen install)

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
1. Install uv via the `astral-sh/setup-uv@v7` GitHub Action (not via `pip install uv`)
2. Export pinned versions from `uv.lock` via `uv export --frozen --no-hashes` and pipe to `uvx pip-audit` for vulnerability scanning
3. Audit all three services: `langchain/`, `bloommcp/`, and `services/video-worker/`

The `compose-health-check` CI job SHALL also use `astral-sh/setup-uv@v7` instead of `pip install uv`.

#### Scenario: Transitive dependency with known CVE

- **WHEN** a transitive dependency in `uv.lock` has a known vulnerability
- **THEN** `uvx pip-audit` SHALL report it in the CI output

#### Scenario: CI detects lockfile drift

- **WHEN** a PR modifies `pyproject.toml` without regenerating `uv.lock`
- **THEN** `uv sync --frozen` SHALL fail and the CI job SHALL not pass

#### Scenario: CI installs uv via setup-uv action

- **WHEN** any CI job needs uv
- **THEN** uv is installed via `astral-sh/setup-uv@v7`
- **AND** pip is NOT used to install uv

### Requirement: Python service directories SHALL have .dockerignore files

Both `langchain/` and `bloommcp/` SHALL contain `.dockerignore` files that exclude development artifacts (`.git`, `__pycache__`, `.venv`, `.env*`, `*.pyc`, `.mypy_cache`, `.pytest_cache`) from Docker build context. `services/video-worker/` does not need a `.dockerignore` (no Dockerfile exists).

#### Scenario: Development artifacts excluded from Docker context

- **WHEN** `docker build` is run for a Python service
- **THEN** `.venv/`, `__pycache__/`, and `.env*` files are NOT copied into the image

### Requirement: Makefile SHALL use uv instead of pip for Python package management

All `pip install` calls in the Makefile SHALL be replaced with `uv run --with <packages>` to maintain consistency with the uv-based workflow.

#### Scenario: Developer loads test data

- **WHEN** a developer runs `make load-test-data`
- **THEN** uv is used to install and run the script, not pip

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
