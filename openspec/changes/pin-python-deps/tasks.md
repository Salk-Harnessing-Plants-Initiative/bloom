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
## 12. Address 5-subagent review findings on PR #126

Seven items surfaced by a parallel 5-subagent review (Code Quality · Testing · Scientific Rigor · Security · Behavioural Correctness) after round-3 Copilot fixes landed. All legitimate improvements; none blocking. TDD applied where the item is test-shaped (12.1–12.3). Configuration-only items (12.4–12.8) don't have meaningful test shape.

### Test-first items (TDD)

- [x] 12.1a **Refactor `scripts/check-uv-locks.py` for testability** (prerequisite for 12.1):
  - Extract the per-service loop and failure-reporting into a pure function: `def check_services(repo_root: Path, services: Iterable[str] = SERVICES) -> int:` returning 0 (clean), 1 (drift/timeout), or 127 (uv missing — keep this check inside `main` or move to the helper's entry; pick one and document)
  - Have `main()` compute `repo_root = Path(__file__).resolve().parent.parent` and call `check_services(repo_root)`
  - Rationale: Test B ("missing pyproject.toml skips") needs to point `repo_root` at a `tmp_path` with a subset of services. Hardcoded `Path(__file__).resolve().parent.parent` is not monkeypatchable without fragile tricks. Injecting `repo_root` makes the test trivial.
  - Also add `timeout=120` wiring point: the helper is where `subprocess.run` lives, so the timeout in 12.2 lands here.
  - No behavior change for real CLI users (pre-commit hook still calls `python scripts/check-uv-locks.py`)
  - Commit this refactor WITHOUT the timeout change so 12.1 tests can run against a clean, still-pre-12.2 state

- [x] 12.1 **Write pytest for `scripts/check-uv-locks.py`** (tests A-D are characterization — they pass against the 12.1a refactor immediately; test E is the only true RED-first driver for 12.2):
  - Create `tests/unit/test_check_uv_locks.py` and `tests/unit/conftest.py` (shared fixtures if needed)
  - Import the `check_services` helper directly from `scripts.check_uv_locks` (add a `sys.path` shim in conftest, OR rename the script to `check_uv_locks.py` with underscores so it imports cleanly — underscores preferred; update `.pre-commit-config.yaml` and any docs in the same commit if renamed)
  - Use `capsys` (pytest fixture) to capture parent-script stderr — the subprocess itself doesn't use `capture_output=True`, so assertions about "service name in stderr" must target the PARENT script's final error message (lines 65-71 of the current script), not child stderr
  - **Test A** (`test_uv_missing_returns_127`): monkeypatch `shutil.which` to return `None`; assert `main()` returns `127` and `capsys.readouterr().err` contains the install URL
  - **Test B** (`test_missing_pyproject_skips`): pass a `tmp_path` to `check_services()` containing a `langchain/pyproject.toml` but no `bloommcp/pyproject.toml`; monkeypatch `subprocess.run` to return returncode=0 for langchain; assert return value is `0` and skip message for bloommcp appears in captured stdout
  - **Test C** (`test_drift_detected_returns_1`): `tmp_path` with all 3 service pyproject.tomls; monkeypatch `subprocess.run` to return `CompletedProcess(returncode=1)` for one service and 0 for others; assert `check_services()` returns `1` AND `capsys.readouterr().err` contains the drifted service name
  - **Test D** (`test_clean_pass_returns_0`): monkeypatch `subprocess.run` to always return returncode=0; assert `check_services()` returns `0`
  - **Test E** (`test_subprocess_timeout_surfaces`): monkeypatch `subprocess.run` to raise `subprocess.TimeoutExpired(cmd=..., timeout=120)`; assert `check_services()` returns `1`, the service name appears in stderr, AND processing continues to subsequent services (monkeypatch can use a side-effect list: first call raises TimeoutExpired, second+ returns 0; assert all services were attempted). Test E SHOULD FAIL against the 12.1a refactor (no timeout wired yet) — the failure is what drives 12.2.
  - **Test F** (`test_unexpected_subprocess_error_surfaces`): monkeypatch `subprocess.run` to raise `FileNotFoundError("uv disappeared mid-run")`; assert the script fails with non-zero and a clear message (NOT an uncaught traceback). Documents defensive handling of races between `shutil.which` and the actual exec.
  - Run `uv run --with pytest pytest tests/unit/test_check_uv_locks.py -v`; confirm 5 of 6 pass (test E fails; test F may fail too — if so, 12.2 should also add a broad `except subprocess.SubprocessError` or narrower catch)

- [x] 12.2 **Implement `timeout=120` and defensive subprocess error handling in `check_services()`**:
  - Add `timeout=120` keyword argument to the `subprocess.run(["uv", "lock", "--check"], cwd=..., timeout=120)` call in the refactored helper
  - Wrap the call in `try / except subprocess.TimeoutExpired as e:` that prints a clear `timeout: {service} exceeded 120s` message to stderr, appends the service to `failed`, and continues the loop
  - Also catch `FileNotFoundError` (in case `uv` vanishes between the `shutil.which` probe and the actual exec — race) with a message pointing back at the install URL; append to `failed` and continue
  - Re-run `uv run --with pytest pytest tests/unit/test_check_uv_locks.py -v` — all 6 tests now pass (green)
  - Confirm the existing `uvx pre-commit run uv-lock-check --all-files` still passes on the current repo state (no regression)

- [x] 12.3 **Wire unit tests into the `python-audit` CI job**:
  - Add a step to the `python-audit` job in `.github/workflows/pr-checks.yml` (NOT `compose-health-check`): `uv run --with pytest pytest tests/unit/ -v --tb=short`
  - Reason for placement: `compose-health-check` has `continue-on-error: true` (failures don't block PRs) and `needs: docker-build` (~10 min delay). `python-audit` has neither — it runs in parallel, fails fast, and blocks merges. The `astral-sh/setup-uv` step is already present in that job so no new setup is needed.
  - Place the step after `setup-uv` and before the first `uvx pip-audit` step so a failing unit test short-circuits the audit run
  - Confirm via `gh pr view 126 --json statusCheckRollup` (after push) that the step appears and passes

### Configuration-only items (direct changes)

- [x] 12.4 **Pin `astral-sh/setup-uv` to commit SHA in both workflows**:
  - Look up the commit SHA for the current `v7.x.y` tag of `astral-sh/setup-uv` (use `gh api repos/astral-sh/setup-uv/tags`)
  - Replace `astral-sh/setup-uv@v7` with `astral-sh/setup-uv@<sha>  # v7.x.y` in:
    - `.github/workflows/pr-checks.yml` (`python-audit` job; `compose-health-check` job)
    - `.github/workflows/deploy.yml` (Python audit step)
  - Matches the SHA-pin pattern used by `gitleaks` in `.pre-commit-config.yaml`

- [x] 12.5 **Pin `uvx pip-audit` to a specific version**:
  - Choose `uvx pip-audit@2.10.0` or the latest stable at the time of this commit (look up via `gh release list --repo pypa/pip-audit --limit 3`)
  - Replace all three audit steps in `pr-checks.yml` and the audit step in `deploy.yml`: `uvx pip-audit -r /dev/stdin` → `uvx pip-audit@<version> -r /dev/stdin`
  - Dependabot's `uv` ecosystem tracks the lockfiles but not inline `uvx` versions; manual bumps are the trade-off

- [x] 12.6 **Align Trivy action in `deploy.yml`**:
  - Change `aquasecurity/trivy-action@0.28.0` → `aquasecurity/trivy-action@v0.35.0`
  - Confirm by diff: both `pr-checks.yml` and `deploy.yml` now use `v0.35.0`

- [x] 12.7 **Install-as-bloom Dockerfile restructuring (both services)**:
  - In `bloommcp/Dockerfile` and `langchain/Dockerfile`, restructure to: (1) keep apt-get steps as root, (2) add a single RUN layer that creates the bloom user AND pre-creates `/opt/venv` + any `/app/data/...` dirs with `chown -R bloom:bloom`, (3) `WORKDIR /app`, (4) `USER bloom`, (5) `COPY --chown=bloom:bloom pyproject.toml uv.lock .python-version ./`, (6) `RUN uv sync --frozen --no-dev --no-cache`, (7) `COPY --chown=bloom:bloom . .`
  - Delete the trailing `RUN chown -R bloom:bloom /app /opt/venv` line (ownership is now set at copy/install time)
  - Delete the trailing `USER bloom` line (switch already happened earlier)
  - bloommcp: fold the existing `RUN mkdir -p /app/data/SLEAP_OUT_CSV /app/data/PLOTS_DIR /app/data/ANALYSIS_OUTPUT` into the user-creation layer
  - langchain: fold `RUN mkdir -p /app/data/PLOTS_DIR` into the user-creation layer
  - Do NOT use `RUN --chown=...` — that syntax is invalid (`--chown` is a COPY/ADD flag, not a RUN flag)
  - Rebuild both images: `docker build -f bloommcp/Dockerfile -t bloommcp:layer-test ./bloommcp` and same for langchain
  - Confirm smoke tests still pass: `docker run --rm --entrypoint python bloommcp:layer-test -c "import fastmcp; import statsmodels; import umap; print('ok')"`
  - Confirm `docker history bloommcp:layer-test | grep -c "chown -R"` returns 0
  - Confirm the container still runs as bloom: `docker run --rm --entrypoint whoami bloommcp:layer-test` returns `bloom`

- [x] 12.8 **`pre-merge.md` `/tmp/reqs.txt` cleanup via `trap`**:
  - Replace trailing `rm -f /tmp/reqs.txt` in both the main Python Audit block and the Quick Pre-Merge block with `trap "rm -f /tmp/reqs.txt" EXIT` at the start of each block
  - Ensures cleanup runs on both success and failure paths

### Follow-up (filed separately, NOT in this PR)

- [ ] 12.9 **Numerical reproducibility smoke test** — file as a new GitHub issue (NOT a task in this PR):
  - Scope: a pytest that runs a fixed-seed UMAP embedding + a reference pandas trait computation against a small committed input, asserts bit-exact output against a golden file
  - Motivation: catch silent numerical drift from lockfile updates (esp. numba/numpy/pandas)
  - Cross-link: mention this PR (#126) and the 5-subagent review comment as the origin
