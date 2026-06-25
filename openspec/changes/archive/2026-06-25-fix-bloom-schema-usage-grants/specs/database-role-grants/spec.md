## ADDED Requirements

### Requirement: Schema-USAGE grants MUST live in a single source-of-truth file applied as the schema owner

The `bloom_*` schema-`USAGE` grants MUST be defined in one committed file,
`supabase/grants/schema_grants.sql`, and MUST be applied as `supabase_admin` (the
owner of schemas `storage`/`auth`, a superuser). This is required because
`supabase db push` applies migrations as `postgres` — which is neither a superuser
nor a member of `supabase_admin` — so a `GRANT USAGE ON SCHEMA … TO bloom_*` inside a
migration (or run as `postgres`) silently no-ops (`WARNING: no privileges were
granted`). The file MUST be idempotent and applied after the migrations that create
the `bloom_*` roles.

#### Scenario: Plain grant as postgres silently no-ops

- **WHEN** `GRANT USAGE ON SCHEMA storage TO bloom_agent` is executed as the
  `postgres` role (as `supabase db push` does)
- **THEN** Postgres emits `WARNING: no privileges were granted for "storage"`
- **AND** `has_schema_privilege('bloom_agent','storage','USAGE')` remains `false`

#### Scenario: Same grant as supabase_admin sticks

- **WHEN** the same grant is executed as `supabase_admin` (the schema owner)
- **THEN** `has_schema_privilege('bloom_agent','storage','USAGE')` becomes `true`

#### Scenario: schema_grants.sql grants the full set

- **WHEN** `supabase/grants/schema_grants.sql` is applied as `supabase_admin`
- **THEN** `bloom_user`, `bloom_admin`, `bloom_agent`, and `bloom_writer` hold
  `USAGE` on schema `storage`
- **AND** `bloom_writer` holds `USAGE` on schema `auth`

#### Scenario: Re-applying changes nothing

- **WHEN** `schema_grants.sql` is applied a second time
- **THEN** it completes without error and no role's schema privileges change

### Requirement: auth USAGE is granted to bloom_writer only

`schema_grants.sql` SHALL grant `auth` `USAGE` to `bloom_writer` only. It SHALL NOT
grant `auth` `USAGE` to `bloom_user`, `bloom_admin`, or `bloom_agent` — that is
#341's intentional read-only gap (their `auth.uid()` RLS paths are not evaluated as
those roles), and widening it requires its own review.

#### Scenario: user/admin/agent do not receive auth USAGE

- **WHEN** the grant set in `schema_grants.sql` is inspected (and after it is applied)
- **THEN** `bloom_user`, `bloom_admin`, and `bloom_agent` do **not** hold `auth`
  `USAGE`
- **AND** only `bloom_writer` appears for schema `auth`

### Requirement: The grants file MUST be applied after migrations in every DB-bringing-up path

Every path that creates the `bloom_*` roles via `supabase db push` MUST apply
`schema_grants.sql` as `supabase_admin` after `db push`: `make migrate-local`
(local, and therefore `make verify-dev` and the `dev-stack-smoke` CI job), and the
`pr-checks.yml` `compose-health-check` job. Ongoing prod/staging deployments apply it
manually when the grant set changes (the file is the reviewed source of truth).

#### Scenario: migrate-local applies the grants after db push

- **WHEN** `make migrate-local` runs
- **THEN** it applies `supabase/grants/schema_grants.sql` as `supabase_admin` after
  `supabase db push`
- **AND** `bloom_agent` holds `storage` `USAGE` afterwards

#### Scenario: CI compose-health-check applies the grants after migrations

- **WHEN** the `compose-health-check` job applies migrations via `supabase db push`
- **THEN** it then applies `schema_grants.sql` as `supabase_admin`
- **AND** the bloommcp persistence write path resolves `storage.objects` without
  `relation "objects" does not exist`

### Requirement: A CI guard MUST reject raw schema grants in migrations

A CI test MUST fail when any file under `supabase/migrations/` contains a `GRANT` or
`REVOKE … ON SCHEMA (auth|storage)` statement, because such statements silently no-op
under `supabase db push`. The two historical migrations that already contain such
statements (`20260428130000`, `20260519130000`) MUST be allowlisted and byte-pinned
(they are already applied everywhere and MUST NOT be edited — editing an applied
migration breaks `db push` history validation), directing new schema grants to
`schema_grants.sql`.

#### Scenario: A new migration with a raw schema grant fails CI

- **WHEN** a non-allowlisted migration contains `GRANT USAGE ON SCHEMA storage TO …`
  or `REVOKE … ON SCHEMA auth …`
- **THEN** the CI guard test fails, naming the offending file and statement

#### Scenario: Historical offenders are allowlisted, not edited

- **WHEN** the guard scans `20260428130000` and `20260519130000`
- **THEN** they pass via the documented allowlist
- **AND** the test asserts neither file's bytes changed (pinned sha256)

### Requirement: Health check MUST assert each bloom_* role's schema USAGE from the single source

`scripts/check_health.py` (run by `make check`) and the CI dev-stack-smoke MUST
assert that each `bloom_*` role holds its expected schema `USAGE`, with the expected
set **parsed from `supabase/grants/schema_grants.sql`** (the single source). A silent
grant no-op (file never applied, or applied as the wrong role) MUST fail
`make check` / CI loudly. The check MUST report a problem rather than crash when a
referenced role or schema is absent.

#### Scenario: Missing schema USAGE fails make check

- **WHEN** `make check` runs against a stack where a `bloom_*` role is missing an
  expected schema `USAGE` grant
- **THEN** `check_health.py` reports the missing role/schema pair
- **AND** the command exits non-zero

#### Scenario: Expected set is parsed from schema_grants.sql

- **WHEN** the health check determines which `(schema, role)` pairs to assert
- **THEN** it derives them by parsing `schema_grants.sql`, not a separate matrix file

#### Scenario: Absent role or schema is reported, not crashed

- **WHEN** `make check` runs against a partially-initialized stack where an expected
  role or schema does not yet exist
- **THEN** `check_health.py` reports it as a problem
- **AND** the check does not raise an unhandled exception

#### Scenario: Correctly granted stack passes make check

- **WHEN** `make check` runs against a stack where every `bloom_*` role holds its
  expected schema `USAGE`
- **THEN** the schema-USAGE assertion contributes no problems
- **AND** the command exits zero (absent other failures)
