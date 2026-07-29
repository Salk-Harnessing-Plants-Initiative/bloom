# Add a Preflight Environment Doctor and Fix Windows/WSL2 Onboarding

## Why

A from-scratch Windows onboarding reproduction (2026-07-09) surfaced three
environment traps that silently break local setup for a new developer, none of
which the existing tooling catches *before* the stack fails confusingly:

1. **Repo on the Windows filesystem (`/mnt/c`).** MinIO's data bind mount fails
   with `lstat /data: input/output error` / "drive is faulty" (Docker Desktop
   cannot reliably serve MinIO's low-level disk I/O from the Windows drive), and
   on stale checkouts `minio/init/create-buckets.sh` runs with CRLF
   (`set: -\r: invalid option`). Only a Linux-ext4 clone under WSL2 works.
2. **Toolchain installed on Windows, not in WSL.** `node`/`npm` resolved from
   `/mnt/c/Program Files/nodejs` inside Ubuntu, so `make dev-up`'s host
   `npm install` used the wrong runtime. `uv`/`node`/`supabase` must live inside
   the WSL distro.
3. **A WSL-relayed Postgres on host port 5432.** `make migrate-local` connected
   to the wrong Postgres and failed with `password authentication failed for
   user "supabase_admin"`. The fix is `POSTGRES_HOST_PORT=5433`.

The existing health check (`make check` → `scripts/check_health.py`) only
validates the **already-running** stack. There is no preflight that inspects the
developer's environment *before* bring-up, so these traps appear as opaque
downstream failures. `DEV_SETUP.md`'s MinIO Storage Setup section is also stale
(tells developers to `mkdir ~/minio` and set `MINIO_DATA_PATH` to a `/Users/...`
path, contradicting the committed `./volumes/minio-dev` default and the
auto-creating `minio-init` service). This change **supersedes the small open PR
#424**, which fixed only that MinIO section.

## What Changes

- **ADD `make doctor`** — a dependency-light POSIX-`sh` preflight
  (`scripts/doctor.sh`) that inspects the developer environment and classifies
  findings as hard errors (exit non-zero) or advisories (exit 0):
  - **ERROR**: repo on a Windows mount (`/mnt/...`) under WSL; a required tool
    (`uv`, `node`, `npm`, `supabase`, `make`, `docker`) missing from `PATH`.
  - **WARN**: a toolchain binary resolving from a Windows mount (leak);
    `supabase` CLI version ≠ the pinned `SUPABASE_VERSION`; the configured
    `POSTGRES_HOST_PORT` already in use before bring-up; CRLF in bind-mounted
    init scripts (`minio/init/*.sh`, `volumes/db/**`).
  - Windows/WSL-only checks no-op on macOS/Linux.
- **WIRE `make dev-up`** to run the doctor first: a hard error aborts before
  `docker compose` with an actionable message; advisories print and bring-up
  continues. A `DOCTOR_SKIP=1` escape hatch is provided.
- **REWRITE the Windows/WSL2 parts of `DEV_SETUP.md`**: surface `make doctor` as
  the first step; require installing the toolchain **inside** WSL Ubuntu (call
  out the `/mnt/c` npm leak); explain **why** the Linux-ext4 clone is mandatory
  (MinIO `/data` I/O error primary, CRLF secondary); and correct the MinIO
  Storage Setup section (buckets auto-create via `minio-init`; `MINIO_DATA_PATH`
  defaults to `./volumes/minio-dev`; drop the `mkdir ~/minio` / `/Users/...`
  guidance). **Supersedes PR #424.**

## Impact

- **Affected specs**: `development-environment` — one **ADDED** requirement
  (Preflight Environment Doctor) and one **MODIFIED** requirement (Accurate
  Cross-Platform Setup Documentation).
- **Affected code/docs**:
  - new `scripts/doctor.sh` (POSIX sh);
  - new `.supabase-version` (committed source of truth for the pinned `supabase`
    CLI version, read by the doctor and the docs);
  - `Makefile` (`doctor` target + `dev-up` preflight wiring);
  - `.github/workflows/pr-checks.yml` — set `DOCTOR_SKIP=1` on the
    `dev-stack-smoke` job's `make dev-up` step (that job runs `make dev-up`
    verbatim, so the preflight would otherwise run — and could fail — in CI);
  - `DEV_SETUP.md` rewrite (Windows/WSL2 + MinIO sections; documents `DOCTOR_SKIP`);
  - `bloommcp/docs/roadmap.md` — update the `/mnt/c` rationale line (CRLF was the
    stated cause; MinIO `/data` I/O error is now primary);
  - new `tests/unit/test_doctor.py`; new `tests/unit/test_makefile_doctor.py`;
    extend `tests/unit/test_dev_setup_doc.py` and
    `tests/unit/test_ci_dev_stack_smoke.py`.
- **Superseded**: PR #424 — close (unmerged) once this change is approved; add
  `Supersedes #424` to this PR's body.
- **Deferred follow-ups (human-applied — subagents cannot edit these paths)**:
  stale `mkdir minio_data` / `chmod 777` / `cp .env.dev.example` / `npm run
  init-env` walkthroughs in `.claude/commands/docs-review.md`, and a stale MinIO
  `mkdir` in `.serena/memories/suggested_commands.md`. Tracked here so they aren't
  lost; not edited by this change.
- **Out of scope**: the `species_illustrations` bucket rename (PR #261) and any
  DB clean-init role/password work. This change adds a diagnostic + docs only; it
  does not modify compose, DB init, or MinIO init behavior (it only *reads* those
  scripts for the CRLF check).
