# development-environment Spec Delta — add-dev-environment-doctor

## ADDED Requirements

### Requirement: Preflight Environment Doctor

The project SHALL provide a preflight diagnostic, `make doctor` (backed by
`scripts/doctor.sh`), that inspects the developer's environment **before** stack
bring-up and reports actionable findings, and `make dev-up` SHALL run it first as
a preflight. The doctor SHALL be dependency-light — implemented in POSIX `sh` and
MUST NOT require `uv`, Python, or Node, so it can itself report those missing.

Findings SHALL be classified by severity. The following SHALL be **errors** that
cause a non-zero exit (and thus abort `make dev-up`): the repository residing on
a Windows-mounted path (under `/mnt/`) when running under WSL, and any of the
required tools `uv`, `node`, `npm`, `supabase`, `make`, `docker` being absent
from `PATH`. The following SHALL be **advisories** that are printed but do not
change the exit code: a required tool resolving from a Windows mount (a `/mnt/`
leak), the `supabase` CLI version differing from the pinned version recorded in a
committed repository source of truth (`.supabase-version`), the configured
`POSTGRES_HOST_PORT` already being in use before bring-up, and CRLF line endings
in the bind-mounted init scripts (`minio/init/*.sh`, `volumes/db/**`). The CRLF
advisory is a runtime safety net for working trees that predate the
`.gitattributes` LF rules (it *detects* what `.gitattributes` *prevents* on a
fresh clone) — defense in depth, not a duplicate.

The Windows/WSL-specific checks (filesystem location, toolchain leak) SHALL be
skipped on macOS and Linux so the doctor is a clean no-op there. A `DOCTOR_SKIP=1`
escape hatch SHALL bypass the preflight — honored both by `scripts/doctor.sh`
directly and by the `make dev-up` wiring — for use in CI, where the environment
is known-good.

#### Scenario: Repo on the Windows filesystem is a hard error

- **WHEN** `make doctor` runs under WSL with the repository checked out under
  `/mnt/` (e.g. `/mnt/c/repos/bloom`)
- **THEN** it exits non-zero and prints a message naming the `/mnt/` path and the
  remedy (clone into the WSL2 Linux filesystem, e.g. `~/repos/bloom`)

#### Scenario: A missing required tool is a hard error

- **WHEN** `make doctor` runs with any of `uv`, `node`, `npm`, `supabase`,
  `make`, or `docker` absent from `PATH`
- **THEN** it exits non-zero, names the missing tool, and prints its install hint

#### Scenario: A Windows-mount toolchain leak is an advisory

- **WHEN** `make doctor` runs under WSL and a required tool (e.g. `node`)
  resolves to a path under `/mnt/` (installed on Windows, not in the WSL distro)
- **THEN** it prints a warning about the leak but does not, on that basis alone,
  exit non-zero

#### Scenario: supabase version, host-port, and CRLF findings are advisories

- **WHEN** `make doctor` runs and the `supabase` CLI version differs from the
  pinned version in `.supabase-version`, or the configured `POSTGRES_HOST_PORT` is
  already in use, or a bind-mounted init script under `minio/init/` or
  `volumes/db/` contains CRLF line endings
- **THEN** each is reported as a warning (the port warning advises a
  `POSTGRES_HOST_PORT` override) and, absent any hard error, the doctor exits 0

#### Scenario: A hard error takes precedence over advisories

- **WHEN** `make doctor` runs and finds both a hard error (e.g. the repo under
  `/mnt/`) and one or more advisories (e.g. a CRLF init script or an occupied
  `POSTGRES_HOST_PORT`)
- **THEN** all findings are printed, the doctor exits non-zero on account of the
  error (advisories never mask it), and `make dev-up` aborts before
  `docker compose up`

#### Scenario: Clean environment on macOS/Linux passes

- **WHEN** `make doctor` runs on macOS or Linux (not WSL) with all required tools
  present
- **THEN** the Windows/WSL-only checks are skipped and the doctor exits 0

#### Scenario: dev-up runs the doctor as a preflight

- **WHEN** a developer runs `make dev-up`
- **THEN** the doctor runs first; a hard error aborts `dev-up` before
  `docker compose up` with an actionable message, while advisory-only findings
  are printed and bring-up continues (and `DOCTOR_SKIP=1` bypasses the preflight)

## MODIFIED Requirements

### Requirement: Accurate Cross-Platform Setup Documentation

`DEV_SETUP.md` SHALL reference only `make` targets that exist, the `Makefile`
`help` text SHALL not advertise non-existent targets, the docs SHALL document the
canonical command path end to end, and SHALL include a WSL2 section for Windows
developers. They SHALL NOT reference `make apply-migrations` or `make drop-tables`.

Additionally, the documentation SHALL be accurate about the traps a Windows/WSL2
developer hits. Specifically it SHALL: surface `make doctor` as the first
onboarding step; instruct developers to install the toolchain (`uv`, `node` via
nvm, the pinned `supabase` CLI) **inside** the WSL Ubuntu distribution, warning
that a Windows-installed `node`/`npm` leaks into WSL via `/mnt/c`; explain why the
repository must be a Linux-ext4 clone under WSL2 (the MinIO `/data` bind-mount
I/O error is the primary reason, CRLF-mangled init scripts secondary); and the
MinIO Storage Setup section SHALL be accurate — buckets are created automatically
by the `minio-init` service and `MINIO_DATA_PATH` defaults to
`./volumes/minio-dev`. The docs SHALL NOT instruct developers to `mkdir ~/minio`
or to set `MINIO_DATA_PATH` to a `/Users/...` path.

#### Scenario: No phantom make targets in docs or help

- **WHEN** every `make <target>` referenced in `DEV_SETUP.md` and advertised in
  `make help` is checked against actual `Makefile` rule definitions
- **THEN** all referenced/advertised targets resolve to real rules (migrations are
  applied via `make migrate-local`; `apply-migrations`/`drop-tables` appear
  nowhere)

#### Scenario: Windows developers have a working documented path

- **WHEN** a Windows developer reads `DEV_SETUP.md`
- **THEN** it instructs them to use WSL2 with the repository cloned into the WSL2
  Linux filesystem (not `/mnt/c`), lists `make` as a prerequisite, includes the
  `supabase` CLI install step with the pinned `SUPABASE_VERSION`, documents the
  `POSTGRES_HOST_PORT` override, and from there follows the same path as
  macOS/Linux

#### Scenario: MinIO Storage Setup documentation is accurate

- **WHEN** a developer reads the MinIO section of `DEV_SETUP.md`
- **THEN** it states that buckets are created automatically by the `minio-init`
  service and that `MINIO_DATA_PATH` defaults to `./volumes/minio-dev`, and it
  does not instruct them to `mkdir ~/minio` or set `MINIO_DATA_PATH` to a
  `/Users/...` path

#### Scenario: Toolchain-in-WSL and doctor-first are documented

- **WHEN** a Windows/WSL2 developer follows `DEV_SETUP.md` from the top
- **THEN** the first step is `make doctor`, and the prerequisites instruct
  installing `uv`, `node` (via nvm), and the pinned `supabase` CLI inside WSL
  Ubuntu, explicitly warning that a Windows-installed `node`/`npm` shadows the
  WSL one via `/mnt/c`
