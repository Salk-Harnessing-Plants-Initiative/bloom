# Change: Add Supabase CLI migration runner to deploy and CI workflows

## Why

The production deploy workflow ([.github/workflows/deploy.yml](.github/workflows/deploy.yml)) currently has **no step to apply database migrations**. On Monday's first deploy, services would start against a database populated only by Supabase services' own init scripts — not by `supabase/migrations/*.sql`. The result:

- `storage.buckets` contains no bucket rows → storage-api returns 404 for every upload
- RLS policies are absent → authenticated users can't read or write rows
- `custom_access_token_hook` (added in #98) is not callable → GoTrue auth fails on login

The CI workflow ([.github/workflows/pr-checks.yml](.github/workflows/pr-checks.yml) lines 342–386) applies migrations by polling for `storage.buckets` and looping over `supabase/migrations/*.sql` with inline `psql` calls. That logic works for CI (fresh database each run) but is unsuitable for deploy: re-running the same migrations on the persistent prod/staging database fails the second deploy because `CREATE TABLE` is not idempotent.

The **Supabase CLI** (`supabase db push --db-url ...`) is the official tool for this workflow. It creates the canonical `supabase_migrations.schema_migrations` tracking table on first run, skips already-applied migrations, wraps each in a transaction, takes an advisory lock, and works against any PostgreSQL database including self-hosted setups (official docs: *"For users managing self-hosted databases, connection parameters can alternatively be supplied via the `--db-url` flag"*).

### Why the Supabase CLI runs on the server (not in a container)

The `supabase/cli` image does **not** exist on Docker Hub — the Supabase CLI is distributed as a Go binary via `.deb`/`.rpm`/`.apk`/`.tar.gz`/NPM/Homebrew only (verified against GitHub releases and Docker Hub API).

This proposal adopts **CLI installed on server + localhost port mapping** as the minimum-effort path that uses official tooling. The port is bound to `127.0.0.1` (loopback only — not reachable from LAN or internet), and parameterized via an env var so prod and staging can coexist on the same host with different host ports.

## What Changes

### 1. Install the Supabase CLI on the Salk server (one-time, documented in #135)

Documented as a pre-deploy step in [#135](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/135):

```bash
SUPABASE_VERSION=2.90.0
curl -LO https://github.com/supabase/cli/releases/download/v${SUPABASE_VERSION}/supabase_${SUPABASE_VERSION}_linux_amd64.deb
sudo apt install -y ./supabase_${SUPABASE_VERSION}_linux_amd64.deb
supabase --version    # verify v2.90.0
```

The `-y` flag is mandatory for non-interactive installation. The CLI-version verification and port-collision check (are `127.0.0.1:5432` / `127.0.0.1:5433` free?) are documented as one-time manual pre-deploy steps in #135, not workflow automation — the Salk server is single-tenant and you control it.

### 2. Install the Supabase CLI on the GitHub CI runner

```yaml
env:
  SUPABASE_VERSION: "2.90.0"

# ...

- name: Install Supabase CLI
  run: |
    curl -LO https://github.com/supabase/cli/releases/download/v${SUPABASE_VERSION}/supabase_${SUPABASE_VERSION}_linux_amd64.deb
    sudo apt install -y ./supabase_${SUPABASE_VERSION}_linux_amd64.deb
    supabase --version | grep -q "${SUPABASE_VERSION}"
```

Same pinned version as the server. `-y` is mandatory — the GitHub runner has no TTY and apt would stall without it.

### 3. Bind Postgres to `127.0.0.1` on a configurable host port

Add to `db-prod` in `docker-compose.prod.yml`:

```yaml
db-prod:
  expose:
    - "5432"
  ports:
    - "127.0.0.1:${POSTGRES_HOST_PORT:-5432}:5432"
```

**Naming**: renamed from `POSTGRES_EXPOSE_PORT` (first revision) to `POSTGRES_HOST_PORT` to differentiate clearly from `POSTGRES_PORT` (container-internal, always 5432).

The `:-5432` fallback is retained intentionally — `POSTGRES_HOST_PORT` is written into the generated env file for every deploy by the workflow, so the fallback never fires in practice. It is kept as defensive cover for local-dev invocations that might not set the var.

### 4. Where `POSTGRES_HOST_PORT` lives

`.env.prod` / `.env.staging` on the server are **regenerated on every deploy** from the heredoc in `deploy.yml:39-102` and `:199-262`. So the port must be added as a **literal line in those heredocs** (it's not a secret — just a non-secret config value), not to the on-server `.env` file.

- `deploy.yml` prod heredoc (new literal line): `POSTGRES_HOST_PORT=5432`
- `deploy.yml` staging heredoc (new literal line): `POSTGRES_HOST_PORT=5433`
- `.env.ci` (checked-in CI env file): `POSTGRES_HOST_PORT=5432`
- `.env.example` and `.env.staging.example`: add `POSTGRES_HOST_PORT` with an inline comment explaining prod=5432 / staging=5433

### 5. Separate migration step in each deploy job

Add to the `deploy-production` job in `deploy.yml`, positioned **after** the health check from PR #142 and **before** the rollback step. The step first waits for `storage.buckets` schema to exist (addresses #138 item 1 storage-ordering intent), then applies migrations.

```yaml
- name: Wait for storage schema (storage-api async init)
  run: |
    ssh -i ~/.ssh/deploy_key ${{ secrets.DEPLOY_USER }}@${{ secrets.DEPLOY_HOST }} "
      cd ${{ secrets.PROD_DEPLOY_PATH }}
      for i in \$(seq 1 60); do
        if docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T db-prod \
             psql -U supabase_admin -d postgres -tAc \
             \"SELECT 1 FROM information_schema.tables WHERE table_schema='storage' AND table_name='buckets'\" 2>/dev/null \
             | grep -q '^1\$'; then
          echo 'storage.buckets ready (attempt '\$i')'; exit 0
        fi
        sleep 2
      done
      echo '::error::storage.buckets did not appear within 120s — storage-api schema init failed'
      exit 1
    "

- name: Apply database migrations (production)
  run: |
    ssh -i ~/.ssh/deploy_key ${{ secrets.DEPLOY_USER }}@${{ secrets.DEPLOY_HOST }} "
      cd ${{ secrets.PROD_DEPLOY_PATH }}
      PG_USER=\$(grep -E '^POSTGRES_USER=' .env.prod | cut -d= -f2-)
      PG_PASSWORD=\$(grep -E '^POSTGRES_PASSWORD=' .env.prod | cut -d= -f2-)
      PG_DB=\$(grep -E '^POSTGRES_DB=' .env.prod | cut -d= -f2-)
      PG_HOST_PORT=\$(grep -E '^POSTGRES_HOST_PORT=' .env.prod | cut -d= -f2-)
      supabase db push --db-url \"postgresql://\${PG_USER}:\${PG_PASSWORD}@127.0.0.1:\${PG_HOST_PORT}/\${PG_DB}\" --yes
    " || {
      echo '::error title=Migration failed (production)::See Layer B diagnostic step and Layer C summary for applied vs pending state.'
      exit 1
    }
```

**Explicit variable extraction** (not `set -a; source; set +a`) avoids leaking `JWT_SECRET`, `SERVICE_ROLE_KEY`, `DB_ENC_KEY` and other secrets into the subprocess environment where a later `set -x` or debug log could expose them.

The `deploy-staging` job gets the identical pattern, using `.env.staging` and the staging host port (5433).

### 6. Migration ordering

The net order is:

1. `docker compose up -d --wait` (PR #142) — containers healthy per their healthchecks
2. **Poll `storage.buckets` existence** (this proposal) — defends against the race where storage-api's Fastify server is up but its async schema init hasn't created the table yet
3. **Apply migrations** via `supabase db push` (this proposal)
4. PR #142's connectivity smoke test
5. Rollback on failure

The polling loop is a direct port of `pr-checks.yml:342-358` and preserves #138 item 1's storage-ordering intent while slotting into PR #142's structure.

### 7. Replace inline psql migration logic in `pr-checks.yml`

Current inline logic at `pr-checks.yml:342–386` becomes:

```yaml
- name: Apply database migrations
  run: |
    PG_USER=$(grep -E '^POSTGRES_USER=' .env.ci | cut -d= -f2-)
    PG_PASSWORD=$(grep -E '^POSTGRES_PASSWORD=' .env.ci | cut -d= -f2-)
    PG_DB=$(grep -E '^POSTGRES_DB=' .env.ci | cut -d= -f2-)
    PG_HOST_PORT=$(grep -E '^POSTGRES_HOST_PORT=' .env.ci | cut -d= -f2-)
    supabase db push --db-url "postgresql://${PG_USER}:${PG_PASSWORD}@127.0.0.1:${PG_HOST_PORT}/${PG_DB}" --yes
```

The existing `Wait for storage schema` step at `pr-checks.yml:342-358` stays (same race applies in CI). The MinIO write test stays as "Verify MinIO writes".

### 8. CI lint: migration filename + sequential timestamps (closes [#130](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/130))

Extracted to `scripts/lint_migrations.sh` so it is testable from pytest fixtures and not duplicated in the workflow:

```bash
#!/usr/bin/env bash
# scripts/lint_migrations.sh
# Validates: new migration filenames match pattern; timestamps strictly increase vs main.
set -euo pipefail

BASE_REF="${1:-origin/main}"
git fetch origin main --depth=1 2>/dev/null || {
  echo "::error::Could not fetch origin/main; cannot compare timestamps."
  exit 1
}

MAX_MAIN_TS=$(git ls-tree -r --name-only "$BASE_REF" -- supabase/migrations/ 2>/dev/null \
  | xargs -I{} basename {} | grep -oE '^[0-9]{14}' | sort -n | tail -1)
MAX_MAIN_TS=${MAX_MAIN_TS:-00000000000000}

for f in supabase/migrations/*.sql; do
  fname=$(basename "$f")
  if git cat-file -e "${BASE_REF}:supabase/migrations/${fname}" 2>/dev/null; then
    continue   # grandfather historical files (158 existing on main)
  fi
  if ! echo "$fname" | grep -qE '^[0-9]{14}_[a-z0-9_]+\.sql$'; then
    echo "::error title=Invalid migration filename::$fname must match YYYYMMDDHHMMSS_lowercase_name.sql"
    exit 1
  fi
  ts=$(echo "$fname" | grep -oE '^[0-9]{14}')
  if [ "$ts" -le "$MAX_MAIN_TS" ]; then
    echo "::error title=Stale migration timestamp::$fname (ts=$ts) is not newer than latest on $BASE_REF (ts=$MAX_MAIN_TS)."
    exit 1
  fi
done
echo "Migration filenames and timestamps OK."
```

Workflow step: `run: ./scripts/lint_migrations.sh`. Testable via pytest + git fixtures (see tasks.md §3).

### 9. Make migration failures highly visible on GitHub Actions

**Layer A — `::error::` annotation on failure.** Wired into Section 5's migration step via `|| { echo '::error title=...'; exit 1; }`.

**Layer B — `supabase migration list` diagnostic step (`if: failure()`).**

```yaml
- name: Show migration status on failure
  if: failure()
  run: |
    ssh ... "
      cd \$PROD_DEPLOY_PATH
      PG_USER=\$(grep -E '^POSTGRES_USER=' .env.prod | cut -d= -f2-)
      PG_PASSWORD=\$(grep -E '^POSTGRES_PASSWORD=' .env.prod | cut -d= -f2-)
      PG_DB=\$(grep -E '^POSTGRES_DB=' .env.prod | cut -d= -f2-)
      PG_HOST_PORT=\$(grep -E '^POSTGRES_HOST_PORT=' .env.prod | cut -d= -f2-)
      supabase migration list --db-url \"postgresql://\${PG_USER}:\${PG_PASSWORD}@127.0.0.1:\${PG_HOST_PORT}/\${PG_DB}\"
    " || echo '::warning::Diagnostic fetch failed'
```

**Layer C — Migration state in `$GITHUB_STEP_SUMMARY` (every run).** Uses raw `supabase migration list` markdown output — the `-o json` flag does NOT exist on `migration list` in v2.90.0 (verified against CLI source at the pinned tag):

```yaml
- name: Migration summary
  if: always()
  run: |
    {
      echo "## Database migration state"
      echo ""
      echo '```'
      ssh ... "
        cd \$PROD_DEPLOY_PATH
        PG_USER=\$(grep -E '^POSTGRES_USER=' .env.prod | cut -d= -f2-)
        PG_PASSWORD=\$(grep -E '^POSTGRES_PASSWORD=' .env.prod | cut -d= -f2-)
        PG_DB=\$(grep -E '^POSTGRES_DB=' .env.prod | cut -d= -f2-)
        PG_HOST_PORT=\$(grep -E '^POSTGRES_HOST_PORT=' .env.prod | cut -d= -f2-)
        supabase migration list --db-url \"postgresql://\${PG_USER}:\${PG_PASSWORD}@127.0.0.1:\${PG_HOST_PORT}/\${PG_DB}\"
      " 2>/dev/null || echo '(migration state fetch failed)'
      echo '```'
    } >> "$GITHUB_STEP_SUMMARY"
```

The `migration list` command's native output is a markdown table with columns `Local | Remote | Time (UTC)`. Wrapping it in a code fence and writing to `$GITHUB_STEP_SUMMARY` renders cleanly at the top of the workflow run page.

### 10. Credential handling: `PGPASSWORD` env var + no `--debug` in step summary

All three migration invocations (prod deploy, staging deploy, CI) MUST pass the Postgres password via the `PGPASSWORD` environment variable, not embedded in the `--db-url` argument string. The Supabase CLI, like every libpq-based tool, honours `PGPASSWORD` — the URL only needs user, host, port, and database.

This closes two leak channels:

- **Process argv.** `ps auxww` / `/proc/<pid>/cmdline` on the Salk server expose the full command line of every running process. With the password in `--db-url`, any other host-local process can read the live prod credentials while migrations are running. With `PGPASSWORD`, the password is only in the process's env (not argv), and tools like `ps -e` do not show env by default.
- **`$GITHUB_STEP_SUMMARY`.** The Layer C "Migration summary" step captures `supabase migration list` output and writes it into the step summary, which is rendered on the GitHub run page for every repo collaborator. With `--debug` on, the CLI echoes the resolved connection string — including the password in the URL form — as a diagnostic line. Removing `--debug` from the Layer C step and using `PGPASSWORD` for the connection means neither the URL nor the password ever reaches the summary.

`::add-mask::` is NOT a sufficient mitigation — it scrubs subsequent job-log lines within the same step, but does not mask `$GITHUB_STEP_SUMMARY` content (summary files are written raw and then rendered), and it does not cover URL-encoded variants of the password.

Revised pattern (prod, staging, CI identical):

```yaml
- name: Apply database migrations (production)
  run: |
    ssh ... "
      set -euo pipefail
      cd ${{ secrets.PROD_DEPLOY_PATH }}
      PG_USER=\$(grep -E '^POSTGRES_USER=' .env.prod | cut -d= -f2-)
      PG_PASSWORD=\$(grep -E '^POSTGRES_PASSWORD=' .env.prod | cut -d= -f2-)
      PG_DB=\$(grep -E '^POSTGRES_DB=' .env.prod | cut -d= -f2-)
      PG_HOST_PORT=\$(grep -E '^POSTGRES_HOST_PORT=' .env.prod | cut -d= -f2-)
      export PGPASSWORD=\"\$PG_PASSWORD\"
      supabase db push \\
        --db-url \"postgresql://\${PG_USER}@127.0.0.1:\${PG_HOST_PORT}/\${PG_DB}?sslmode=disable\" \\
        --yes
    " || { echo '::error title=Migration failed (production)::See Layer B diagnostic step and Layer C summary for applied vs pending state.'; exit 1; }
```

The Layer B `if: failure()` diagnostic step MAY keep `--debug` since its output is not piped into `$GITHUB_STEP_SUMMARY` — but even there, the connection URL MUST NOT be echoed into any captured log that is later persisted or surfaced. The Layer C summary step MUST drop `--debug` entirely and capture only the markdown state table (`Local | Remote | Time (UTC)`) from `supabase migration list`'s native output.

## Operating policy: migrations reach prod ONLY through this workflow

Added to [#135](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/135):

> **Migrations to production and staging are applied EXCLUSIVELY through the GitHub Actions deploy workflow. Do not manually run `supabase db push` or any migration command via SSH on the server except for documented emergency recovery. Any emergency manual operation must be logged on an incident ticket.**

Enforced by: branch protection on `main` (migration files review-gated), SSH access restricted to deploy user, documented policy, audit trail via `supabase_migrations.schema_migrations` timestamps vs GitHub Actions deploy run timestamps.

## Out of scope (tracked separately)

This proposal is deliberately scoped to **migration application** for Monday's first deploy. The following are deferred to a follow-up proposal (`improve-deploy-migration-rollback`):

- Automatic rollback of committed-in-batch migrations on failure
- CI lint requiring rollback file for every new migration
- **Partial-apply state contract and operator runbook** (what happens when a batch fails mid-way, RLS-specific partial-apply mitigation, audit trail)
- Pre-migration `pg_dump` snapshots (ongoing). A one-time snapshot before Monday's first deploy is an optional recommendation in #135, not a spec requirement.
- Backfilling rollback files for 156 historical migrations

## Impact

### Files modified
- `docker-compose.prod.yml` — 1 new `ports:` line on `db-prod`
- `.github/workflows/deploy.yml` — 2 new `POSTGRES_HOST_PORT=...` lines in heredocs (prod + staging) + 3 new steps per env (wait-for-storage, apply migrations, Layer B diagnostic) + Layer C summary step per env
- `.github/workflows/pr-checks.yml` — CLI install step + migration step + lint step, replaces inline psql logic
- `.env.ci`, `.env.example`, `.env.staging.example` — new `POSTGRES_HOST_PORT` entry
- `scripts/lint_migrations.sh` (new) — extracted filename + timestamp lint
- `tests/integration/test_migrations.py` (new) — integration tests for migration state
- `tests/integration/test_lint_migrations.py` (new) — lint script tests
- [#135](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/135) — CLI install steps, port-collision check, CLI version check, only-workflow policy statement, optional one-time pg_dump recommendation

### Issues this PR closes when merged
- [#130](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/130) — CI validates migration timestamps are sequential
- [#138 item 1](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/138) — Migrations on deploy (with Section 6 ordering documented)
- [#113](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/113) — Add automated database migration runner to CI/CD pipeline (the "Resolved" label was premature; this proposal implements the runner it describes)

### Follow-up issues to raise before this merges
- **Proposal 2 tracker**: rollback automation, **partial-apply state contract + recovery runbook** (I8), RLS-specific partial-apply mitigation, historical rollback backfill, ongoing pg_dump strategy
- Local dev: migrate Makefile workflow from `public._migrations` to `supabase_migrations.schema_migrations` tracking

## Dependencies

- **Docker Compose v2.17+** (PR #142 already requires)
- **Supabase CLI v2.90.0** on server and CI runner (pinned; `.deb` from GitHub releases)
- **PR #142** must merge first — this proposal positions migrations after that PR's health-check step

## Risks

- **Single-tenant security posture**: port bound to `127.0.0.1` only. Marginal increase over today on the single-user Salk server.
- **Tracking-table divergence with local dev.** Local Makefile workflow uses `public._migrations`. Running `supabase db push` against a DB previously bootstrapped with local Makefile will attempt to re-apply all 158 migrations because the CLI sees an empty `supabase_migrations.schema_migrations`. **Local-dev-only footgun — prod is unaffected because the operating policy above guarantees only the CI workflow touches prod.** Follow-up issue tracked to migrate local-dev Makefile to the canonical table.
- **Partial-apply state on mid-batch failure.** If `supabase db push` applies migrations 1-2 successfully then migration 3 fails, migrations 1-2 remain committed and the operator must manually fix migration 3 and redeploy. **Contract, recovery runbook, and RLS partial-apply mitigation are all in scope for Proposal 2, not this PR.** Flagged here only to acknowledge the risk exists.
- **First-deploy timing**: 158 migrations on a fresh prod DB in one batch. No explicit step timeout — relying on GitHub Actions global job timeout (6h).
- **Coordination with [#137](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/137) (SSH heredoc refactor)**: this proposal adds 6 new SSH blocks per deploy (3 prod + 3 staging: wait-for-storage, apply, diagnostic, summary — times two environments). They use the existing heavy-escaping style. **The new blocks are added to #137's refactor scope** — when #137 lands, all 16+ SSH blocks will be refactored together. Task 10.5 ensures #137 is updated with a comment listing the new blocks when this PR merges.
