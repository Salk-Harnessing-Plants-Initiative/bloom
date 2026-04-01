# Design: Database Testing Strategy

## Context

Bloom uses a self-hosted Supabase PostgreSQL database with:

- 100+ migration files managing schema evolution
- Complex schema with tables, views, functions, triggers
- Row Level Security (RLS) policies protecting sensitive data
- Storage buckets for images and files
- Critical scientific data (species, experiments, scans)

Database reliability is critical for research data integrity.

## Goals / Non-Goals

**Goals:**

- Prevent migration failures in production
- Ensure data integrity constraints are respected
- Verify RLS policies protect data correctly
- Establish performance baselines for critical queries
- Validate backup/recovery procedures work
- Provide reproducible test data for debugging

**Non-Goals:**

- Testing application business logic (covered by API tests)
- Production database monitoring (separate observability concern)
- Query optimization tuning (this is validation, not optimization)

## Decisions

### Decision 1: Use pytest for Database Tests

**What:** Use pytest framework for all database tests

**Why:**

- Consistent with backend testing strategy (from CI/CD proposal)
- Excellent fixture support for database setup/teardown
- Can integrate with existing CI pipeline
- Good support for parameterized tests (test multiple scenarios)

### Decision 2: Test Against Local Supabase Instance

**What:** Run tests against local Docker Compose Supabase stack

**Why:**

- Isolated from production data
- Fast test execution (no network latency)
- Can reset database state between tests
- Matches development environment
- No cost for test database

**Test database setup:**

```bash
docker compose -f docker-compose.dev.yml up -d db-dev
# Run migrations
# Run tests
# Tear down
```

### Decision 3: Test Each RLS Policy with Different Roles

**What:** Create test users for anon, authenticated, and service_role

**Why:**

- RLS policies behave differently based on JWT claims
- Must verify anon users cannot access protected data
- Must verify authenticated users see only their own data
- Must verify service_role bypasses RLS for admin operations

### Decision 4: Establish Performance Baselines, Don't Optimize

**What:** Measure and track query performance, fail if regressions detected

**Why:**

- Performance testing is about detecting regressions, not optimization
- Baselines provide early warning of slow queries
- Can catch missing indexes or inefficient query plans
- Focus on critical queries (top 10 by frequency/importance)

**Threshold approach:**

- Measure baseline on representative data (e.g., 1000 scans)
- Allow 10% variance for normal fluctuation
- Fail test if query takes >2x baseline (clear regression)

### Decision 5: Use Factories for Test Data, Not SQL Dumps

**What:** Create Python factory functions to generate test data

**Why:**

- More flexible than static SQL dumps
- Can generate random/realistic data with faker
- Easier to maintain (no brittle SQL scripts)
- Can vary test scenarios (small/large datasets)
- Self-documenting (code shows what data looks like)

**Example:**

```python
def create_test_plant(species_id: int, experiment_id: int) -> dict:
    return {
        "species_id": species_id,
        "experiment_id": experiment_id,
        "barcode": f"TEST{random.randint(1000, 9999)}",
        "planted_date": datetime.now(),
    }
```

## Implementation Approach

See [tasks.md](./tasks.md) for detailed implementation tasks.

**Key components:**

1. **Migration tests**: Apply/rollback migrations, verify idempotency
2. **Integrity tests**: Test constraints, cascades, triggers
3. **RLS tests**: Test policies with different user roles
4. **Performance tests**: Baseline critical queries, detect regressions
5. **Backup tests**: Verify pg_dump/restore procedures
6. **Schema tests**: Validate indexes, naming, RLS enabled

## Risks / Trade-offs

### Risk: Test Database State Management

**Risk:** Tests may interfere with each other if database state not properly isolated

**Mitigation:**

- Use pytest fixtures for database setup/teardown
- Transaction rollback after each test
- Separate test database from development database
- Clear documentation on test isolation patterns

### Risk: Slow Test Execution

**Risk:** Database tests may be slow, especially with large datasets

**Mitigation:**

- Run in parallel where possible (separate test databases)
- Use factories to generate minimal necessary data
- Optimize test database (smaller datasets for unit tests)
- Run full-scale performance tests separately (not on every commit)

### Risk: Flaky RLS Tests

**Risk:** RLS tests depend on JWT token generation, may be flaky

**Mitigation:**

- Use Supabase service role key for deterministic auth
- Create test users with known IDs
- Clear test user data between tests
- Document RLS testing patterns

### Trade-off: Test Coverage vs Test Maintenance

**Trade-off:** Comprehensive database tests require ongoing maintenance as schema evolves

**Approach:**

- Focus on high-value tests (critical queries, sensitive RLS policies)
- Accept some duplication for clarity (separate test per policy)
- Use factories to reduce brittle test data
- Update tests as part of migration PR (before merging)

## Success Metrics

- Migration tests catch 100% of migration syntax errors
- RLS tests verify all sensitive tables have policies
- Performance tests detect query regressions before production
- Backup tests verify recovery procedures work
- Zero production database incidents due to untested migrations
