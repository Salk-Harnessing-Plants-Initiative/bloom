---
name: Database Migration
description: Create and manage Supabase database migrations for schema changes
category: Database
tags: [database, migrations, supabase, postgresql, schema]
---

# Database Migrations

Guide for creating and managing database migrations in Bloom. The project uses self-hosted Supabase via Docker Compose with migrations managed through the Makefile and psql.

## Quick Commands

```bash
# Create a new migration file
make new-migration name=add_experiment_status

# Apply pending migrations to local dev database
make apply-migrations-local

# Generate TypeScript types from database schema
make gen-types

# Check database connectivity
docker exec db-dev pg_isready -U supabase_admin -h localhost

# View database logs
docker compose -f docker-compose.dev.yml logs db-dev --tail=50

# Direct psql access
PGPASSWORD=postgres psql -h localhost -p 5432 -U supabase_admin -d postgres
```

## Migration Workflow

### 1. Create Migration File

```bash
make new-migration name=create_experiments_table
# Creates: supabase/migrations/<timestamp>_create_experiments_table.sql
```

### 2. Write SQL Migration

Edit the generated file in `supabase/migrations/`:

```sql
-- Migration: create_experiments_table
-- Created: 2026-04-08

-- Create the table
CREATE TABLE IF NOT EXISTS public.experiments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    description TEXT,
    organism_id UUID REFERENCES public.organisms(id),
    created_by UUID REFERENCES auth.users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Add RLS policy
ALTER TABLE public.experiments ENABLE ROW LEVEL SECURITY;

-- Users can read their own experiments
CREATE POLICY "Users can read own experiments"
    ON public.experiments
    FOR SELECT
    USING (auth.uid() = created_by);

-- Users can insert their own experiments
CREATE POLICY "Users can insert own experiments"
    ON public.experiments
    FOR INSERT
    WITH CHECK (auth.uid() = created_by);

-- Add index for common queries
CREATE INDEX IF NOT EXISTS idx_experiments_organism_id ON public.experiments(organism_id);
CREATE INDEX IF NOT EXISTS idx_experiments_created_by ON public.experiments(created_by);
```

### 3. Apply Migration

```bash
# Ensure dev stack is running
make dev-up

# Apply the migration
make apply-migrations-local
```

### 4. Generate Types

```bash
# Update TypeScript types from new schema
make gen-types
```

### 5. Test and Commit

```bash
# Verify the migration worked
PGPASSWORD=postgres psql -h localhost -p 5432 -U supabase_admin -d postgres -c "\dt public.*"

# Commit the migration and generated types
git add supabase/migrations/ packages/*/src/types/database.types.ts web/lib/database.types.ts
git commit -m "feat: add experiments table with RLS policies"
```

## Common SQL Patterns

### Add Column

```sql
ALTER TABLE public.experiments ADD COLUMN status TEXT DEFAULT 'active';
```

### Add Foreign Key

```sql
ALTER TABLE public.scans ADD COLUMN experiment_id UUID REFERENCES public.experiments(id);
```

### Create Index

```sql
CREATE INDEX IF NOT EXISTS idx_scans_experiment_id ON public.scans(experiment_id);
```

### RLS Policies

```sql
-- Enable RLS
ALTER TABLE public.my_table ENABLE ROW LEVEL SECURITY;

-- Read own data
CREATE POLICY "Users read own data" ON public.my_table
    FOR SELECT USING (auth.uid() = user_id);

-- Insert own data
CREATE POLICY "Users insert own data" ON public.my_table
    FOR INSERT WITH CHECK (auth.uid() = user_id);

-- Update own data
CREATE POLICY "Users update own data" ON public.my_table
    FOR UPDATE USING (auth.uid() = user_id);

-- Service role bypass (for server-side operations)
CREATE POLICY "Service role full access" ON public.my_table
    FOR ALL USING (auth.role() = 'service_role');
```

## Resetting the Database

**WARNING: This deletes all data!**

```bash
# Stop dev stack, remove volumes, restart, reapply migrations
docker compose -f docker-compose.dev.yml down -v
make dev-up
make apply-migrations-local

# Optionally reload test data
make load-test-data
```

## Troubleshooting

### Migration already applied

The Makefile tracks applied migrations in a `_migrations` table. If a migration was partially applied:

```bash
# Check which migrations are recorded
PGPASSWORD=postgres psql -h localhost -p 5432 -U supabase_admin -d postgres \
    -c "SELECT * FROM _migrations ORDER BY applied_at;"

# Remove a migration record to re-run it
PGPASSWORD=postgres psql -h localhost -p 5432 -U supabase_admin -d postgres \
    -c "DELETE FROM _migrations WHERE name = '<filename>.sql';"

# Re-apply
make apply-migrations-local
```

### Foreign key constraint errors

Check that referenced tables and rows exist:

```bash
PGPASSWORD=postgres psql -h localhost -p 5432 -U supabase_admin -d postgres \
    -c "SELECT id FROM public.organisms LIMIT 5;"
```

### RLS blocking queries

```bash
# Check RLS policies
PGPASSWORD=postgres psql -h localhost -p 5432 -U supabase_admin -d postgres -c "\dp public.my_table"

# Test as service role (bypasses RLS)
# Use the service_role key in your Supabase client
```

### Database not running

```bash
docker compose -f docker-compose.dev.yml ps db-dev
docker compose -f docker-compose.dev.yml logs db-dev --tail=30
docker exec db-dev pg_isready -U supabase_admin -h localhost
```

## Related Commands

- `/validate-env` — check database connectivity
- `/run-ci-locally` — run integration tests against database
- `/pre-merge` — pre-merge checklist includes migration verification