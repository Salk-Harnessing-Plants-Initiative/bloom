# development-environment Specification

## Purpose
TBD - created by archiving change fix-local-dev-setup. Update Purpose after archive.
## Requirements
### Requirement: Canonical Local Stack Path

The project SHALL document the `docker-compose.dev.yml` `db-dev` service as the
single canonical local Supabase stack, brought up via `make dev-up`. No parallel
`supabase start` / `supabase/config.toml` flow SHALL be introduced. Supported
platforms SHALL be macOS, Linux, and Windows via WSL2.

#### Scenario: Single documented bring-up command

- **WHEN** a developer on macOS, Linux, or WSL2 follows `DEV_SETUP.md` from a
  fresh clone
- **THEN** there is exactly one documented command path to start the local stack
  (`make dev-up`), with no references to an alternative `supabase start` flow

#### Scenario: No compose data-mount divergence

- **WHEN** this change is applied
- **THEN** `docker-compose.dev.yml` and `docker-compose.prod.yml` continue to
  bind-mount the Postgres data directory exactly as before (no switch to a named
  volume), so macOS, Linux, and CI behavior is unchanged

### Requirement: Fresh-Clone Stack Startup

`make dev-up` SHALL succeed on a fresh clone that has run `make init`, without the
developer having to hand-create any file that `make init` does not generate. Any
`env_file` a dev service references that is not committed and not produced by
`make init` (e.g. `web/.env`) SHALL be marked optional (`required: false`) so a
missing file does not abort `docker compose up` (issue #123). "Succeed" SHALL include the
`bloommcp` container's bind-mounted data directories (`TRAITS_DIR`, `PLOTS_DIR`,
`ANALYSIS_OUTPUT`) being writable by its runtime user immediately after bring-up — not just
that `docker compose up` itself exits 0 — per the "bloommcp Data Directory Writability"
requirement below.

#### Scenario: dev-up works without a web/.env

- **WHEN** a developer runs `make dev-up` on a fresh clone after `make init`, with
  no `web/.env` present
- **THEN** Compose does not error on the missing `web/.env` — the `bloom-web`
  `env_file` entry is marked `required: false`, and the variables it needs are
  supplied via the service's `environment`/`args` from the root `.env.dev`

### Requirement: LF Line Endings for Container Init Scripts

The repository SHALL enforce LF line endings, via `.gitattributes`, for the
Postgres/Supabase init scripts bind-mounted into Linux containers (under
`volumes/db/`, and shell/SQL scripts generally), so that a Windows checkout with
`core.autocrlf=true` does not produce CRLF scripts that fail with
`/bin/bash^M: bad interpreter` and leave the database partially initialized.

#### Scenario: Init scripts stay LF on a Windows checkout

- **WHEN** the repository is cloned on Windows with `core.autocrlf=true`
- **THEN** the files under `volumes/db/` (e.g. `_supabase.sh`, `roles.sql`) are
  checked out with LF line endings because `.gitattributes` marks them as such

#### Scenario: No CRLF bad-interpreter failure on clean init

- **WHEN** the local stack is brought up from a clean clone
- **THEN** the `db-dev` init scripts execute without a `bad interpreter` error and
  the Supabase roles and schemas are created (the failure mode described in issue
  #124 does not occur)

### Requirement: CI Dev-Stack Smoke Test

CI SHALL exercise the documented local dev workflow end-to-end on
`docker-compose.dev.yml` — generate credentials (`make init`), bring the stack up
(`make dev-up`), apply migrations (`make migrate-local`), and verify it
(`make check`) — so a regression in the dev path (env template, `web/.env`
optionality, dev port mapping, `migrate-local`, or the health check) fails CI
rather than a developer. Optional LLM services that need user-supplied keys MUST
NOT fail the smoke test.

#### Scenario: Dev workflow runs on every PR

- **WHEN** CI runs for a pull request
- **THEN** a job generates `.env.dev` via `make init`, brings up the dev stack via
  `make dev-up`, applies every migration via `make migrate-local`, and verifies
  the stack via `make check`, failing the PR if any step fails

#### Scenario: Optional LLM services don't fail the smoke test

- **WHEN** the smoke job runs without `OPENAI_API_KEY`/`LOCAL_LLM_URL`
- **THEN** `make check` still passes — `langchain-agent` being unhealthy is a
  warning, not a failure — because the core dev stack is healthy

### Requirement: Committed Local Environment Template

The repository SHALL contain a committed `.env.dev.example` template that
contains every variable required by the local stack, with an explanatory comment
per variable and no real secret values. `.gitignore` SHALL exclude `.env.dev` and
`.env.dev.backup` while keeping `.env.dev.example` tracked. An opt-in feature that
is disabled by default (e.g. a backend toggle) SHALL be represented as an
uncommented variable left empty, with a comment explaining what setting it does and
that it is off by default — not as a commented-out `VAR=value` line — so
`scripts/init_dev.py` copies it through to a freshly generated `.env.dev` in the
same (inert) state, ready to be filled in without un-commenting anything.

#### Scenario: Template is complete and secret-free

- **WHEN** `.env.dev.example` is inspected
- **THEN** it lists every variable `docker-compose.dev.yml` requires to start the
  stack (excluding variables compose supplies via `${VAR:-default}` defaults),
  every value is a placeholder (not a real secret), and each variable has a
  comment describing its purpose

#### Scenario: Real env files cannot be committed

- **WHEN** a developer runs `git status` after creating `.env.dev` and
  `.env.dev.backup`
- **THEN** `.env.dev` and `.env.dev.backup` are ignored by git and
  `.env.dev.example` is tracked

#### Scenario: Opt-in backend toggle documented empty, not commented out

- **WHEN** `.env.dev.example` documents `BLOOM_STORAGE_BACKEND`,
  `BLOOM_STORAGE_LOCAL_ROOT`, and `BLOOM_EXPERIMENT_LOCAL_ROOT`
- **THEN** each appears as an uncommented `VAR=` line with an empty value and an
  explanatory comment (mirroring the existing `LOCAL_LLM_URL`/`LOCAL_LLM_MODEL`
  "disabled — leave empty" convention), and a freshly generated `.env.dev` from
  `make init` carries them through still empty, requiring no un-commenting to
  discover or opt into

### Requirement: Cross-Platform Credential Generation

The project SHALL provide a runnable credential generator (`scripts/init_dev.py`,
exposed as `make init`) usable identically on macOS, Linux, and WSL2 via
`uv run --with pyjwt,python-dotenv`, which produces a working `.env.dev` from the
template with cryptographically secure local secrets. The generated `ANON_KEY`,
`SERVICE_ROLE_KEY`, and `BLOOM_AGENT_KEY` SHALL be JWTs signed with the generated
`JWT_SECRET` and carry the `anon`, `service_role`, and `bloom_agent` roles
respectively. Each generated encryption
key SHALL meet the size its consuming service requires (in particular
`DB_ENC_KEY` SHALL be exactly 16 bytes for Realtime AES-128; `VAULT_ENC_KEY` 32
bytes; `SECRET_KEY_BASE` at least 64; `SUPAVISOR_ENC_KEY` 64 hex characters;
`JWT_SECRET` at least 32). The generator SHALL be idempotent: it SHALL NOT
overwrite an existing `.env.dev` unless `--force` is given, and SHALL NOT print
secret values to stdout or logs.

#### Scenario: Generates a consistent, valid credential set

- **WHEN** a developer runs `make init` from a fresh clone with no `.env.dev`
- **THEN** `.env.dev` is created with secure random secrets of the required sizes,
  and `ANON_KEY`, `SERVICE_ROLE_KEY`, and `BLOOM_AGENT_KEY` verify against the
  generated `JWT_SECRET` with the expected `anon`/`service_role`/`bloom_agent`
  `role` claims, and no placeholder values remain

#### Scenario: Encryption keys meet service size constraints

- **WHEN** the generator produces `.env.dev`
- **THEN** `DB_ENC_KEY` is exactly 16 bytes, `VAULT_ENC_KEY` is 32 bytes,
  `SECRET_KEY_BASE` is at least 64, `SUPAVISOR_ENC_KEY` is 64 hex characters, and
  `JWT_SECRET` is at least 32 characters

#### Scenario: Refuses to clobber existing secrets

- **WHEN** a developer runs `make init` and `.env.dev` already exists
- **THEN** the generator refuses to overwrite it and instructs the developer to
  pass `--force` (which backs up the existing file first)

#### Scenario: Force backup does not destroy an existing backup

- **WHEN** a developer runs `make init --force` and both `.env.dev` and a previous
  `.env.dev.backup` already exist
- **THEN** the existing backup is not silently overwritten — the prior `.env.dev`
  is preserved to a timestamped backup (e.g. `.env.dev.backup.<timestamp>`) so no
  previously generated credentials are lost

### Requirement: Unified Host Port Configuration

The host-exposed Postgres port SHALL be configured by a single variable name,
`POSTGRES_HOST_PORT`, across `.env.dev`, `docker-compose.dev.yml`, the
integration test configuration, the `migrate-local` command, and CI. The default
SHALL be `5432`, and the documentation SHALL describe overriding it (e.g. to
`5433`) when host port `5432` is already taken.

#### Scenario: One port variable name everywhere

- **WHEN** the configuration is inspected after this change
- **THEN** `.env.dev` uses `POSTGRES_HOST_PORT` (not `POSTGRES_EXTERNAL_PORT`),
  matching `conftest.py`, `docker-compose.prod.yml`, and CI

#### Scenario: Port override is honored by every component including migrations

- **WHEN** a developer sets `POSTGRES_HOST_PORT=5433` because `5432` is shadowed
- **THEN** the stack publishes on `5433`, the integration tests connect on `5433`,
  and `make migrate-local` builds its `--db-url` with `5433` (it does not connect
  to the wrong Postgres on `5432`)

### Requirement: Local Migration Application

The project SHALL provide a single command (`make migrate-local`) to apply every
`supabase/migrations/*.sql` to the running local stack. The command SHALL build
its connection URL from `POSTGRES_HOST_PORT`, the local `POSTGRES_PASSWORD`,
user, and database (so it works with generated credentials and a non-default
port), and SHALL pass `--debug` so `sslmode=disable` is honored on the TLS-less
local Postgres, mirroring CI.

#### Scenario: All migrations apply cleanly with local credentials

- **WHEN** a developer runs `make migrate-local` against a healthy `db-dev` whose
  credentials were produced by `make init`
- **THEN** the command authenticates with the generated password on the
  configured port, all migrations apply without error, and they are recorded in
  `supabase_migrations.schema_migrations`

#### Scenario: sslmode workaround is in place

- **WHEN** the local migration command is invoked
- **THEN** it passes `--debug` to `supabase db push` so `sslmode=disable` is not
  silently ignored (supabase-cli #4839)

#### Scenario: Waits for the storage schema before pushing

- **WHEN** `make migrate-local` is run right after `make dev-up`, before
  `storage-api` has provisioned `storage.buckets`
- **THEN** it bounded-waits for the `storage.buckets` table (incl. the columns
  bucket migrations insert into) to exist before `supabase db push`, so migrations
  that touch `storage.buckets` do not race storage-api and fail (SQLSTATE 42703)

### Requirement: Local Integration Test Execution

The integration test configuration SHALL load `.env.dev` when present, after the
existing `.env.prod`/`.env.ci` sources so those continue to take precedence in CI
and prod, so that DB-backed tests connect to the local stack instead of silently
skipping. A single documented command (`make test-integration`) SHALL run the
integration suite locally against the running stack.

#### Scenario: DB-backed tests run locally instead of skipping

- **WHEN** the local stack is up, `.env.dev` exists, and `make test-integration`
  is run
- **THEN** `conftest.py` sources the Postgres credentials and host port from
  `.env.dev`, and the `pg_conn`-based tests execute against `db-dev` rather than
  being skipped for a missing password

#### Scenario: CI and prod precedence preserved

- **WHEN** both `.env.ci` (or `.env.prod`) and `.env.dev` are present
- **THEN** the values from `.env.ci`/`.env.prod` take precedence over `.env.dev`,
  so adding the `.env.dev` source does not change CI or prod behavior

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

### Requirement: Local Stack Health Verification

The project SHALL provide a health check (`scripts/check_health.py`, exposed as
`make check`) and a one-shot `make verify-dev` (clean reset → up → migrate →
check) that assert the local stack is correct: every service that defines a
Compose healthcheck reports `healthy` (and none has exited non-zero), required
roles present, `auth` and `storage` schemas present, and **all** migrations
applied. Migration completeness SHALL be checked by set comparison — every
`supabase/migrations/*.sql` file is recorded in
`supabase_migrations.schema_migrations`, with no missing and no unexpected
entries — reusing the existing `tests/integration/test_migrations.py` approach
rather than a brittle count. An automated test
(`tests/integration/test_local_dev_bootstrap.py`) SHALL encode the
database-substrate assertions and SHALL be safe to run in CI.

#### Scenario: Health check asserts roles, schemas, and migration completeness

- **WHEN** `make check` runs against a freshly initialized and migrated local
  stack
- **THEN** it asserts the base roles `postgres`, `anon`, `authenticated`,
  `service_role`, `authenticator`, `supabase_admin`, `supabase_auth_admin`, and
  `supabase_storage_admin` exist; that the application roles `bloom_admin`,
  `bloom_user`, `bloom_writer`, and `bloom_agent` (created by migrations) exist;
  that the `auth` schema and `auth.uid()` exist; that the `storage` schema and
  `storage.buckets` exist; and that every `supabase/migrations/*.sql` file is
  recorded in `supabase_migrations.schema_migrations` (set comparison — no missing
  and no unexpected entries)

#### Scenario: Partial migration run is reported as failure

- **WHEN** only some migrations applied (the tracking table is non-empty but
  migrations remain pending)
- **THEN** the health check fails rather than reporting success on a merely
  non-empty tracking table

#### Scenario: Tolerates a still-settling stack

- **WHEN** `make check` is run right after `make dev-up`, while required services
  (e.g. `bloommcp`, `realtime`) are still in `starting` because their healthchecks
  have not run yet
- **THEN** the check bounded-waits for those services to leave `starting` before
  judging, rather than failing immediately — a required service that is still
  `starting`/`unhealthy` after the wait is a failure; an optional service stays a
  warning

#### Scenario: One-shot reset-and-verify target

- **WHEN** a developer runs `make verify-dev`
- **THEN** it performs a clean reset, brings the stack up, applies migrations, and
  runs `make check`, failing loudly if any required service, role, schema, or
  migration is missing

### Requirement: Preflight Environment Doctor

The project SHALL provide a preflight diagnostic, `make doctor` (backed by
`scripts/doctor.sh`), that inspects the developer's environment **before** stack
bring-up and reports actionable findings, and `make dev-up` SHALL run it first as
a preflight. The doctor SHALL be dependency-light — implemented in POSIX `sh` and
MUST NOT require `uv`, Python, or Node, so it can itself report those missing.

Findings SHALL be classified by severity. The following SHALL be **errors** that
cause a non-zero exit (and thus abort `make dev-up`): the repository residing on
a Windows-mounted path (under `/mnt/`) when running under WSL, any of the
required tools `uv`, `node`, `npm`, `supabase`, `make`, `docker` being absent
from `PATH`, and `docker` being installed but its daemon unreachable (`docker
info` fails) — since `make dev-up`'s next step is `docker compose up`. The
following SHALL be **advisories** that are printed but do not
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

#### Scenario: Docker installed but its daemon not running is a hard error

- **WHEN** `make doctor` runs with `docker` on `PATH` but its daemon unreachable
  (`docker info` fails)
- **THEN** it exits non-zero with a message to start Docker, rather than letting
  `make dev-up`'s subsequent `docker compose up` fail cryptically

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

### Requirement: bloommcp Data Directory Writability

The three host directories `bloommcp` bind-mounts SHALL exist and be writable by the
`bloommcp` container's runtime user **before** `docker compose up` runs, on every fresh
clone — specifically `bloommcp/data/TRAITS_DIR`, `bloommcp/data/PLOTS_DIR`, and
`bloommcp/data/ANALYSIS_OUTPUT`. This SHALL NOT rely on Docker's default behavior for a missing bind-mount source
(creating it owned by the Docker daemon's user, typically root) — that default leaves the
non-root `bloommcp` container user unable to write into them, which silently breaks every
tool that writes to local disk (the 5 `sleap_roots` plotting tools always do, regardless of
`BLOOM_STORAGE_BACKEND`; the QC/analysis tools do only in fully-local storage-backend mode).

#### Scenario: Fresh clone provisions writable data directories

- **WHEN** `make dev-up` runs on a fresh clone where `bloommcp/data/` does not yet exist on
  the host
- **THEN** `bloommcp/data/{TRAITS_DIR,PLOTS_DIR,ANALYSIS_OUTPUT}` exist and are writable
  by the `bloommcp` container's runtime user before `docker compose up` starts the container
- **AND** no plotting or fully-local-backend analysis tool call fails with a permission error
  as a result of directory ownership

#### Scenario: A plotting tool succeeds end-to-end against the dev stack

- **WHEN** the dev stack is up and a plotting tool (e.g. `plot_trait_histograms`) is called
  through the MCP interface
- **THEN** it renders and saves its PNG to `PLOTS_DIR` without a permission error and returns
  the expected "Plot saved: `<url>`" summary

#### Scenario: A regression is caught by CI, not a developer

- **WHEN** the directory-provisioning step is skipped or broken
- **THEN** the CI check added by this change (task 2, location per design.md) fails, rather
  than the failure only surfacing when a developer or agent calls a plotting tool for the
  first time

### Requirement: Externalized Local-Only Storage Backend Vars

The `bloommcp` service in `docker-compose.dev.yml` SHALL source
`BLOOM_STORAGE_BACKEND`, `BLOOM_STORAGE_LOCAL_ROOT`, and
`BLOOM_EXPERIMENT_LOCAL_ROOT` via `${VAR:-}` interpolation from the active env
file — not as literal, commented-out YAML — so enabling bloommcp's fully-local
(offline) mode never requires editing a tracked file.

#### Scenario: Toggling local mode requires no tracked-file edit

- **WHEN** a developer wants to run bloommcp in fully-local (offline) mode in dev
- **THEN** they set `BLOOM_STORAGE_BACKEND=local` (and optionally
  `BLOOM_STORAGE_LOCAL_ROOT` / `BLOOM_EXPERIMENT_LOCAL_ROOT`) in their own
  `.env.dev` — this does not require editing `docker-compose.dev.yml`

#### Scenario: Unset stays inert, matching today's default

- **WHEN** `BLOOM_STORAGE_BACKEND` is unset in both `.env.dev` and the shell
  environment
- **THEN** `${BLOOM_STORAGE_BACKEND:-}` resolves to an empty string, the
  `bloommcp` container sees no meaningful value for the var, and the server boots
  in the default Supabase-backed mode exactly as it does today with the line
  commented out

#### Scenario: Pre-set backend is announced at `dev-up` invocation time

- **WHEN** a developer runs plain `make dev-up` with `BLOOM_STORAGE_BACKEND`
  resolving non-empty from either the shell environment or `.env.dev`
- **THEN** a foreground NOTE is printed before the doctor preflight/build steps,
  naming the resolved value and pointing at `make dev-up-local` or unsetting the
  var to restore the default — so externalizing the toggle (making it newly
  overridable by a stray shell export or a forgotten `.env.dev` value) doesn't
  silently redirect a plain `dev-up` with the only cue buried in detached
  container logs

### Requirement: Discoverable `make dev-up-local` Entrypoint

The project SHALL provide a `make dev-up-local` target, listed in `make help`,
that starts the dev stack with `BLOOM_STORAGE_BACKEND=local` for that invocation
without persisting the change to `.env.dev`, by delegating to the existing
`dev-up` target rather than duplicating its recipe.

#### Scenario: `make dev-up-local` is discoverable and scoped to one invocation

- **WHEN** a developer runs `make help`
- **THEN** `dev-up-local` is listed alongside `dev-up`/`prod-up`
- **WHEN** a developer runs `make dev-up-local`
- **THEN** the `bloommcp` container boots with `BLOOM_STORAGE_BACKEND=local` in
  its environment, and the developer's `.env.dev` file on disk is unchanged

#### Scenario: Plain `dev-up` is unaffected

- **WHEN** a developer runs plain `make dev-up` (not `dev-up-local`)
- **THEN** `BLOOM_STORAGE_BACKEND` is empty/absent in the `bloommcp` container,
  regardless of any prior `make dev-up-local` invocation on the same machine

