## ADDED Requirements

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

### Requirement: Committed Local Environment Template

The repository SHALL contain a committed `.env.dev.example` template that
contains every variable required by the local stack, with an explanatory comment
per variable and no real secret values. `.gitignore` SHALL exclude `.env.dev` and
`.env.dev.backup` while keeping `.env.dev.example` tracked.

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

#### Scenario: One-shot reset-and-verify target

- **WHEN** a developer runs `make verify-dev`
- **THEN** it performs a clean reset, brings the stack up, applies migrations, and
  runs `make check`, failing loudly if any required service, role, schema, or
  migration is missing
