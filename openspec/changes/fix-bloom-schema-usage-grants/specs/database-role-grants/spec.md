## ADDED Requirements

### Requirement: Schema-USAGE grants MUST be applied via a SECURITY DEFINER helper owned by the schema owner

The `bloom_*` schema-`USAGE` grants MUST be applied by calling a `SECURITY
DEFINER` function owned by `supabase_admin`, so the grant runs with the owner's
privileges regardless of which role `db push` downgrades to. This is required
because `supabase db push` applies migrations as `postgres` — which is neither a
superuser nor a member of `supabase_admin` (the owner of schemas `storage`/`auth`)
— so a plain `GRANT USAGE ON SCHEMA … TO bloom_*` inside a migration silently
no-ops (`WARNING: no privileges were granted`).

The function (`public.bloom_grant_schema_usage(schema, role)`) MUST be owned by
`supabase_admin` and MUST be installed by a superuser; it cannot be created by a
`db push`-applied migration, because `postgres` cannot `CREATE`/`ALTER FUNCTION …
OWNER TO supabase_admin`. The install MUST verify the function is owned by
`supabase_admin` after creation and fail if it is not (a wrong-owner helper would
`CREATE OR REPLACE` successfully yet still no-op grants).

#### Scenario: Plain grant as postgres silently no-ops

- **WHEN** `GRANT USAGE ON SCHEMA storage TO bloom_agent` is executed as the
  `postgres` role (as `supabase db push` does)
- **THEN** Postgres emits `WARNING: no privileges were granted for "storage"`
- **AND** `has_schema_privilege('bloom_agent','storage','USAGE')` remains `false`

#### Scenario: Helper call as postgres makes the grant stick

- **WHEN** the helper, installed and owned by `supabase_admin`, is called as the
  `postgres` role to grant `bloom_agent` `USAGE` on `storage`
- **THEN** the grant takes effect with the owner's privileges
- **AND** `has_schema_privilege('bloom_agent','storage','USAGE')` becomes `true`

#### Scenario: Helper cannot be created by a db push migration

- **WHEN** a migration applied as `postgres` attempts to create the helper and
  transfer ownership to `supabase_admin`
- **THEN** the statement fails (ownership cannot be transferred to a role the
  caller is not a member of)
- **AND** the helper MUST instead be installed by a superuser (the init layer)

#### Scenario: Install detects a wrong-owner helper

- **WHEN** the helper install runs and the resulting function is not owned by
  `supabase_admin`
- **THEN** the install fails loudly

### Requirement: The helper MUST be a locked-down grant primitive

The install MUST `REVOKE EXECUTE` on the helper `FROM PUBLIC` (and from `anon` and
`authenticated`) and grant `EXECUTE` only to `postgres`, and the helper body MUST
whitelist its arguments — `schema` restricted to `{storage, auth}` and `role`
restricted to `bloom_*` — and `RAISE EXCEPTION` on anything else. This is required
because the helper executes `GRANT USAGE` with `supabase_admin`'s superuser
authority and lives in a PostgREST-exposed schema (`public`), so an unlocked
function is reachable as a `POST /rpc/…` privilege-escalation primitive; the
whitelist bounds the blast radius to the documented grant matrix even if `EXECUTE`
is ever obtained.

#### Scenario: Anon and bloom_* roles cannot execute the helper

- **WHEN** `anon`, `authenticated`, or any `bloom_*` role attempts to execute
  `public.bloom_grant_schema_usage`
- **THEN** execution is denied (no `EXECUTE` privilege)

#### Scenario: Helper rejects out-of-whitelist arguments

- **WHEN** the helper is called with a schema outside `{storage, auth}` or a role
  not matching `bloom_*` (e.g. `('vault','bloom_user')` or `('auth','postgres')`)
- **THEN** the helper raises an exception and grants nothing

### Requirement: The helper MUST be installed by the privileged init layer on fresh init

The helper MUST be installed by the database container's privileged init layer
(`docker-entrypoint-initdb.d`), which runs as the superuser at fresh cluster init,
in both `docker-compose.dev.yml` and `docker-compose.prod.yml`. This makes the
helper present automatically on every fresh init (local reset, CI, disaster
recovery) without any pre-`db push` step in the Makefile, `deploy.yml`, or
`pr-checks.yml`. The init artifact MUST be the same committed
`supabase/grants/install_bloom_grant_helper.sql` used for the manual apply, so the
helper definition has a single source.

#### Scenario: Fresh cluster init installs the helper as superuser

- **WHEN** the database container initializes a fresh data directory
- **THEN** the init layer runs `install_bloom_grant_helper.sql` as the superuser
- **AND** the helper exists, is `SECURITY DEFINER`, and is owned by `supabase_admin`

#### Scenario: Fresh-init migration apply makes grants stick without extra wiring

- **WHEN** `supabase db push` runs the helper-calling migration on a freshly
  initialized stack (local reset or CI)
- **THEN** the helper (installed at init) executes the grants with the owner's
  authority
- **AND** `bloom_agent` holds `storage` `USAGE` with no pre-`db push` install step

#### Scenario: bloommcp persistence write path resolves storage.objects

- **WHEN** a bloommcp workflow writes a result after a fresh `make verify-dev`
- **THEN** the write path resolves `storage.objects` successfully
- **AND** it does not fail with `relation "objects" does not exist`

### Requirement: Existing persistent volumes MUST be covered by a checked-in one-time manual apply

The helper MUST be installed once as `supabase_admin` on existing persistent volumes (prod, staging, and any pre-existing local volume), using the same committed `install_bloom_grant_helper.sql`, and the procedure MUST be documented by a checked-in runbook (`supabase/grants/README.md`) that is the source of truth for the manual step (replacing ad-hoc setup notes). This is required because `docker-entrypoint-initdb.d` runs only on a fresh data directory, so these volumes will not re-init and will not auto-install the helper. The procedure MUST be idempotent.

#### Scenario: One-time manual apply documented and idempotent

- **WHEN** an operator follows `supabase/grants/README.md` on an existing
  persistent volume and runs `install_bloom_grant_helper.sql` as `supabase_admin`
- **THEN** the helper is installed (owned by `supabase_admin`, `SECURITY DEFINER`)
- **AND** running it a second time changes nothing

### Requirement: The grant set MUST be the single source of truth in migration history

A migration MUST apply the complete `bloom_*` schema-`USAGE` set by calling the
helper: `USAGE` on schema `storage` to `bloom_user`, `bloom_admin`, `bloom_agent`,
and `bloom_writer`; and `USAGE` on schema `auth` to `bloom_writer`. The migration
MUST be idempotent and safe to re-apply, and MUST be timestamped after the
migrations that create those roles. The grant set MUST derive from a single
committed grant matrix that the health check also consumes, and a test MUST assert
the migration's grant set equals that matrix so the two cannot drift. The migration
MUST call the helper (e.g. `SELECT bloom_grant_schema_usage(…)`) rather than issue a
raw `GRANT … ON SCHEMA`. If the helper is absent when the migration runs, the
migration MUST fail loudly rather than silently no-op.

#### Scenario: All four roles receive storage USAGE via the helper

- **WHEN** the helper-calling migration is applied (helper installed)
- **THEN** each of `bloom_user`, `bloom_admin`, `bloom_agent`, `bloom_writer`
  holds `USAGE` on schema `storage`

#### Scenario: bloom_writer receives auth USAGE via the helper

- **WHEN** the helper-calling migration is applied (helper installed)
- **THEN** `bloom_writer` holds `USAGE` on schema `auth`

#### Scenario: auth USAGE is not granted to bloom_user/admin/agent

- **WHEN** the helper-calling migration is applied
- **THEN** `bloom_user`, `bloom_admin`, and `bloom_agent` do **not** receive `auth`
  `USAGE` (the intentional #341 read-only gap)
- **AND** the health-check matrix does not list those pairs

#### Scenario: Re-applying the migration changes no privileges

- **WHEN** the helper-calling migration is applied a second time
- **THEN** it completes without error and no role's schema privileges change

#### Scenario: Migration matrix matches the health-check matrix

- **WHEN** the anti-drift test compares the helper-calling migration's grant set
  to the committed grant matrix
- **THEN** they are equal, and the test fails if a role/schema pair is added to one
  but not the other

### Requirement: A CI guard MUST reject raw schema grants in migrations

A CI test MUST fail when any file under `supabase/migrations/` contains a
`GRANT` or `REVOKE … ON SCHEMA (auth|storage)` statement, because such statements
silently no-op under `supabase db push` (applied as `postgres`). The two historical
migrations that already contain such statements (`20260428130000`,
`20260519130000`) MUST be allowlisted (they are already applied everywhere and MUST
NOT be edited, since editing an applied migration breaks `db push` history
validation), and the allowlist MUST reference the helper-calling migration as the
authoritative path.

#### Scenario: A new migration with a raw schema grant fails CI

- **WHEN** a migration under `supabase/migrations/` (not allowlisted) contains
  `GRANT USAGE ON SCHEMA storage TO …` or `REVOKE … ON SCHEMA auth …`
- **THEN** the CI guard test fails, naming the offending file and statement

#### Scenario: The helper-calling migration is not flagged

- **WHEN** the guard scans the helper-calling migration, which uses
  `SELECT bloom_grant_schema_usage(…)` rather than `GRANT … ON SCHEMA`
- **THEN** the guard does not flag it

#### Scenario: Historical offenders are allowlisted, not edited

- **WHEN** the guard scans `20260428130000` and `20260519130000`
- **THEN** they pass via the documented allowlist
- **AND** the test asserts neither file's bytes changed (they are not edited)

### Requirement: Health check MUST assert each bloom_* role's schema USAGE and the helper's integrity

`scripts/check_health.py` (run by `make check`) and the CI dev-stack-smoke MUST
assert that each `bloom_*` role holds its expected schema `USAGE`, driven by the
single committed grant matrix, and MUST assert the helper exists, is
`SECURITY DEFINER`, and is owned by `supabase_admin`. A silent grant no-op, a
missing/mis-owned helper, or a tampered helper MUST fail `make check` / CI loudly
instead of surfacing later as `relation "objects" does not exist`. The check MUST
report a problem rather than crash when a referenced role or schema is absent.

#### Scenario: Missing schema USAGE fails make check

- **WHEN** `make check` runs against a stack where a `bloom_*` role is missing an
  expected schema `USAGE` grant
- **THEN** `check_health.py` reports the missing role/schema pair
- **AND** the command exits non-zero

#### Scenario: Missing or mis-owned helper fails make check

- **WHEN** `make check` runs against a stack where
  `public.bloom_grant_schema_usage` is absent, not `SECURITY DEFINER`, or not owned
  by `supabase_admin`
- **THEN** `check_health.py` reports the helper problem
- **AND** the command exits non-zero

#### Scenario: Absent role or schema is reported, not crashed

- **WHEN** `make check` runs against a partially-initialized stack where an expected
  role or schema does not yet exist
- **THEN** `check_health.py` reports it as a problem
- **AND** the check does not raise an unhandled exception

#### Scenario: Correctly granted stack passes make check

- **WHEN** `make check` runs against a stack where every `bloom_*` role holds its
  expected schema `USAGE` and the helper is present, `SECURITY DEFINER`, and
  correctly owned
- **THEN** the schema-USAGE assertion contributes no problems
- **AND** the command exits zero (absent other failures)
