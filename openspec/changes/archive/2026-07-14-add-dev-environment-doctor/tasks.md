# Tasks — add-dev-environment-doctor

TDD throughout: each check gets a failing test (RED) before the shell
implementation (GREEN). Tests shell out to `scripts/doctor.sh` with the
overridable env vars from `design.md` so no real `/mnt/c`, Postgres, or missing
toolchain is required. Every positive-path test **constructs its own `PATH` of
stub tools** — the `python-audit` CI job (where `tests/unit/` runs) has only
`uv`+`git`, not `node`/`npm`/`supabase`/`docker`/`make`. `test_doctor.py` skips
when `sh` is unavailable (native-Windows dev), mirroring the `skipif` guard in
`test_makefile_migrate_local.py`.

_Verified: 33 unit tests pass; `shellcheck --shell=sh scripts/doctor.sh` clean
(run in WSL, 2026-07)._

## 1. Doctor scaffold + Windows-filesystem check
- [x] 1.1 (RED) `tests/unit/test_doctor.py` with `skipif sh missing`: `/mnt/`
  repo under WSL exits non-zero + names `/mnt/` and the remedy.
- [x] 1.2 (GREEN) `scripts/doctor.sh` (POSIX sh) severity framework + `/mnt/`
  check; honors `DOCTOR_SKIP=1`.
- [x] 1.3 macOS/Linux (`DOCTOR_WSL=0`) skips the fs check; clean env exits 0.

## 2. Required-tool presence + Windows-leak checks
- [x] 2.1 Parametrized over the six tools: each missing → non-zero + named.
- [x] 2.2 A `/mnt/`-resolved tool (via `DOCTOR_MNT_PREFIX`) → WARN, exit 0.
- [x] 2.3 (GREEN) required-tool ERROR + `/mnt/` leak WARN implemented.
- [x] 2.4 (post-#440 review, Benfica) docker-daemon-reachable ERROR (`docker info`)
  so a stopped daemon is caught before `docker compose up` —
  + test `test_docker_daemon_down_is_error`.

## 3. supabase-version (+ pin source of truth), host-port, CRLF
- [x] 3.1 `tests/unit/test_supabase_version_pin.py`: `.supabase-version` ==
  workflow `SUPABASE_VERSION` (drift guard).
- [x] 3.2 Added `.supabase-version` (`2.92.1`).
- [x] 3.3 version ≠ pin → WARN; == → no warn; missing pin file → skipped.
- [x] 3.4 occupied `DOCTOR_PORT` (socket held open) → WARN; free → no warn.
- [x] 3.5 CRLF fixture under `minio/init/` AND `volumes/db/` → WARN each;
  LF-only → none.
- [x] 3.6 (GREEN) version/host-port/CRLF checks implemented.

## 4. Precedence + clean-repo self-guard
- [x] 4.1 ERROR + WARN co-occur → non-zero AND both printed.
- [x] 4.2 Self-guard: real repo tree, tools stubbed, `DOCTOR_WSL=0` → exit 0
  (catches a CRLF regression in the committed init scripts).
- [x] 4.3 (GREEN) severity framework satisfies both.

## 5. `dev-up` preflight wiring + CI guard (single commit)
- [x] 5.1 `tests/unit/test_makefile_doctor.py`: `doctor` target exists; `dev-up`
  runs `scripts/doctor.sh` BEFORE `docker compose up` (positional assertion).
- [x] 5.2 `tests/unit/test_ci_dev_stack_smoke.py`: the `dev-stack-smoke`
  `make dev-up` step sets `DOCTOR_SKIP=1`.
- [x] 5.3 (GREEN) `make doctor` target + `dev-up` wiring + `DOCTOR_SKIP=1` on the
  CI `dev-stack-smoke` `make dev-up` step.
- [x] 5.4 Behavioral: `make dev-up` with a forced doctor ERROR aborts before the
  frontend/compose steps (never reaches "Checking frontend dependencies").

## 6. DEV_SETUP.md + roadmap rewrite + regression guard (supersedes PR #424)
- [x] 6.1 Strengthened `tests/unit/test_dev_setup_doc.py`: no `~/minio` token,
  no `MINIO_DATA_PATH=/Users`; references `make doctor`, `DOCTOR_SKIP`,
  `./volumes/minio-dev`; phantom-target guard still holds.
- [x] 6.2 Rewrote `DEV_SETUP.md`: corrected MinIO section (auto-created buckets,
  default `./volumes/minio-dev`, no `mkdir ~/minio`, writability cross-ref to
  `project.md`), fixed the 3 stale `~/minio` refs, added toolchain-inside-WSL +
  `/mnt/c` leak warning + "why ext4" rationale, `make doctor` preflight +
  `DOCTOR_SKIP`, single `POSTGRES_HOST_PORT` note, bumped footer.
- [x] 6.3 Updated `bloommcp/docs/roadmap.md` `/mnt/c` rationale.

## 7. Validate + verify
- [x] 7.1 `openspec validate add-dev-environment-doctor --strict` passes.
- [x] 7.2 `shellcheck --shell=sh scripts/doctor.sh` clean.
- [x] 7.3 `pytest` on the five test files green (33 passed). _pre-commit runs in
  Step 8 (/pre-merge)._
- [x] 7.4 Fresh-clone behavior validated live in the 2026-07-09 reproduction +
  encoded in the `test_real_repo_init_scripts_are_lf` self-guard.
- [x] 7.5 Merged as #440 (CI green); PR #424 closed as superseded; the stale
  onboarding docs handled — `.claude/commands/docs-review.md` corrected and the
  unused `.serena/` directory removed (both folded into #440).
