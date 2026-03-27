#!/bin/bash
# Initialize Supabase database roles and schemas
# Run this after a fresh database setup when volumes/db/init/ scripts
# weren't present during first boot.
#
# Usage: ./scripts/init-db-roles.sh

set -e

ENV_FILE="${1:-.env.prod}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Error: $ENV_FILE not found"
  exit 1
fi

PGPASSWORD=$(grep POSTGRES_PASSWORD "$ENV_FILE" | cut -d= -f2)
export PGPASSWORD

PSQL="docker exec -e PGPASSWORD=$PGPASSWORD db-prod psql -U supabase_admin -d postgres"

echo "=== Running init SQL files ==="
for f in volumes/db/init/*.sql; do
  echo "  Running: $(basename $f)"
  docker exec -i -e PGPASSWORD="$PGPASSWORD" db-prod psql -U supabase_admin -d postgres < "$f" 2>&1 | grep -i error || true
done

echo ""
echo "=== Setting role passwords ==="
$PSQL -c "ALTER USER authenticator WITH PASSWORD '$PGPASSWORD';" 2>/dev/null || true
$PSQL -c "ALTER USER supabase_auth_admin WITH PASSWORD '$PGPASSWORD';" 2>/dev/null || true
$PSQL -c "ALTER USER supabase_storage_admin WITH PASSWORD '$PGPASSWORD';" 2>/dev/null || true
$PSQL -c "ALTER USER supabase_functions_admin WITH PASSWORD '$PGPASSWORD';" 2>/dev/null || true
$PSQL -c "ALTER USER pgbouncer WITH PASSWORD '$PGPASSWORD';" 2>/dev/null || true
$PSQL -c "ALTER USER postgres WITH PASSWORD '$PGPASSWORD';" 2>/dev/null || true

echo ""
echo "=== Skipping _supabase database (analytics removed) ==="

echo ""
echo "=== Done. Restart services: ==="
echo "  docker compose -f docker-compose.prod.yml --env-file $ENV_FILE restart auth"
echo "  docker compose -f docker-compose.prod.yml --env-file $ENV_FILE up -d"
