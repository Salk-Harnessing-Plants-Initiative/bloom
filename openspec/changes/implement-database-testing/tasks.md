# Implementation Tasks

## 1. Setup Test Infrastructure

- [ ] 1.1 Create `supabase/tests/` directory structure
- [ ] 1.2 Create `supabase/tests/conftest.py` with shared pytest fixtures
- [ ] 1.3 Add pytest and psycopg2 to project dependencies
- [ ] 1.4 Add faker for test data generation
- [ ] 1.5 Configure pytest for database tests in pyproject.toml or setup.cfg
- [ ] 1.6 Document how to run database tests locally

## 2. Migration Testing

- [ ] 2.1 Create `supabase/tests/test_migrations.py`
- [ ] 2.2 Write test for applying all migrations in order
- [ ] 2.3 Write test for rolling back migrations
- [ ] 2.4 Write test for migration idempotency (can run twice safely)
- [ ] 2.5 Write test to detect migration ordering issues
- [ ] 2.6 Write test to validate migration file naming conventions
- [ ] 2.7 Run migration tests locally and verify they pass

## 3. Data Integrity Testing

- [ ] 3.1 Create `supabase/tests/test_data_integrity.py`
- [ ] 3.2 Write tests for foreign key constraints (core tables)
- [ ] 3.3 Write tests for check constraints
- [ ] 3.4 Write tests for NOT NULL constraints on critical fields
- [ ] 3.5 Write tests for unique constraints
- [ ] 3.6 Write tests for cascade delete behaviors
- [ ] 3.7 Write tests for trigger functions
- [ ] 3.8 Test that orphaned records cannot be created

## 4. RLS Policy Testing

- [ ] 4.1 Create `supabase/tests/test_rls_policies.py`
- [ ] 4.2 Create test fixtures for different user roles (anon, authenticated, admin)
- [ ] 4.3 Write tests for SELECT policies (users see only allowed data)
- [ ] 4.4 Write tests for INSERT policies (users can only insert valid data)
- [ ] 4.5 Write tests for UPDATE policies (users can only update own data)
- [ ] 4.6 Write tests for DELETE policies (users can only delete own data)
- [ ] 4.7 Write tests for service_role bypass (admin access)
- [ ] 4.8 Test that RLS is enabled on all sensitive tables
- [ ] 4.9 Test cross-table RLS policies (joins with RLS)

## 5. Performance Testing

- [ ] 5.1 Create `supabase/tests/test_performance.py`
- [ ] 5.2 Identify top 10 critical queries to baseline
- [ ] 5.3 Write tests that establish performance baselines
- [ ] 5.4 Write tests that detect query plan regressions
- [ ] 5.5 Write tests for index effectiveness (ensure indexes are used)
- [ ] 5.6 Write tests with realistic data volumes (1000s of rows)
- [ ] 5.7 Test N+1 query detection
- [ ] 5.8 Test that all foreign keys have indexes
- [ ] 5.9 Document acceptable performance thresholds

## 6. Test Data Management

- [ ] 6.1 Create `supabase/tests/fixtures/` directory
- [ ] 6.2 Create factory functions for core entities (species, experiments, plants, scans)
- [ ] 6.3 Create sample datasets for common scenarios
- [ ] 6.4 Write fixture for clean database state
- [ ] 6.5 Write fixture for seeded database with realistic data
- [ ] 6.6 Implement test data cleanup between tests
- [ ] 6.7 Create snapshot/restore utilities for fast test setup
- [ ] 6.8 Document test data patterns and usage

## 7. Backup and Recovery Testing

- [ ] 7.1 Create `supabase/tests/test_backups.py`
- [ ] 7.2 Write test to create database backup (pg_dump)
- [ ] 7.3 Write test to restore backup to separate database
- [ ] 7.4 Write test to validate restored data integrity
- [ ] 7.5 Write test for point-in-time recovery (if supported)
- [ ] 7.6 Document backup and recovery procedures
- [ ] 7.7 Create automated backup verification job
- [ ] 7.8 Test disaster recovery scenario end-to-end

## 8. Schema Validation Testing

- [ ] 8.1 Create `supabase/tests/test_schema.py`
- [ ] 8.2 Write test to validate all tables have primary keys
- [ ] 8.3 Write test to validate foreign keys have indexes
- [ ] 8.4 Write test to validate naming conventions
- [ ] 8.5 Write test to verify RLS is enabled on sensitive tables
- [ ] 8.6 Write test to check for missing constraints
- [ ] 8.7 Write test to validate view definitions
- [ ] 8.8 Write test to validate function signatures

## 9. CI Integration

- [ ] 9.1 Create `.github/workflows/database-tests.yml`
- [ ] 9.2 Configure workflow to start test PostgreSQL container
- [ ] 9.3 Configure workflow to apply migrations to test database
- [ ] 9.4 Configure workflow to run all database tests
- [ ] 9.5 Configure workflow to upload test results
- [ ] 9.6 Add database test job to main CI workflow
- [ ] 9.7 Make PR checks require database tests to pass
- [ ] 9.8 Test CI workflow with intentional failures

## 10. Documentation

- [ ] 10.1 Document how to run database tests locally
- [ ] 10.2 Document how to write new migration tests
- [ ] 10.3 Document how to write RLS policy tests
- [ ] 10.4 Document test data factory usage
- [ ] 10.5 Document backup/recovery procedures
- [ ] 10.6 Create troubleshooting guide for common test failures
- [ ] 10.7 Add database testing section to CONTRIBUTING.md

## Success Criteria

- [ ] All 100+ migrations can be applied and rolled back without errors
- [ ] All RLS policies are tested and verified secure
- [ ] Performance baselines established for top 10 queries
- [ ] Backup and restore procedure verified and documented
- [ ] Database tests run in CI on every PR
- [ ] Test coverage for critical database operations ≥70%
