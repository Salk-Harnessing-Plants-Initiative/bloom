## Why

Python builds are not reproducible. All three Python services (`langchain/`, `bloommcp/`, `services/video-worker/`) declare dependencies with `>=` floor constraints and no lockfile, so every `docker build` or CI run may resolve different transitive versions. The `langchain/Dockerfile` also installs 7 data-science packages inline that are not used by the service. This creates CVE audit blind spots (transitive deps are not scanned) and makes debugging version-related failures impossible.

## What Changes

- Replace `requirements.txt` with `pyproject.toml` + `uv.lock` in `langchain/`, `bloommcp/`, and `services/video-worker/`
- Add `.python-version` files (uv convention) pinning Python 3.11
- Remove the unused inline `pip install pandas matplotlib scipy numpy scikit-learn seaborn statsmodels` from `langchain/Dockerfile`
- Update both Dockerfiles to install deps via `uv sync --frozen` instead of `pip install`, with `UV_PROJECT_ENVIRONMENT=/opt/venv` to avoid dev compose bind-mount conflicts
- Add `ENV PATH="/opt/venv/bin:$PATH"` so CMD resolves to venv Python
- Add `.dockerignore` files for both `langchain/` and `bloommcp/`
- Update CI (`pr-checks.yml`): use `astral-sh/setup-uv@v7`, audit exported lockfiles with `uvx pip-audit`, update compose-health-check integration test step
- Update Makefile: replace `pip install` calls with `uv run --with`
- Update `dependabot.yml`: switch Python ecosystem from `pip` to `uv`, add `services/video-worker`
- Update `.claude/commands/` files: replace `requirements.txt` and `pip-audit` references with uv equivalents

## Out of Scope

- JS/frontend dependency pinning (`npm ci`, `@supabase/supabase-js` pin) — deferred to a follow-up within issue #106
- Docker Compose service definitions (compose files are not modified; venv location change avoids the need)
- `services/video-worker/` has no Dockerfile (it's a systemd service deployed to host); only `pyproject.toml` + `uv.lock` migration applies. No `.dockerignore` needed.

## Related Changes

- `implement-cicd-pipeline` (0/121 tasks, stale) — overlaps on Python dep management. Should be archived before this proceeds.
- Issue #25 proposes a uv workspace with single root lockfile — future direction, compatible with per-service lockfiles now.

## Impact

- Affected specs: none previously existing (this creates a new `python-dependency-management` capability)
- Affected code:
  - `langchain/requirements.txt` (deleted)
  - `langchain/pyproject.toml` (new)
  - `langchain/uv.lock` (new)
  - `langchain/.python-version` (new)
  - `langchain/Dockerfile` (modified)
  - `langchain/.dockerignore` (new)
  - `bloommcp/requirements.txt` (deleted)
  - `bloommcp/pyproject.toml` (new)
  - `bloommcp/uv.lock` (new)
  - `bloommcp/.python-version` (new)
  - `bloommcp/Dockerfile` (modified)
  - `bloommcp/.dockerignore` (new)
  - `services/video-worker/requirements.txt` (deleted)
  - `services/video-worker/pyproject.toml` (new)
  - `services/video-worker/uv.lock` (new)
  - `services/video-worker/.python-version` (new)
  - `.github/workflows/pr-checks.yml` (modified)
  - `.github/dependabot.yml` (modified)
  - `Makefile` (modified)
  - `.claude/commands/ci-debug.md` (modified)
  - `.claude/commands/run-ci-locally.md` (modified)
  - `.claude/commands/pre-merge.md` (modified)
  - `.claude/commands/lint.md` (modified)
  - `.claude/commands/pr-description.md` (modified)
  - `.claude/commands/release.md` (modified)
