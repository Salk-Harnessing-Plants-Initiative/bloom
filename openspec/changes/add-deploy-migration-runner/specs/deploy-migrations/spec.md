# deploy-migrations Specification

## Purpose

Apply Supabase database migrations during CI, production deploy, and staging deploy using the official Supabase CLI. Close the gap where the current deploy workflow has no migration step (services would start against an unmigrated schema) and the current CI uses an inline hand-rolled loop that is not safe to re-run on persistent prod/staging databases.

Rollback automation, rollback-file pairing, partial-apply state contract, RLS partial-apply mitigation, and per-deploy `pg_dump` snapshots are intentionally out of scope for this change and deferred to a follow-up proposal (`improve-deploy-migration-rollback`). A one-time `pg_dump` before Monday's first deploy is documented as an optional pre-deploy recommendation in [#135](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/135), not a spec requirement.

## ADDED Requirements

### Requirement: Supabase CLI MUST be pinned to the same version on the server and CI runner

The Salk deploy server and the GitHub Actions runner used by `pr-checks.yml` MUST have the Supabase CLI installed at the same pinned version (v2.90.0). Server install and version verification are documented as manual pre-deploy steps in [#135](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/135) (the server is single-tenant and operator-controlled). CI installs the CLI automatically at job start.

#### Scenario: Salk server has the CLI installed at the pinned version

Given the Salk deploy server has had its pre-deploy setup completed per #135
When an operator runs `supabase --version` on the server
Then the output MUST match the pinned version (v2.90.0)

#### Scenario: CI runner installs the CLI at the same pinned version non-interactively

Given a PR triggers the `compose-health-check` workflow
When the workflow runs its "Install Supabase CLI" step
Then the step MUST install the Supabase CLI via the pinned `.deb` release using `sudo apt install -y` (no TTY available on runners — `-y` is mandatory)
And `supabase --version | grep -q "${SUPABASE_VERSION}"` MUST pass in the same step

### Requirement: Postgres MUST be reachable from the host on a loopback-only port

To let the host-installed CLI reach Postgres without introducing a public port, `db-prod` MUST publish port 5432 on the host's loopback interface only (`127.0.0.1`). The host port MUST be parameterized via an env var (`POSTGRES_HOST_PORT`) so production and staging can coexist on the same host with different host ports.

#### Scenario: db-prod is bound to loopback only on the configured host port

Given `docker-compose.prod.yml` maps `127.0.0.1:${POSTGRES_HOST_PORT:-5432}:5432` on `db-prod`
And the deployed environment's `.env` file sets `POSTGRES_HOST_PORT`
When an operator runs `ss -tlnp | grep 5432` on the server
Then the listening socket MUST be bound to `127.0.0.1:${POSTGRES_HOST_PORT}` only
And no socket MUST be bound to `0.0.0.0:5432` or any other public interface

#### Scenario: Prod and staging use different host ports

Given the deploy workflow writes `POSTGRES_HOST_PORT=5432` into `.env.prod` via heredoc
And writes `POSTGRES_HOST_PORT=5433` into `.env.staging` via heredoc
When prod and staging stacks are both running on the Salk server
Then prod Postgres MUST be reachable at `127.0.0.1:5432` only
And staging Postgres MUST be reachable at `127.0.0.1:5433` only
And neither stack MUST fail to start due to port collision

### Requirement: Deploy MUST apply migrations via `supabase db push` after the stack is healthy and after storage-schema is confirmed ready

Each deploy job in `deploy.yml` (production and staging) MUST include a step that runs `supabase db push --db-url <loopback URL> --yes` via SSH. The step MUST be positioned after the health check step (from PR #142), after an explicit `storage.buckets` schema poll, and before the rollback-on-failure step. The schema poll is required because PR #142's storage-api healthcheck verifies only that the Fastify HTTP server bound its port, not that storage-api has finished its async schema initialization.

#### Scenario: Storage-schema poll runs before migrations and catches async init race

Given `docker compose up --wait` has returned (all container healthchecks pass)
And the `Wait for storage schema` step begins
When the step polls `information_schema.tables` for `storage` schema + `buckets` table
Then the step MUST poll every 2 seconds for up to 60 iterations (120s total)
And MUST exit 0 as soon as the table exists
And MUST exit non-zero with a `::error::` annotation if the timeout expires

#### Scenario: Production deploy applies pending migrations

Given `main` has been updated with new migrations in `supabase/migrations/`
And the production stack is healthy
And `storage.buckets` exists
When the "Apply database migrations (production)" step runs over SSH
Then the step MUST execute `supabase db push --db-url postgresql://...@127.0.0.1:${POSTGRES_HOST_PORT}/... --yes`
And `supabase_migrations.schema_migrations` MUST contain one row per newly-applied migration after the step completes
And the step MUST exit non-zero if any migration fails

#### Scenario: Staging deploy applies pending migrations independently

Given the `deploy-staging` job runs with `STAGING_DEPLOY_PATH` and `.env.staging`
When the "Apply database migrations (staging)" step runs over SSH
Then the step MUST use the staging env file and `POSTGRES_HOST_PORT=5433`
And the command MUST be structurally identical to prod except for env file and host port
And a failure in staging MUST NOT affect a concurrent or subsequent production deploy

#### Scenario: Already-applied migrations are skipped on re-deploy

Given a previous deploy successfully applied migrations m1, m2
And a new deploy runs with no new migrations added
When the migration step executes
Then `supabase db push` MUST detect that all migrations are already recorded
And MUST exit 0 with no migration actions taken
And `supabase_migrations.schema_migrations` row count MUST be unchanged

#### Scenario: Migration step extracts only Postgres connection env vars

Given `.env.prod` contains JWT_SECRET, SERVICE_ROLE_KEY, DB_ENC_KEY and many other secrets
When the migration step constructs the db-url
Then the step MUST extract POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, POSTGRES_HOST_PORT via explicit `grep -E '^VAR=' | cut -d= -f2-`
And MUST NOT use `set -a; source .env.prod; set +a` which would export every variable to the subprocess
And no non-Postgres secret MUST appear in the subprocess environment

### Requirement: Tracking table MUST be `supabase_migrations.schema_migrations`

All applied migrations MUST be recorded in `supabase_migrations.schema_migrations` — the canonical Supabase tracking table — created automatically by the CLI on first run. The ad-hoc `public._migrations` table used by local dev Makefile workflows MUST NOT be used for CI or production deploys.

#### Scenario: First run against a fresh database creates the canonical table

Given the target database has no `supabase_migrations` schema
When `supabase db push` runs for the first time
Then the CLI MUST create the `supabase_migrations` schema
And MUST create the `supabase_migrations.schema_migrations` table
And MUST record one row per applied migration

#### Scenario: CLI ignores legacy `public._migrations` if present

Given a target database has a `public._migrations` table from prior local-dev work
When `supabase db push` runs
Then the CLI MUST use `supabase_migrations.schema_migrations` as the sole source of truth
And MUST NOT read migration state from `public._migrations`

### Requirement: CI MUST apply migrations using the same CLI as deploy

The `compose-health-check` job in `pr-checks.yml` MUST use `supabase db push` to apply migrations, not the previous inline `for migration in *.sql; do psql < $migration; done` loop. This ensures CI exercises the exact migration pathway that deploy uses.

#### Scenario: CI applies migrations via the CLI against a fresh stack

Given a PR triggers `compose-health-check`
When the CI stack reaches the "Apply database migrations" step
Then the step MUST invoke `supabase db push --db-url postgresql://...@127.0.0.1:${POSTGRES_HOST_PORT}/... --yes`
And MUST NOT contain any inline loop over `supabase/migrations/*.sql`

#### Scenario: CI fails fast on a broken migration

Given a PR adds a migration with invalid SQL
When the CI migration step runs
Then `supabase db push` MUST exit non-zero
And the workflow job MUST fail
And the PR MUST be blocked from merging

### Requirement: CI MUST lint newly-added migration filenames and reject stale timestamps via an extracted, testable script

The `pr-checks.yml` workflow MUST fail any PR where a newly-added file in `supabase/migrations/` does not match the `YYYYMMDDHHMMSS_lowercase_name.sql` pattern, OR where the new migration's timestamp is less than or equal to the latest migration already on `main`. Historical migrations on `main` are grandfathered — the lint evaluates only files added in the PR's diff vs. `main`. The lint logic MUST live in `scripts/lint_migrations.sh` so it can be unit-tested independently of the workflow.

#### Scenario: CI rejects a malformed new migration filename

Given a PR adds `supabase/migrations/20260418_add_cyl_traits.sql` (missing HHMMSS segment)
And that file is not present on `main`
When the `scripts/lint_migrations.sh` runs
Then the lint MUST fail with a `::error title=Invalid migration filename::` annotation naming the offending file
And the PR MUST be blocked from merging

#### Scenario: CI rejects a new migration with a stale timestamp

Given `main` already has `supabase/migrations/20260410120000_existing.sql`
And a PR adds `supabase/migrations/20260408000000_stale_backdated.sql`
When the sequential-timestamp lint runs
Then the lint MUST fail with a `::error title=Stale migration timestamp::` annotation showing the stale file's timestamp and the latest timestamp on main
And the PR MUST be blocked from merging

#### Scenario: Historical migrations with irregular filenames are grandfathered

Given `main` already contains migrations with filenames that include hyphens or dots (e.g. `20250617163449_insert_image_2.0_rpc.sql`)
When a PR that does not touch those historical migrations runs CI
Then the lint MUST pass
And the lint MUST only flag files added in the PR's diff vs. `main`

#### Scenario: `scripts/lint_migrations.sh` is testable independently of the workflow

Given a pytest fixture creates a scratch git repo with a synthetic migration layout
When the test invokes `scripts/lint_migrations.sh` with a given `BASE_REF`
Then the script MUST exit 0 for valid inputs and non-zero for each failure class
And tests MUST cover: valid filename + valid timestamp, malformed filename, stale timestamp, grandfathered historical filename

### Requirement: Migration failures MUST be highly visible on GitHub Actions

Default step failure (red X with CLI error in step logs) is insufficient. Each deploy and CI run MUST produce three layers of visibility: a top-of-run error banner on failure, an actionable `migration list` diagnostic step, and a markdown summary panel on every run. Layer C MUST use the CLI's native markdown output — the `-o json` flag does NOT exist on the `migration list` subcommand in Supabase CLI v2.90.0.

#### Scenario: Migration failure emits a workflow error annotation

Given `supabase db push` exits non-zero
When the "Apply database migrations" step handles the failure
Then the step MUST emit a `::error title=Migration failed::` annotation
And the step MUST exit 1 so the workflow is marked as failed

#### Scenario: Diagnostic step runs on any migration failure

Given the "Apply database migrations" step has failed
When the workflow evaluates subsequent steps
Then a step with `if: failure()` MUST run `supabase migration list --db-url <url>` against the target database
And the output MUST show which migrations are recorded in `supabase_migrations.schema_migrations` and which files in `supabase/migrations/` are pending

#### Scenario: Migration state MUST appear in workflow summary via raw markdown

Given the workflow has attempted migrations (success or failure)
When the job finishes
Then a step with `if: always()` MUST write markdown to `$GITHUB_STEP_SUMMARY`
And the markdown MUST contain the raw output of `supabase migration list --db-url <url>` (a markdown table with `Local | Remote | Time (UTC)` columns emitted natively by the CLI)
And the implementation MUST NOT rely on `-o json` — that flag does not exist on the `migration list` subcommand in Supabase CLI v2.90.0

### Requirement: Migration credentials MUST NOT appear in process argv, logs, or step summaries

The Postgres password used by `supabase db push` and `supabase migration list` MUST be passed via the `PGPASSWORD` environment variable, never embedded in the `--db-url` command-line argument. The Layer C "Migration summary" step MUST NOT use `--debug` — its output is written to `$GITHUB_STEP_SUMMARY`, which is rendered on the GitHub run page and readable by every repo collaborator. Any CLI output captured into `$GITHUB_STEP_SUMMARY` MUST NOT contain the connection URL. `::add-mask::` is NOT a sufficient mitigation because it does not scrub `$GITHUB_STEP_SUMMARY` content and does not cover URL-encoded variants of the password.

#### Scenario: Password is passed via PGPASSWORD, not in --db-url

Given a migration step is about to invoke `supabase db push`
When the step constructs the command
Then the command MUST set `PGPASSWORD` as an env var for the CLI process
And the `--db-url` value MUST have the form `postgresql://<user>@<host>:<port>/<db>?sslmode=disable` (no password segment)
And the password MUST NOT appear anywhere in the process's argv or in shell-expanded command strings visible to `ps`

#### Scenario: Migration summary step does not use --debug

Given the Layer C "Migration summary" step runs with `if: always()`
When the step captures the output of `supabase migration list`
Then the `--debug` flag MUST NOT be present in the command
And the captured output MUST contain only the migration-state table (columns: Local, Remote, Time (UTC))
And the captured output MUST NOT contain the connection URL or any diagnostic line that echoes credentials

#### Scenario: $GITHUB_STEP_SUMMARY contents are credential-free

Given a deploy run completes (success or failure)
When an auditor reads the step summary rendered on the run page
Then the summary MUST NOT contain the Postgres password
And MUST NOT contain the full `postgresql://user:password@host:port/db` URL form
And MUST NOT contain any debug diagnostic line that echoes credentials

#### Scenario: Automated test asserts password never appears in CLI argv

Given the CI migration step runs against the CI database
When an automated test captures the `supabase db push` process argv via `/proc/<pid>/cmdline` (or a subprocess wrapper that records argv)
Then the captured argv MUST NOT contain the password value
And the password MUST be present in the process's env as `PGPASSWORD`
