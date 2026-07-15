# Design — add-dev-environment-doctor

## Doctor script language: POSIX `sh`, not Python

`scripts/check_health.py` is Python run via `uv run` — but it runs *after* the
stack is up, when `uv` is guaranteed present. The doctor runs at the *earliest*
moment, and one of its own checks is "is `uv` installed?". It therefore MUST NOT
depend on `uv`, Python, or Node. **Decision:** implement as a **POSIX `sh`
script** (`scripts/doctor.sh`) using only shell builtins, coreutils, and the
`docker`/`supabase` CLIs it is probing for. This lets it run on a machine with
nothing but `sh` + `make` and still report what's missing. The script itself is
covered by `.gitattributes` `*.sh text eol=lf`.

## Severity policy

| Check | Condition | Severity |
|---|---|---|
| Windows filesystem | repo path under `/mnt/` on WSL | **ERROR** |
| Required tools | `uv`/`node`/`npm`/`supabase`/`make`/`docker` missing from PATH | **ERROR** |
| Toolchain leak | a required binary resolves under `/mnt/` | WARN |
| supabase version | `supabase --version` ≠ pinned `SUPABASE_VERSION` | WARN |
| Host port in use | configured `POSTGRES_HOST_PORT` already listening pre-up | WARN |
| CRLF init scripts | CR bytes in `minio/init/*.sh` or `volumes/db/**` | WARN |

ERROR ⇒ exit non-zero (aborts `dev-up`). WARN ⇒ printed, exit 0. Rationale for
fail-vs-warn: `/mnt/` and missing tools *cannot* produce a working stack; the
port collision and CRLF have clean workarounds (`POSTGRES_HOST_PORT` override;
`.gitattributes` already forces LF on fresh clones), so they are advisory.

## Cross-platform guarding

The WSL/Windows-only checks (filesystem, leak) run only when WSL is detected —
`/proc/version` contains `microsoft`/`WSL`, or the repo path is under `/mnt/`.
On macOS/Linux they are skipped, so the doctor is a clean no-op there and never
produces platform-inappropriate noise.

## `dev-up` integration

`make dev-up` runs `scripts/doctor.sh` as its first recipe step. Because
ERROR ⇒ non-zero exit and `make` stops on a failed recipe line, a hard error
aborts before `docker compose up`. Advisories do not change the exit code, so
bring-up proceeds. `DOCTOR_SKIP=1` bypasses the preflight entirely (for CI, where
the environment is known-good, and for power users).

## Testability

`scripts/doctor.sh` reads its inputs from overridable env vars so tests drive
each branch deterministically without a real `/mnt/c`, Postgres, or toolchain:

- `DOCTOR_REPO_PATH` (default `pwd`) — the path classified for the `/mnt/` check.
- `DOCTOR_WSL` (default auto-detected) — force WSL on/off for cross-platform tests.
- `DOCTOR_PORT` (default resolved `POSTGRES_HOST_PORT`) — the port probed.
- `DOCTOR_SCAN_ROOT` (default repo root) — where the CRLF scan looks.
- `PATH` — tests prepend fake tool dirs (including a fake `/mnt/`-rooted `node`)
  to exercise the missing-tool and leak branches.

`tests/unit/test_doctor.py` shells out to the script under these controlled
conditions and asserts exit code + message substrings — mirroring the subprocess
style of `tests/unit/test_makefile_migrate_local.py`, and running in CI's
`python-audit` unit job (no running stack required).

## Host-port check heuristic (and its limits)

The doctor warns when the configured `POSTGRES_HOST_PORT` is already accepting
TCP connections *before* the stack is up (something else owns it). It cannot
perfectly distinguish "a foreign Postgres" from "our own stack already running",
so it only ever **warns** (never errors) and is explicitly documented as a
heuristic. This is enough to steer a developer to `POSTGRES_HOST_PORT=5433`
before the confusing `migrate-local` auth failure.

## Supabase version pin: single source of truth

The doctor's version check needs the pinned `supabase` CLI version, but today
`SUPABASE_VERSION` lives *only* in `.github/workflows/pr-checks.yml` and
`deploy.yml` — a local developer has no file to read. Decision: commit a
one-line `.supabase-version` file as the canonical pin; the doctor reads it (and
`DEV_SETUP.md` cites it). To prevent drift, a unit test asserts the workflow
`SUPABASE_VERSION` values equal `.supabase-version`. The workflows keep their
literal env value (no workflow-logic refactor); the test is the drift guard. If
`.supabase-version` is somehow absent, the version check is skipped (it is
advisory-only anyway).

## CI integration: skip the preflight in `dev-stack-smoke`

The `dev-stack-smoke` job in `pr-checks.yml` runs `make dev-up` verbatim, so
wiring the doctor into `dev-up` makes it run on every PR against a **required**
status check. All of the doctor's meaningful checks are either no-ops on a clean
Linux runner (the `/mnt/`/WSL checks) or already provisioned by that job (the
tool checks) — so it adds no CI value there, only risk. Decision: set
`DOCTOR_SKIP=1` on that job's `make dev-up` step **in the same commit** that wires
the preflight, and extend `tests/unit/test_ci_dev_stack_smoke.py` to assert the
guard is present (so a future edit can't silently un-skip it). This keeps the
doctor's false-abort surface off CI entirely.

## Static analysis: shellcheck

`scripts/doctor.sh` becomes a hard gate on `make dev-up` and must run under a bare
POSIX `sh`, where quoting/word-splitting/bashism bugs hide. The repo has no
shellcheck today. Decision: add `shellcheck --shell=sh scripts/doctor.sh` as a
verification step (task 6) to catch bashisms that would break the
"runs on a machine with nothing but `sh`" guarantee. A repo-wide shellcheck
pre-commit hook is deferred (it would need auditing the existing `scripts/*.sh`).

## Out of scope

The `species_illustrations` bucket rename (PR #261) and any DB clean-init
role/password behavior are separate efforts. This change does not modify
`docker-compose.dev.yml`, DB init scripts, or `minio/init/create-buckets.sh`
(beyond reading them for the CRLF check).
