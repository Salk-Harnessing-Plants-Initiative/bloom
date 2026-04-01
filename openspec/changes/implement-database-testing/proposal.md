# Implement Comprehensive Database Testing Strategy

## Why

The Bloom project uses a self-hosted Supabase PostgreSQL database with 100+ migration files, complex schema including tables, views, functions, and Row Level Security (RLS) policies. Currently, there is no automated testing for the database layer, creating critical gaps:

- **No migration testing**: Schema changes are deployed without validation, risking production data corruption
- **No data integrity tests**: Foreign key constraints, check constraints, and business rules not systematically validated
- **No performance testing**: No query performance baselines or regression detection
- **No backup verification**: Database backups exist but restoration procedures are untested
- **No rollback testing**: Cannot safely revert problematic schema changes
- **No test data management**: No standardized test datasets or factories for reproducible testing
- **No RLS policy testing**: Security policies protecting sensitive data are not systematically tested

These gaps lead to:

- Production schema migration failures
- Data integrity violations that go undetected
- Performance regressions in queries
- Unverified backup/recovery procedures (disaster recovery risk)
- Security vulnerabilities in RLS policies
- Difficulty reproducing bugs due to lack of test data

## What Changes

### 1. Migration Testing Framework

- Create `supabase/tests/test_migrations.py` with pytest framework
- Automated tests for applying migrations forward and backward
- Validation that migrations are idempotent (can run multiple times safely)
- Test migration rollback procedures
- Verify data integrity before/after migrations
- Automated migration ordering validation

### 2. Data Integrity Testing

- Test all foreign key constraints (referential integrity)
- Test check constraints and business rules
- Test NOT NULL constraints on critical fields
- Test unique constraints and indexes
- Validate cascade delete behaviors
- Test trigger functions and their side effects

### 3. RLS Policy Testing

- Create test users with different roles (anon, authenticated, service_role)
- Test that RLS policies correctly restrict access
- Test that users can only see/modify their own data
- Test that admin users have appropriate permissions
- Validate that policies don't have security holes
- Test performance impact of RLS policies

### 4. Performance Testing

- Establish query performance baselines for critical queries
- Automated regression detection for slow queries
- Test index effectiveness
- Monitor query plan changes across migrations
- Test performance with realistic data volumes
- Identify N+1 query problems

### 5. Test Data Management

- Create database fixtures for common scenarios
- Use factories (faker) to generate realistic test data
- Seed databases with representative datasets
- Test data cleanup between tests
- Snapshot/restore mechanisms for fast test setup

### 6. Backup and Recovery Testing

- Automated backup verification (can restore successfully)
- Test point-in-time recovery (PITR)
- Test backup restoration to separate environment
- Validate backup data integrity
- Document and test disaster recovery procedures

### 7. Schema Validation

- Test that all tables have appropriate indexes
- Validate naming conventions (consistency)
- Test that all foreign keys have indexes
- Verify that RLS is enabled on all sensitive tables
- Check for missing constraints or validations

## Impact

- **Affected specs**: `database-testing` (new capability spec)
- **Affected code**:

  - **New files**:
    - `supabase/tests/` - Test directory structure
    - `supabase/tests/test_migrations.py` - Migration tests
    - `supabase/tests/test_data_integrity.py` - Constraint tests
    - `supabase/tests/test_rls_policies.py` - Security tests
    - `supabase/tests/test_performance.py` - Performance tests
    - `supabase/tests/test_backups.py` - Backup/recovery tests
    - `supabase/tests/fixtures/` - Test data fixtures
    - `supabase/tests/conftest.py` - Shared pytest fixtures
    - `.github/workflows/database-tests.yml` - CI workflow for database tests
  - **Modified files**:
    - `turbo.json` - Add database test tasks
    - Root `package.json` - Add database test scripts

- **Breaking changes**: None (tests are additive)

- **Dependencies**:

  - pytest (already in CI/CD proposal)
  - psycopg2 (PostgreSQL adapter for Python)
  - faker (test data generation)
  - Supabase CLI (for migrations)
  - Docker Compose (for test database)

- **Testing required**:

  - Run database tests locally before CI integration
  - Verify tests catch intentional migration errors
  - Validate RLS policy tests with different user roles
  - Confirm performance tests detect regressions

- **Benefits**:
  - Catch migration errors before production deployment
  - Prevent data integrity violations
  - Detect performance regressions early
  - Verify backup/recovery procedures work
  - Ensure RLS policies protect data correctly
  - Confidence in schema changes
  - Faster debugging with reproducible test data
