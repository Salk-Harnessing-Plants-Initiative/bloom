---
name: Database Migration
description: Create and manage Supabase database migrations for schema changes
category: Database
tags: [database, migrations, supabase, postgresql, schema]
---

# Database Migrations with Supabase

Guide for creating and managing Supabase database migrations for schema changes in Bloom (PostgreSQL + RLS policies).

## Quick Commands

### Create New Migration

```bash
# Create empty migration file
supabase migration new <migration_name>

# Example
supabase migration new add_experiment_status_field
```

### Apply Migrations

```bash
# Apply all pending migrations to local database
supabase db push

# Or reset and reapply all migrations (WARNING: deletes all data!)
supabase db reset
```

### Check Migration Status

```bash
# List all migrations and their status
supabase migration list

# Check for schema drift (unapplied changes)
supabase db diff

# Compare local with remote (if using hosted Supabase)
supabase db diff --linked
```

### Generate Migration from Schema Changes

```bash
# Make changes via Supabase Studio (http://localhost:54323)
# Then generate migration from changes
supabase db diff --schema public -f migration_name

# This creates: supabase/migrations/<timestamp>_migration_name.sql
```

## Migration Workflow

### 1. Modify Schema

**Option A: Write SQL directly**

Create migration file:

```bash
supabase migration new add_status_to_experiments
```

Edit `supabase/migrations/<timestamp>_add_status_to_experiments.sql`:

```sql
-- Add status column to experiments table
ALTER TABLE public.experiments
ADD COLUMN status TEXT NOT NULL DEFAULT 'draft'
CHECK (status IN ('draft', 'active', 'completed', 'archived'));

-- Add index for faster queries
CREATE INDEX idx_experiments_status ON public.experiments(status);

-- Update RLS policy to include new field
DROP POLICY IF EXISTS "Users can view own experiments" ON public.experiments;

CREATE POLICY "Users can view own experiments"
ON public.experiments FOR SELECT
USING (auth.uid() = user_id);

-- Add comment for documentation
COMMENT ON COLUMN public.experiments.status IS 'Current status of the experiment';
```

**Option B: Use Supabase Studio**

1. Open Supabase Studio: http://localhost:54323
2. Navigate to Table Editor
3. Make changes via UI (add columns, change types, etc.)
4. Generate migration from changes:
   ```bash
   supabase db diff -f add_status_to_experiments
   ```

### 2. Test Migration Locally

```bash
# Apply migration
supabase db push

# Verify in Supabase Studio
open http://localhost:54323

# Or check via psql
psql postgresql://postgres:postgres@localhost:54322/postgres -c "\d experiments"

# Run application to test
make dev-up
```

### 3. Verify Migration SQL

Check the generated SQL:

- [ ] Column types correct (TEXT, INTEGER, TIMESTAMP, UUID, etc.)
- [ ] Default values appropriate
- [ ] NOT NULL vs nullable correct
- [ ] Foreign keys reference correct tables
- [ ] Indexes created for frequently queried columns
- [ ] RLS policies updated if table security changed
- [ ] Comments added for documentation

### 4. Test with Data

```bash
# Insert test data
psql postgresql://postgres:postgres@localhost:54322/postgres

postgres=# INSERT INTO experiments (name, description, user_id, status)
postgres=# VALUES ('Test Experiment', 'Testing new status field', 'test-user-uuid', 'active');

# Verify data integrity
postgres=# SELECT * FROM experiments WHERE status = 'active';

# Test RLS policies (should only see own experiments)
postgres=# SET request.jwt.claim.sub = 'test-user-uuid';
postgres=# SELECT * FROM experiments;
```

### 5. Commit Migration

```bash
# Add migration file
git add supabase/migrations/<timestamp>_add_status_to_experiments.sql

# Commit with descriptive message
git commit -m "feat: add status field to experiments table

- Add status column with check constraint
- Add index for status queries
- Update RLS policies
- Add documentation comments"
```

## Database Locations

### Local Development (Supabase)

- **Host**: `localhost:54322` (PostgreSQL)
- **Database**: `postgres`
- **User**: `postgres`
- **Password**: `postgres`
- **Connection String**: `postgresql://postgres:postgres@localhost:54322/postgres`
- **Studio**: http://localhost:54323

### Supabase Services

- **API Gateway (Kong)**: http://localhost:54321
- **PostgreSQL**: localhost:54322
- **Studio**: http://localhost:54323
- **Inbucket (Email)**: http://localhost:54324
- **Storage (S3)**: http://localhost:54325

### Production (Future)

- **Self-hosted Supabase** (when deployed)
- Connection string from environment variables
- Migrations applied via CI/CD or manual deployment

## Migration Best Practices

### Naming Conventions

Use descriptive, verb-led names with underscores:

✅ **Good**:

- `add_status_to_experiments`
- `create_trials_table`
- `rename_video_url_to_video_key`
- `add_rls_policies_to_frames`
- `create_index_on_videos_experiment_id`

❌ **Bad**:

- `migration1`
- `update`
- `changes`
- `new-column` (use underscores, not hyphens)

### Schema Organization

Follow Bloom's schema structure:

```sql
-- Core tables
public.experiments
public.videos
public.trials
public.frames

-- Supporting tables
public.audit_logs
storage.objects (managed by Supabase Storage)
```

### RLS Policy Patterns

Always include RLS policies for security:

```sql
-- Enable RLS on new tables
ALTER TABLE public.trials ENABLE ROW LEVEL SECURITY;

-- Policy: Users can view own data
CREATE POLICY "Users can view own trials"
ON public.trials FOR SELECT
USING (
  auth.uid() = user_id
  OR
  EXISTS (
    SELECT 1 FROM public.experiments
    WHERE experiments.id = trials.experiment_id
    AND experiments.user_id = auth.uid()
  )
);

-- Policy: Users can insert own data
CREATE POLICY "Users can insert own trials"
ON public.trials FOR INSERT
WITH CHECK (
  EXISTS (
    SELECT 1 FROM public.experiments
    WHERE experiments.id = trials.experiment_id
    AND experiments.user_id = auth.uid()
  )
);

-- Policy: Users can update own data
CREATE POLICY "Users can update own trials"
ON public.trials FOR UPDATE
USING (
  EXISTS (
    SELECT 1 FROM public.experiments
    WHERE experiments.id = trials.experiment_id
    AND experiments.user_id = auth.uid()
  )
);

-- Policy: Users can delete own data
CREATE POLICY "Users can delete own trials"
ON public.trials FOR DELETE
USING (
  EXISTS (
    SELECT 1 FROM public.experiments
    WHERE experiments.id = trials.experiment_id
    AND experiments.user_id = auth.uid()
  )
);
```

### Data Migrations

For complex migrations needing data transformation:

```sql
-- Migration: normalize_video_durations.sql

-- Add new column
ALTER TABLE public.videos
ADD COLUMN duration_seconds INTEGER;

-- Transform existing data (duration_ms to duration_seconds)
UPDATE public.videos
SET duration_seconds = FLOOR(duration_ms / 1000.0);

-- Add constraint after data migration
ALTER TABLE public.videos
ADD CONSTRAINT duration_seconds_positive CHECK (duration_seconds >= 0);

-- Optionally drop old column (be careful!)
-- ALTER TABLE public.videos DROP COLUMN duration_ms;
```

### Indexes for Performance

Add indexes for frequently queried columns:

```sql
-- Single column index
CREATE INDEX idx_videos_experiment_id ON public.videos(experiment_id);

-- Composite index for common query patterns
CREATE INDEX idx_videos_experiment_status ON public.videos(experiment_id, status);

-- Partial index for specific conditions
CREATE INDEX idx_videos_processing ON public.videos(status)
WHERE status = 'processing';

-- Full-text search index
CREATE INDEX idx_experiments_name_search ON public.experiments
USING GIN (to_tsvector('english', name));
```

### Foreign Keys and Constraints

Maintain referential integrity:

```sql
-- Foreign key with cascade delete
ALTER TABLE public.trials
ADD CONSTRAINT fk_trials_experiment
FOREIGN KEY (experiment_id)
REFERENCES public.experiments(id)
ON DELETE CASCADE;

-- Check constraint
ALTER TABLE public.videos
ADD CONSTRAINT valid_duration CHECK (duration_seconds > 0 AND duration_seconds < 86400);

-- Unique constraint
ALTER TABLE public.experiments
ADD CONSTRAINT unique_experiment_name_per_user UNIQUE (user_id, name);
```

## Testing Migrations

Always test:

1. **Fresh database**: Migration works on empty database

   ```bash
   supabase db reset
   # Database recreated with all migrations
   ```

2. **Existing data**: Migration preserves and transforms existing data correctly

   ```bash
   # Seed test data
   psql postgresql://postgres:postgres@localhost:54322/postgres < scripts/seed_test_data.sql

   # Apply new migration
   supabase db push

   # Verify data integrity
   psql postgresql://postgres:postgres@localhost:54322/postgres -c "SELECT * FROM experiments LIMIT 10"
   ```

3. **RLS policies**: Verify policies work correctly

   ```bash
   # Test with different user contexts
   psql postgresql://postgres:postgres@localhost:54322/postgres

   -- Set user context
   SET request.jwt.claim.sub = 'user-uuid';

   -- Should only see own data
   SELECT * FROM experiments;
   ```

4. **Application integration**: App still works after migration
   ```bash
   make dev-up
   # Test CRUD operations via UI
   ```

## Common Issues

### "Migration already applied"

**Cause**: Migration file exists but database thinks it's already applied

**Solution**:

```bash
# Check migration status
supabase migration list

# If migration needs to be reapplied
supabase db reset

# Or manually mark as applied (if SQL already ran)
supabase migration repair --status applied <migration_id>
```

### "Database schema is not in sync"

**Cause**: Manual database changes or migration issues

**Solution**:

```bash
# Check for drift
supabase db diff

# Generate migration from current schema
supabase db diff -f sync_schema_changes

# Review generated SQL, then apply
supabase db push
```

### "RLS policy blocks query"

**Cause**: RLS policy too restrictive or JWT context not set

**Debug**:

```bash
# Test without RLS (use service_role key in application)
# Or disable RLS temporarily for debugging
psql postgresql://postgres:postgres@localhost:54322/postgres

-- Disable RLS on table (DANGER: only for debugging)
ALTER TABLE public.experiments DISABLE ROW LEVEL SECURITY;

-- Check policy definitions
SELECT * FROM pg_policies WHERE tablename = 'experiments';

-- Re-enable RLS after debugging
ALTER TABLE public.experiments ENABLE ROW LEVEL SECURITY;
```

**Fix**: Update RLS policy to allow correct access:

```sql
-- More permissive policy example
CREATE POLICY "Users can view own experiments or shared"
ON public.experiments FOR SELECT
USING (
  auth.uid() = user_id
  OR
  is_public = true
);
```

### "Foreign key constraint fails"

**Cause**: Referenced data doesn't exist or cascade rules incorrect

**Debug**:

```bash
# Check referenced data exists
SELECT * FROM public.experiments WHERE id = 'experiment-uuid';

# Check foreign key constraints
SELECT
  tc.table_name,
  kcu.column_name,
  ccu.table_name AS foreign_table_name,
  ccu.column_name AS foreign_column_name
FROM information_schema.table_constraints AS tc
JOIN information_schema.key_column_usage AS kcu
  ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.constraint_column_usage AS ccu
  ON ccu.constraint_name = tc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name = 'trials';
```

**Fix**: Add cascade rules or ensure referenced data exists:

```sql
-- Update foreign key to cascade deletes
ALTER TABLE public.trials
DROP CONSTRAINT fk_trials_experiment;

ALTER TABLE public.trials
ADD CONSTRAINT fk_trials_experiment
FOREIGN KEY (experiment_id)
REFERENCES public.experiments(id)
ON DELETE CASCADE;
```

### "Migration fails in production"

**Cause**: Syntax error, missing permissions, or data incompatibility

**Debug**:

1. Check migration SQL manually:

   ```bash
   psql <production_connection_string> < supabase/migrations/XXX_migration.sql
   ```

2. Check error logs:

   ```bash
   supabase logs db
   ```

3. Verify permissions:
   ```bash
   # Ensure postgres user has correct permissions
   psql <connection_string> -c "\du"
   ```

**Prevention**:

- Test migrations locally first
- Use PostgreSQL-compatible SQL only
- Avoid operations that lock tables for long periods
- Consider downtime for major migrations

## Rollback Procedures

### Development

```bash
# Reset to clean state (loses all data)
supabase db reset

# Or manually revert migration
psql postgresql://postgres:postgres@localhost:54322/postgres

-- Manually undo changes
DROP TABLE IF EXISTS public.new_table;
ALTER TABLE public.experiments DROP COLUMN IF EXISTS status;
```

### Production

**Manual rollback** (no automatic rollback):

1. **Backup database**:

   ```bash
   # If using hosted Supabase
   # Use Supabase Dashboard → Database → Backups

   # If self-hosted
   pg_dump <connection_string> > backup_$(date +%Y%m%d_%H%M%S).sql
   ```

2. **Revert migration manually**:

   ```sql
   -- Write inverse migration
   ALTER TABLE public.experiments DROP COLUMN status;
   DROP INDEX IF EXISTS idx_experiments_status;
   ```

3. **Test after rollback**:
   ```bash
   # Verify application still works
   curl http://localhost:5002/api/experiments
   ```

**Prevention**: Always backup before applying migrations in production

## Supabase Studio Usage

### Local Development

```bash
# Start Supabase (if not running)
supabase start

# Open Studio
open http://localhost:54323

# Or specify in browser
# Username: postgres
# Password: postgres (from supabase status)
```

**Studio features:**

- **Table Editor**: View/edit data, modify schema
- **SQL Editor**: Run custom queries
- **Database**: View schema, indexes, policies
- **Authentication**: Manage users
- **Storage**: Manage files
- **Logs**: View realtime logs

### Schema Changes via Studio

1. Go to Table Editor
2. Create/modify tables visually
3. Generate migration:
   ```bash
   supabase db diff -f changes_from_studio
   ```
4. Review generated SQL
5. Apply migration:
   ```bash
   supabase db push
   ```

## Schema File Organization

**Location**: `supabase/migrations/`

Migration files follow this format:

```
<timestamp>_<description>.sql

Examples:
20250107120000_create_experiments_table.sql
20250107120100_add_rls_policies.sql
20250107120200_create_videos_table.sql
```

**Timestamp format**: `YYYYMMDDHHmmss` (automatically generated)

## Example Migrations

### Creating a New Table

```sql
-- Migration: create_trials_table.sql

-- Create trials table
CREATE TABLE public.trials (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  experiment_id UUID NOT NULL REFERENCES public.experiments(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT,
  started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  completed_at TIMESTAMP WITH TIME ZONE,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed', 'failed')),
  metadata JSONB DEFAULT '{}',
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Add indexes
CREATE INDEX idx_trials_experiment_id ON public.trials(experiment_id);
CREATE INDEX idx_trials_status ON public.trials(status);
CREATE INDEX idx_trials_started_at ON public.trials(started_at DESC);

-- Enable RLS
ALTER TABLE public.trials ENABLE ROW LEVEL SECURITY;

-- RLS policies
CREATE POLICY "Users can view own trials"
ON public.trials FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.experiments
    WHERE experiments.id = trials.experiment_id
    AND experiments.user_id = auth.uid()
  )
);

CREATE POLICY "Users can insert own trials"
ON public.trials FOR INSERT
WITH CHECK (
  EXISTS (
    SELECT 1 FROM public.experiments
    WHERE experiments.id = trials.experiment_id
    AND experiments.user_id = auth.uid()
  )
);

-- Add trigger for updated_at
CREATE TRIGGER update_trials_updated_at
BEFORE UPDATE ON public.trials
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

-- Add comments
COMMENT ON TABLE public.trials IS 'Experimental trials within experiments';
COMMENT ON COLUMN public.trials.metadata IS 'Additional trial metadata as JSON';
```

### Adding a Column

```sql
-- Migration: add_tags_to_experiments.sql

-- Add tags column (JSONB array)
ALTER TABLE public.experiments
ADD COLUMN tags JSONB DEFAULT '[]'::JSONB;

-- Add GIN index for efficient JSONB queries
CREATE INDEX idx_experiments_tags ON public.experiments USING GIN (tags);

-- Add check constraint
ALTER TABLE public.experiments
ADD CONSTRAINT tags_is_array CHECK (jsonb_typeof(tags) = 'array');

-- Add comment
COMMENT ON COLUMN public.experiments.tags IS 'Experiment tags for categorization';
```

### Modifying a Column

```sql
-- Migration: make_experiment_description_not_null.sql

-- First, set default value for NULL descriptions
UPDATE public.experiments
SET description = 'No description provided'
WHERE description IS NULL;

-- Then make column NOT NULL
ALTER TABLE public.experiments
ALTER COLUMN description SET NOT NULL;

-- Add default for future inserts
ALTER TABLE public.experiments
ALTER COLUMN description SET DEFAULT 'No description provided';
```

### Creating Indexes

```sql
-- Migration: add_performance_indexes.sql

-- Composite index for common query pattern
CREATE INDEX idx_videos_experiment_status_created
ON public.videos(experiment_id, status, created_at DESC);

-- Partial index for processing videos
CREATE INDEX idx_videos_processing
ON public.videos(created_at DESC)
WHERE status = 'processing';

-- Full-text search index
CREATE INDEX idx_experiments_search
ON public.experiments
USING GIN (to_tsvector('english', name || ' ' || COALESCE(description, '')));
```

## CI/CD Considerations

Migrations in CI:

- **Local testing**: Database reset before each test run
- **PR checks**: Verify migrations can be applied cleanly
- **Production deployment**: Apply migrations before deploying new app version

**GitHub Actions example** (planned):

```yaml
- name: Apply migrations
  run: supabase db push
  env:
    SUPABASE_DB_URL: ${{ secrets.DATABASE_URL }}
```

## Related Commands

- `/validate-env` - Verify Supabase is running and migrations applied
- `/ci-debug` - Debug migration failures in CI
- `/run-ci-locally` - Test migrations as part of CI suite

## Documentation

- **Supabase Migrations**: https://supabase.com/docs/guides/cli/local-development#database-migrations
- **PostgreSQL Docs**: https://www.postgresql.org/docs/
- **RLS Documentation**: https://supabase.com/docs/guides/auth/row-level-security
- **Bloom Database Schema**: See `supabase/migrations/` for current schema

## Tips

1. **Always test locally first**: Use `supabase db reset` to test migrations from scratch
2. **Use descriptive names**: Future you will thank present you
3. **Add comments**: Document why, not just what
4. **Version control**: Commit migrations with the code that uses them
5. **RLS by default**: Always enable RLS on new tables for security
6. **Index strategically**: Add indexes for WHERE, JOIN, and ORDER BY columns
7. **Foreign keys**: Use cascading deletes to maintain referential integrity
8. **Backup before prod**: Always backup production database before migrations
9. **Review generated SQL**: Studio-generated migrations may need tweaking
10. **Test RLS policies**: Verify policies work with different user contexts
