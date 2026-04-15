## 1. bloommcp: Migrate to pyproject.toml + uv.lock

- [x] 1.1 Create `bloommcp/pyproject.toml` with all deps from `requirements.txt` — explicitly include `statsmodels>=0.14.0` and `umap-learn>=0.5.0` (scientifically critical)
- [x] 1.2 Create `bloommcp/.python-version` with `3.11`
- [x] 1.3 Run `uv lock` in `bloommcp/` to generate `uv.lock`; verify `umap-learn` + `numpy` versions are compatible
- [x] 1.4 Delete `bloommcp/requirements.txt`
- [x] 1.5 Verify: `uv sync --frozen` installs successfully in a clean venv

## 2. video-worker: Migrate to pyproject.toml + uv.lock

- [x] 2.1 Create `services/video-worker/pyproject.toml` with deps: psycopg2-binary, boto3, pillow, numpy
- [x] 2.2 Create `services/video-worker/.python-version` with `3.11`
- [x] 2.3 Run `uv lock` in `services/video-worker/` to generate `uv.lock`
- [x] 2.4 Delete `services/video-worker/requirements.txt`
- [x] 2.5 Verify: `uv sync --frozen` installs successfully in a clean venv

## 3. langchain: Migrate to pyproject.toml + uv.lock

- [x] 3.1 Create `langchain/pyproject.toml` with all deps from `requirements.txt` (do NOT include the 7 unused data-science packages from Dockerfile line 15)
- [x] 3.2 Create `langchain/.python-version` with `3.11`
- [x] 3.3 Run `uv lock` in `langchain/` to generate `uv.lock`
- [x] 3.4 Delete `langchain/requirements.txt`
- [x] 3.5 Verify: `uv sync --frozen` installs successfully in a clean venv

## 4. Update Dockerfiles to use uv

- [x] 4.1 Update `bloommcp/Dockerfile`:
  - Add `COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/`
  - Add `ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"`
  - Keep system apt deps (gcc, libfreetype6-dev, etc.) BEFORE uv sync (needed for native extensions)
  - COPY `pyproject.toml`, `uv.lock`, `.python-version` BEFORE source code (layer caching)
  - Replace `pip install -r requirements.txt` with `RUN uv sync --frozen --no-dev --no-cache`
  - `COPY . .` AFTER dep install
  - `chown -R bloom:bloom /opt/venv` so bloom user can write cache files (required by numba via umap-learn)
- [x] 4.2 Update `langchain/Dockerfile`:
  - Same uv migration as 4.1
  - Remove the inline `RUN pip install --no-cache-dir pandas matplotlib scipy numpy scikit-learn seaborn statsmodels` entirely
  - No system apt deps needed (no native extensions in langchain deps)
- [x] 4.3 Verify: `docker build -f bloommcp/Dockerfile -t bloommcp:test ./bloommcp` succeeds
- [x] 4.4 Verify: `docker build -f langchain/Dockerfile -t langchain:test ./langchain` succeeds
- [x] 4.5 Verify bloom user can access venv and import packages:
  - `docker run --rm --entrypoint python bloommcp:test -c "import fastmcp; import statsmodels; import umap; print('ok')"`
  - `docker run --rm --entrypoint python langchain:test -c "import langchain; print('ok')"`
- [x] 4.6 Verify removed deps are absent from langchain:
  - `docker run --rm --entrypoint python langchain:test -c "import pandas" 2>&1 | grep -q "ModuleNotFoundError" && echo "PASS" || echo "FAIL"`

## 5. Add .dockerignore files

- [x] 5.1 Create `langchain/.dockerignore` (exclude `.git`, `__pycache__`, `.venv`, `.env*`, `*.pyc`, `.mypy_cache`, `.pytest_cache`)
- [x] 5.2 Create `bloommcp/.dockerignore` (same patterns)

## 6. Update CI (pr-checks.yml)

- [x] 6.1 Add `astral-sh/setup-uv@v7` step to `python-audit` job, replacing `pip install uv && uv pip install --system pip-audit`
- [x] 6.2 Update langchain audit: `cd langchain && uv export --frozen --no-hashes | uvx pip-audit -r /dev/stdin`
- [x] 6.3 Update bloommcp audit: `cd bloommcp && uv export --frozen --no-hashes | uvx pip-audit -r /dev/stdin`
- [x] 6.4 Add video-worker audit: `cd services/video-worker && uv export --frozen --no-hashes | uvx pip-audit -r /dev/stdin`
- [x] 6.5 Update `compose-health-check` job: replace `pip install uv` (line 327) with `astral-sh/setup-uv@v7` step; `uv run` already works
- [x] 6.6 Verify YAML syntax: run `actionlint .github/workflows/pr-checks.yml`

## 7. Update Makefile

- [x] 7.1 Replace `python3 -m pip install --quiet supabase pandas` with `uv run --with supabase,pandas -- python3` in load-test-data target
- [x] 7.2 Replace `python3 -m pip install --quiet supabase` with `uv run --with supabase -- python3` in upload-test-images, create-bucket, and list-buckets targets

## 8. Update dependabot.yml

- [x] 8.1 Change `package-ecosystem: "pip"` to `"uv"` for `/langchain` and `/bloommcp` entries
- [x] 8.2 Add new `uv` ecosystem entry for `/services/video-worker`

## 9. Update .claude/commands/

- [x] 9.1 Update `ci-debug.md`: replace all `pip-audit -r requirements.txt` references with `uv export --frozen --no-hashes | uvx pip-audit -r /dev/stdin`; update troubleshooting to reference `pyproject.toml` and `uv.lock`
- [x] 9.2 Update `run-ci-locally.md`: same audit command updates; replace `uv pip freeze > requirements.txt` with `uv lock`; replace `uv pip install --upgrade` with `uv add`
- [x] 9.3 Update `pre-merge.md`: replace audit commands
- [x] 9.4 Update `lint.md`: update `python-audit` job description to reference `uvx pip-audit` and lockfiles
- [x] 9.5 Update `pr-description.md`: replace `requirements.txt` references with `pyproject.toml`; replace `pip-audit` reference with `uvx pip-audit`
- [x] 9.6 Update `release.md`: replace `requirements.txt` reference with `pyproject.toml`/`uv.lock`

## 10. Validation

- [x] 10.1 Docker build both images and run as bloom user, confirming key imports work (repeat of 4.5 as final regression check)
- [x] 10.2 `grep -rn "requirements.txt" langchain/ bloommcp/ services/video-worker/ .github/workflows/ .github/dependabot.yml Makefile .claude/commands/` returns no hits
- [x] 10.3 `grep -rn "pip install" langchain/Dockerfile bloommcp/Dockerfile .github/workflows/pr-checks.yml Makefile` returns no hits (except comments explaining the migration)
- [x] 10.4 Confirm all three `uv.lock` files are committed

## 11. Address Copilot review feedback on PR #126

Four items surfaced by Copilot's automated review of the initial PR. All are legitimate improvements that strengthen reproducibility and cross-platform support.

- [x] 11.1 Pin the uv Docker image by immutable digest in `bloommcp/Dockerfile`:
  - Change `COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/` to `COPY --from=ghcr.io/astral-sh/uv:0.11@sha256:b1e699368d24c57cda93c338a57a8c5a119009ba809305cc8e86986d4a006754 /uv /uvx /bin/`
  - Matches the existing digest-pin pattern on the Python base image (`python:3.11-slim@sha256:...`)
- [x] 11.2 Same digest pin in `langchain/Dockerfile`
- [x] 11.3 Rebuild both images to verify the digest resolves:
  - `docker build -f bloommcp/Dockerfile -t bloommcp:digest-test ./bloommcp`
  - `docker build -f langchain/Dockerfile -t langchain:digest-test ./langchain`
  - Both must succeed without network resolution of the `0.11` tag
- [x] 11.4 Create `scripts/check-uv-locks.py` — a cross-platform helper that runs `uv lock --check` in each Python service directory and exits non-zero if any service's lockfile is out of sync. Replaces the `bash -c ... for svc in ... done` pattern in the pre-commit hook so the hook works on Windows without requiring Git Bash.
- [x] 11.5 Update `.pre-commit-config.yaml` `uv-lock-check` hook:
  - Change `language: system` to `language: python` (pre-commit's built-in Python runner, cross-platform)
  - Change `entry:` from the inline bash one-liner to `entry: python scripts/check-uv-locks.py`
  - Keep the `files:` filter and `pass_filenames: false` as-is
- [x] 11.6 Add a `uv` preflight check to each Makefile target that uses `uv run --with ...`:
  - Pattern: `@command -v uv >/dev/null 2>&1 || (echo "Error: uv is required. Install: https://docs.astral.sh/uv/getting-started/installation/" && exit 1)`
  - Apply to: `load-test-data`, `upload-images`, `create-bucket`, `list-buckets`
  - Matches the old pattern of checking for Python deps before proceeding
- [x] 11.7 Verify: `uvx pre-commit run uv-lock-check --all-files` still passes on branch state after the hook rewrite
- [x] 11.8 Verify: `make load-test-data` (or a dry-run equivalent) surfaces a clear error if `uv` is uninstalled — test by temporarily removing `uv` from PATH