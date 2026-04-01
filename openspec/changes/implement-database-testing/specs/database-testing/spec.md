# Database Testing Capability Specification

## ADDED Requirements

### Requirement: Migration Testing

The project SHALL automatically test all database migrations to ensure they can be applied and rolled back safely without data loss or corruption.

#### Scenario: Migrations apply successfully

- **WHEN** all migrations are applied to a fresh database
- **THEN** all 100+ migration files execute in order without errors
- **AND** the final schema matches the expected production schema
- **AND** all tables, views, functions, and triggers are created correctly

#### Scenario: Migrations can be rolled back

- **WHEN** migrations are rolled back one by one
- **THEN** each rollback executes without errors
- **AND** the database state after rollback matches the state before that migration
- **AND** no orphaned objects or data remain

#### Scenario: Migrations are idempotent

- **WHEN** a migration is applied twice to the same database
- **THEN** the second application does not cause errors
- **AND** the database state is unchanged by the second application
- **AND** `CREATE TABLE IF NOT EXISTS` patterns are used correctly

### Requirement: Data Integrity Testing

The project SHALL test all database constraints (foreign keys, check constraints, NOT NULL, unique) to ensure data integrity rules are enforced.

#### Scenario: Foreign key constraints are enforced

- **WHEN** a test attempts to insert a record with invalid foreign key
- **THEN** the database rejects the insert with a constraint violation error
- **AND** the referential integrity is maintained
- **AND** orphaned records cannot be created

#### Scenario: Cascade deletes work correctly

- **WHEN** a parent record is deleted
- **THEN** all child records are automatically deleted (if ON DELETE CASCADE)
- **AND** no orphaned foreign key references remain
- **AND** the cascade behavior matches the schema definition

#### Scenario: Check constraints are enforced

- **WHEN** a test attempts to insert invalid data (e.g., negative height)
- **THEN** the database rejects the insert with a check constraint violation
- **AND** only valid data can be inserted

### Requirement: RLS Policy Testing

The project SHALL test Row Level Security policies to ensure users can only access data they are authorized to see and modify.

#### Scenario: Anonymous users cannot access protected data

- **WHEN** an anonymous user (no JWT token) queries a protected table
- **THEN** the query returns zero rows
- **AND** no sensitive data is leaked
- **AND** the RLS policy blocks unauthorized access

#### Scenario: Authenticated users see only their own data

- **WHEN** an authenticated user queries a table with user-scoped RLS
- **THEN** only rows belonging to that user are returned
- **AND** other users' data is not visible
- **AND** the user cannot UPDATE or DELETE other users' rows

#### Scenario: Service role bypasses RLS for admin operations

- **WHEN** a request uses the service_role key
- **THEN** RLS policies are bypassed
- **AND** all rows in the table are accessible
- **AND** admin operations can modify any data

#### Scenario: RLS is enabled on all sensitive tables

- **WHEN** tests check all tables for RLS configuration
- **THEN** all tables containing user data have RLS enabled
- **AND** policies are defined for SELECT, INSERT, UPDATE, DELETE
- **AND** no sensitive tables are unprotected

### Requirement: Performance Testing and Baseline Tracking

The project SHALL establish performance baselines for critical queries and detect regressions when query performance degrades significantly.

#### Scenario: Critical query performance is baselined

- **WHEN** a test runs a critical query (e.g., get_scan_traits function)
- **THEN** the query execution time is measured and recorded
- **AND** the baseline is stored for future comparison
- **AND** the query completes within acceptable time (<1 second for typical cases)

#### Scenario: Query performance regression is detected

- **WHEN** a test runs after a schema change
- **THEN** query performance is compared to the baseline
- **AND** if query time exceeds 2x baseline, the test fails
- **AND** the regression is reported with query plan analysis

#### Scenario: Indexes are used effectively

- **WHEN** tests analyze query plans for critical queries
- **THEN** appropriate indexes are used (not sequential scans on large tables)
- **AND** join strategies are efficient
- **AND** missing indexes are reported

### Requirement: Backup and Recovery Validation

The project SHALL regularly test database backup and restore procedures to ensure disaster recovery capability.

#### Scenario: Database backup can be created

- **WHEN** a test creates a database backup using pg_dump
- **THEN** the backup file is created successfully
- **AND** the backup includes all schema and data
- **AND** the backup file is not corrupted

#### Scenario: Database can be restored from backup

- **WHEN** a backup is restored to a separate test database
- **THEN** the restoration completes without errors
- **AND** all tables, views, functions, and data are present
- **AND** the restored database is functionally identical to the original

#### Scenario: Restored data integrity is validated

- **WHEN** data is validated after restoration
- **THEN** row counts match the original database
- **AND** sample data queries return identical results
- **AND** all constraints and indexes are intact

### Requirement: Test Data Management

The project SHALL provide factories and fixtures for generating realistic test data to support reproducible database testing.

#### Scenario: Test factories generate realistic data

- **WHEN** a test uses a factory to create a test plant record
- **THEN** the factory generates a valid plant with realistic attributes
- **AND** all required foreign keys are satisfied
- **AND** the data passes all constraints

#### Scenario: Test database is seeded with sample data

- **WHEN** tests need a pre-populated database
- **THEN** fixtures seed the database with representative data (species, experiments, plants, scans)
- **AND** the seeded data includes realistic relationships
- **AND** the data can be used to test complex queries

#### Scenario: Test data is cleaned up between tests

- **WHEN** a test completes
- **THEN** all test data is removed from the database
- **AND** the database is returned to a clean state
- **AND** subsequent tests start with a known state

### Requirement: Schema Validation

The project SHALL validate the database schema structure to ensure best practices are followed (indexes, naming, RLS).

#### Scenario: All foreign keys have indexes

- **WHEN** tests check foreign key columns
- **THEN** every foreign key column has an index (for join performance)
- **AND** missing indexes are reported

#### Scenario: Naming conventions are consistent

- **WHEN** tests check table and column names
- **THEN** naming follows snake_case convention
- **AND** table names are plural
- **AND** foreign keys follow the pattern `{table}_id`

#### Scenario: Primary keys exist on all tables

- **WHEN** tests check all tables
- **THEN** every table has a primary key defined
- **AND** primary key columns are indexed
- **AND** no tables are missing primary keys
