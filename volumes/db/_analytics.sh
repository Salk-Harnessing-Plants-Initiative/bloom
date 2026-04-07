#!/bin/bash
set -e

echo ">>> [INIT] Waiting for Postgres to be ready..."
until pg_isready -U "$POSTGRES_USER" -d "_supabase" > /dev/null 2>&1; do
  sleep 2
done

echo ">>> [INIT] Ensuring _analytics schema and supabase_admin role exist..."

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "_supabase" <<-EOSQL
  DO
  \$\$
  BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'supabase_admin') THEN
      CREATE ROLE supabase_admin LOGIN PASSWORD '${POSTGRES_PASSWORD}' SUPERUSER;
      RAISE NOTICE 'Created supabase_admin role.';
    END IF;
  END
  \$\$;

  DO
  \$\$
  BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = '_analytics') THEN
      EXECUTE 'CREATE SCHEMA _analytics AUTHORIZATION supabase_admin';
      RAISE NOTICE 'Created _analytics schema.';
    END IF;
  END
  \$\$;
EOSQL

echo ">>> [INIT] _analytics schema setup complete."
