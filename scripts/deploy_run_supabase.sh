#!/usr/bin/env bash
# =============================================================================
# Run a supabase CLI command inside the db-prod container.
#
# Why this exists: on the Salk deploy server, the host's supabase CLI (Go pgx
# driver) cannot reach Postgres on 127.0.0.1 — Go's net.Dial times out where
# Python and libpq connect fine on the same address. The root cause hasn't
# been pinned down (likely a kernel/network-namespace interaction), but the
# workaround is reliable: run the CLI inside the db-prod container, where
# localhost:5432 reaches the Postgres process directly without the docker-proxy
# hop the host must go through.
#
# This script copies the CLI binary + the supabase/ project directory into
# the running db-prod container, runs the requested supabase subcommand, and
# cleans up the temp files afterwards.
#
# Usage:
#   scripts/deploy_run_supabase.sh <env> <supabase-subcommand-with-flags>
# Examples:
#   scripts/deploy_run_supabase.sh staging "migration list --debug"
#   scripts/deploy_run_supabase.sh prod    "db push --debug --yes"
#
# Must be run from the deploy directory (where docker-compose.prod.yml and
# .env.{prod,staging} live), with `supabase` CLI on PATH.
#
# Cleanup is guaranteed via `trap EXIT` — even if the supabase command fails,
# the temp files in /tmp/sb and /tmp/proj inside the container are removed.
# =============================================================================
set -euo pipefail

ENV="${1:?missing environment arg (staging|prod)}"
shift
SUBCMD="$*"
: "${SUBCMD:?missing supabase subcommand}"

case "$ENV" in
  prod)
    PROJECT="bloom_v2_prod"
    ENV_FILE=".env.prod"
    ;;
  staging)
    PROJECT="bloom_v2_staging"
    ENV_FILE=".env.staging"
    ;;
  *)
    echo "::error::Unknown environment: $ENV (expected 'prod' or 'staging')" >&2
    exit 2
    ;;
esac

if [ ! -f "$ENV_FILE" ]; then
  echo "::error::env file not found: $ENV_FILE (run from deploy dir)" >&2
  exit 2
fi

PG_USER=$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | cut -d= -f2-)
PG_PASSWORD=$(grep -E '^POSTGRES_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)
PG_DB=$(grep -E '^POSTGRES_DB=' "$ENV_FILE" | cut -d= -f2-)
: "${PG_USER:?POSTGRES_USER missing from $ENV_FILE}"
: "${PG_PASSWORD:?POSTGRES_PASSWORD missing from $ENV_FILE}"
: "${PG_DB:?POSTGRES_DB missing from $ENV_FILE}"

CONTAINER="${PROJECT}-db-prod-1"
COMPOSE="docker compose -p ${PROJECT} -f docker-compose.prod.yml --env-file ${ENV_FILE}"

command -v supabase >/dev/null || {
  echo "::error::supabase CLI not found on host PATH" >&2
  exit 1
}

# Always cleanup, even if setup fails partway. Cleanup is idempotent
# (rm -rf + 2>/dev/null), so registering the trap before the setup commands
# is safe — and necessary, otherwise a failure between line 1 of setup and
# the trap registration would orphan files in the container.
cleanup() {
  $COMPOSE exec -T db-prod rm -rf /tmp/sb /tmp/proj 2>/dev/null || true
}
trap cleanup EXIT

# Copy CLI binary + project files into db-prod.
# Stderr from docker cp is preserved so real errors aren't hidden; stdout
# (the "Successfully copied ..." progress lines) is suppressed so the
# Migration summary code block in the run summary panel stays clean.
#
# rm -rf /tmp/proj before the second cp: docker cp into an existing directory
# MERGES contents rather than replacing them. Without the rm, stale migration
# files from a prior run (where cleanup was swallowed by `|| true`) would mix
# with the current commit's migrations — could re-apply migrations that were
# deleted or modified upstream.
docker cp "$(which supabase)" "${CONTAINER}:/tmp/sb" >/dev/null
$COMPOSE exec -T db-prod rm -rf /tmp/proj >/dev/null
$COMPOSE exec -T db-prod mkdir -p /tmp/proj >/dev/null
docker cp ./supabase "${CONTAINER}:/tmp/proj/" >/dev/null

# Run the supabase subcommand inside the container.
# - DO_NOT_TRACK=1 disables PostHog telemetry (Salk's network may block it).
# - PGPASSWORD is the standard libpq/pgx env var for the connection password.
#   Using it + a passwordless --db-url keeps the password out of the supabase
#   process's argv (otherwise visible via /proc/<pid>/cmdline to anyone with
#   `docker exec` rights inside the container).
# - --db-url uses localhost (intra-container loopback to local Postgres).
$COMPOSE exec -T \
  -e DO_NOT_TRACK=1 \
  -e PGPASSWORD="$PG_PASSWORD" \
  db-prod \
  bash -c "cd /tmp/proj && /tmp/sb $SUBCMD --db-url \"postgresql://${PG_USER}@localhost:5432/${PG_DB}?sslmode=disable\""
