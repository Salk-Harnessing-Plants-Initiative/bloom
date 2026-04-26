## Context

Bloom has three Python services (`langchain/`, `bloommcp/`, and `services/video-worker/`) that declare dependencies with minimum-version floors (`>=`) in `requirements.txt`. Docker builds resolve versions at build time, producing non-reproducible images. CI pip-audit only scans direct dependencies, missing transitive vulnerabilities.

The project already uses `uv` in CI (installed via `pip install uv`) but only as a tool installer, not for dependency management.

`services/video-worker/` is a systemd service (not containerized) — it runs from `/opt/bloom/venv/` on the host. It still benefits from `pyproject.toml` + `uv.lock` for reproducible venv installs.

### Prior Art (Salk repos)

Patterns observed in sleap, lablink, ariadne, and sleap-roots inform these decisions:
- All use `astral-sh/setup-uv` GitHub Action (v5–v7) for CI uv installation
- lablink uses `COPY --from=ghcr.io/astral-sh/uv:0.10` + `UV_PROJECT_ENVIRONMENT` in Dockerfiles
- ariadne uses `uv sync --frozen` as lockfile drift guard
- ariadne uses `uvx pip-audit` for security scanning
- lablink has Dependabot configured with `pip` ecosystem for uv-managed packages

## Goals / Non-Goals

- Goals:
  - Reproducible Docker builds via pinned lockfiles
  - Full transitive dependency auditing in CI
  - Adopt uv as the sole Python package manager (pyproject.toml + uv.lock)
  - Remove unused dependencies from langchain service
  - Ensure bloom user has access to installed packages in Docker containers
  - Cross-platform developer tooling (no shell-specific assumptions)
- Non-Goals:
  - Changing compose files or service architecture
  - Touching JavaScript/frontend code (JS pinning deferred within issue #106)
  - Adding new Python dependencies
  - Changing Python version (staying on 3.11)
  - Adopting uv workspace (future direction, see issue #25; compatible with per-service lockfiles)

## Decisions

- **pyproject.toml over requirements.txt**: uv's native format. Supports dependency groups, metadata, and generates `uv.lock` with full resolution.
- **uv.lock committed to git**: The lockfile is the source of truth for reproducible installs. `uv sync --frozen` in Docker refuses to re-resolve.
- **.python-version file**: uv convention for pinning the Python version. All services pin `3.11`.
- **`astral-sh/setup-uv` pinned to commit SHA in CI**: Use `astral-sh/setup-uv@<commit-sha>  # v7.x.y` in both `pr-checks.yml` and `deploy.yml` rather than `@v7`. GitHub Actions tags are mutable — a compromised or hostile maintainer could retag `v7` to a malicious commit. The same consistency argument we apply to the uv Docker image digest pin (see below) applies to CI actions, and we already pin `gitleaks@6efcfbaa...` by SHA in `.pre-commit-config.yaml`. Replaces `pip install uv`. Standard across Salk repos.
- **Digest-pinned `ghcr.io/astral-sh/uv:0.11@sha256:b1e699368d24c57cda93c338a57a8c5a119009ba809305cc8e86986d4a006754` in Docker**: Pin uv to latest stable minor (`0.11`) **plus an immutable content digest**. The tag alone is a mutable pointer that floats across 0.11.x patches, so pinning only to the tag is indistinguishable from pulling `latest` for reproducibility purposes. We already digest-pin the Python base image (`python:3.11-slim@sha256:...`); the uv image gets the same treatment so both stages of the multi-stage COPY are bit-identical across rebuilds. The digest is bumped deliberately alongside version bumps, same pattern as the Python base image. Dependabot's `docker` ecosystem tracks these and proposes updates automatically.
- **`UV_PROJECT_ENVIRONMENT=/opt/venv` in Docker**: Installs the venv OUTSIDE `/app` so dev compose bind-mounts (`./langchain:/app`, `./bloommcp:/app`) don't shadow installed packages. This is the key insight from lablink's pattern.
- **`ENV PATH="/opt/venv/bin:$PATH"`**: Ensures `CMD ["python", ...]` and `CMD ["uvicorn", ...]` resolve to the venv Python, not system Python. Without this, the containers crash at runtime.
- **Docker layer ordering**: COPY `pyproject.toml` + `uv.lock` + `.python-version` first, run `uv sync --frozen --no-dev --no-cache`, then `COPY . .`. System apt packages (gcc, libfreetype6-dev, etc. in bloommcp) must be installed BEFORE `uv sync` so native extensions can compile.
- **`--no-cache` flag in Docker**: Prevents uv from writing to `~/.cache/uv`, which would bloat the image layer.
- **bloom user access**: `uv sync` runs as root and installs to `/opt/venv`. The venv is then `chown -R bloom:bloom /opt/venv` so the bloom user can write cache files next to package files. This matters for libraries like `numba` (used transitively by `umap-learn`) that write `__pycache__` and JIT cache files next to their `.py` files — a read-only venv causes runtime crashes with errors like `cannot cache function: no locator available`. World-readable is not sufficient; write access is required.
- **`uvx pip-audit@<version>` pinned in CI**: Runs pip-audit as a tool without installing it (ariadne pattern), but pinned to a specific version (`uvx pip-audit@2.10.0` or similar) rather than `uvx pip-audit` alone which resolves the latest published version at CI run time. Dependabot (via `package-ecosystem: "uv"`) tracks updates to the lockfiles but not to this inline version; bumping is a deliberate workflow-file change. Audit via `uv export --frozen --no-hashes | uvx pip-audit@<version> -r /dev/stdin`.
- **Lockfile drift detection**: `uv sync --frozen` already fails if `uv.lock` doesn't match `pyproject.toml`. No additional guard needed — keep it simple.
- **Portable `uv-lock-check` pre-commit hook**: The pre-commit hook runs a checked-in Python script (`scripts/check-uv-locks.py`) via `language: python` rather than a `bash -c ...` one-liner. The earlier bash-based implementation depended on `bash` being on the contributor's `PATH`, which is not guaranteed on Windows (it works on Git Bash, but fails on pure PowerShell/cmd.exe). Using Python keeps the hook cross-platform without adding a shell-detection branch to the config, and the script is reusable from the command line for manual verification.
- **`uv` preflight check in Makefile targets**: Each Makefile target that calls `uv run --with ...` first runs `command -v uv >/dev/null || (echo "Error: uv is required. Install: https://docs.astral.sh/uv/getting-started/installation/" && exit 1)` so a missing `uv` produces an actionable error message instead of a generic `uv: command not found`. Matches the pattern already used by the old `pip` targets that checked for successful install.
- **Remove inline deps from langchain/Dockerfile**: The 7 data-science packages (pandas, matplotlib, scipy, numpy, scikit-learn, seaborn, statsmodels) are not imported anywhere in the langchain service (verified by grep across all .py files, including string references in tool definitions). Removing them.
- **Makefile updates**: Replace `python3 -m pip install --quiet supabase` calls with `uv run --with supabase` (and `uv run --with supabase,pandas` where pandas is needed).
- **Dependabot ecosystem update**: Switch from `package-ecosystem: "pip"` to `"uv"` so Dependabot can parse `uv.lock` and create PRs for transitive dependency updates. Add entry for `services/video-worker`.
- **Claude commands update**: 6 files reference `requirements.txt` and `pip-audit -r requirements.txt`. Update to use `uv export --frozen --no-hashes | uvx pip-audit -r /dev/stdin` and reference `pyproject.toml`/`uv.lock`.
- **Unit tests for `scripts/check-uv-locks.py`**: The script is a commit-time gate with four distinct branches (`uv` missing, missing `pyproject.toml` skip, lockfile drift, clean pass). Tested via `tests/unit/test_check_uv_locks.py` with `pytest` + `monkeypatch` on `shutil.which` and `subprocess.run`. Tests MUST be written before the implementation is modified (TDD) so the `subprocess.run` timeout addition is test-driven, and regressions are caught by CI. Unit tests run under the same `compose-health-check` CI job via `uv run --with pytest` (no new CI job required). Matches the project's stated TDD-going-forward standard.
- **`timeout=120` on subprocess.run in `check-uv-locks.py`**: A hung `uv lock --check` (network partition mid-resolve, stuck git lock file, etc.) must not wedge the pre-commit hook forever. 120 seconds is well above the normal execution time (~10ms per service on a warm cache, ~2s on cold) and below a developer's tolerance threshold. On timeout, `subprocess.TimeoutExpired` surfaces with a clear error and the script exits non-zero.
- **Trivy action version alignment in `deploy.yml`**: `deploy.yml` uses `aquasecurity/trivy-action@0.28.0` (legacy semver) while `pr-checks.yml` uses `@v0.35.0`. The drift is pre-existing, but since this PR already modifies `deploy.yml`, aligning the version removes a latent bug-for-bug-difference between the two workflows. No behavior change expected — v0.35.0 is the same action family, just more recent and consistent with PR-check behavior.
- **Dockerfile install-as-bloom restructuring**: Create the `bloom` user and pre-create `/opt/venv` + `/app/data/...` dirs with bloom ownership in a single RUN layer BEFORE the venv install. Then `USER bloom`, then `COPY --chown=bloom:bloom pyproject.toml ...`, then `RUN uv sync ...` (now runs as the non-root bloom user against the already-bloom-owned venv), then `COPY --chown=bloom:bloom . .`. Replaces the trailing `chown -R bloom:bloom /app /opt/venv` layer entirely. Benefits: (1) source-only changes no longer invalidate the ~200MB recursive chown of `/opt/venv`, (2) principle-of-least-privilege — `uv sync` no longer runs as root during build, (3) one fewer layer. `COPY --chown=` is stable vanilla Docker (since 17.06), NOT BuildKit-specific. System `apt-get` steps (bloommcp's gcc/libfreetype; langchain's nodejs) still run as root — they precede the USER switch. Rejected alternative: `RUN --chown=bloom:bloom uv sync ...` — invalid syntax, `--chown` is a COPY/ADD flag only, not a RUN flag (BuildKit's documented RUN flags are `--mount`, `--network`, `--security`). Rejected alternative: keep trailing `chown -R` and only move `adduser` earlier — small cache win but still rewrites the chown layer on every source change.
- **`pre-merge.md` `/tmp/reqs.txt` cleanup via trap**: The current pattern `... || exit 1; rm -f /tmp/reqs.txt` leaks the temp file if the loop exits early. Replace with `trap "rm -f /tmp/reqs.txt" EXIT` at the start of the block so cleanup runs on both success and failure paths.

## Risks / Trade-offs

- **uv.lock format changes**: uv lockfile format may evolve. Mitigation: lockfile is re-generable from pyproject.toml at any time.
- **Developer workflow change**: Developers must use `uv add`/`uv remove` instead of editing requirements.txt. Mitigation: simpler workflow, well-documented.
- **video-worker has no container**: Its lockfile is only used for manual host installs. This is acceptable — reproducibility still improved.
- **Dependabot `uv` ecosystem is newer**: May have less coverage than `pip` ecosystem. Mitigation: pip-audit in CI catches what Dependabot misses.
- **Digest pinning requires manual bumps**: Unlike tag-only pins, the digest doesn't automatically pick up patch releases within a minor version. Mitigation: Dependabot's `docker` ecosystem is already configured and will propose bumps. Acceptable trade-off for reproducibility guarantees.
- **Python hook depends on `uv` being on PATH inside pre-commit's isolated env**: `language: python` still needs to exec `uv lock --check` as a subprocess. uv must be installed on the contributor's machine. Mitigation: this was already the case with the bash version; preflight instructions in `.claude/commands/pre-merge.md` and `validate-env.md` already call this out.
- **Scientific reproducibility of new lockfile versions not asserted**: The migration pins exact versions across 3 services, including bleeding-edge scientific packages (numba 0.65.0 + numpy 2.4.4 published 3 days apart; **pandas 3.0.2 with Copy-on-Write enabled by default**). Numerical outputs from `bloommcp` analyses could silently drift versus the pre-migration state. The highest-likelihood silent-breakage vector is pandas CoW: `DataFrame.iloc` assignment, `groupby().apply()` mutation, and chained assignment on trait tables all behave differently under CoW-default than under pandas 2.x — what previously mutated a shared view now silently copies, which can flip analytic results without raising an error. Secondary vectors: numba's LLVM version changed JIT output shape on some kernels; numpy 2.4's stricter casting rules may raise on code that silently downcast before. Mitigation: filed as follow-up issue #141 ("numerical reproducibility smoke test") — a fixed-seed UMAP embedding + reference pandas trait computation with **tolerance-based assertions** (`np.allclose(atol=1e-6)` on Procrustes distance; cluster-label stability via ARI, NOT bit-exact golden file) since UMAP is not bit-reproducible across BLAS/numba-LLVM combinations even with `random_state`. Not in this PR's scope because it requires picking a reference dataset, computing known-good outputs, and adding a new pytest dependency footprint. The current PR ships strictly-better reproducibility than the pre-migration state (exact pins vs `>=` floors), just without an automated numerical assertion.

## Migration Plan

1. Create `pyproject.toml` + `uv.lock` + `.python-version` in each service directory
2. Update Dockerfiles to use uv (digest-pinned) with `UV_PROJECT_ENVIRONMENT=/opt/venv` (outside bind-mount path)
3. Verify bloom user can import packages in running containers
4. Update CI to use `astral-sh/setup-uv@v7`, `uvx pip-audit`, fix compose-health-check step
5. Update Makefile to use `uv run --with` with preflight checks
6. Update `dependabot.yml` to use `uv` ecosystem
7. Update `.claude/commands/` files with new audit commands
8. Add `scripts/check-uv-locks.py` and wire pre-commit hook to it
9. Delete `requirements.txt` files
10. Rollback: if uv sync fails in Docker, revert Dockerfile changes and restore requirements.txt (both are in git history)

## Open Questions

None — all clarified with the user.