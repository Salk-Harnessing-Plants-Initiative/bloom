#!/bin/bash
set -e

echo ">>> Running _supabase.sh initialization script..."

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
-- Enable dblink, required for creating DB outside the current transaction
CREATE EXTENSION IF NOT EXISTS dblink;

DO
$$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_database WHERE datname = '_supabase') THEN
      PERFORM dblink_exec('dbname=' || current_database(),
         'CREATE DATABASE _supabase OWNER ' || current_user);
      RAISE NOTICE '_supabase database created successfully';
   ELSE
      RAISE NOTICE '_supabase database already exists';
   END IF;
END
$$;
EOSQL
