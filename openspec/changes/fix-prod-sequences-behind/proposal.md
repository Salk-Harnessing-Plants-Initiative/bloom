## Why

In prod, 21 `public` id sequences are behind their data (bloom#1022), so inserts that let the database choose the id collide with existing rows. Nothing advances them, and nothing stops an import or seed that supplies explicit ids from bringing the problem back.

## What Changes

The change lands in two PRs. design.md has the context and the reasons.

**PR 1: migration (carries this proposal)**

- A forward-only migration that runs the **sequence-advance body**. The body:
  - visits every sequence-backed column in `public`;
  - advances only the sequences that are behind (see the spec), and never moves one backwards;
  - checks and locks every behind table before advancing any of them, so a run that can't finish changes nothing.

  In prod it advances 21 sequences. On staging it does nothing. It is safe to re-apply.
- A rollback file that is a documented no-op.
- Tests, written first:
  - integration tests on scratch tables;
  - file-text unit tests.
- A short rule in `.claude/commands/database-migration.md`: an insert that supplies explicit ids must re-run the advance body.
- No schema change. The PR body says `No schema changes.`

**PR 2: guard (code only; implements §3 of this same change)**

- A read-only "sequences behind" query, `scripts/sql/sequences_behind.sql`, run in two places:
  - a deploy step on prod and staging. It runs after "Rollback on failure", so it fails the deploy but keeps the code;
  - `make check`.
- `scripts/sql/advance_behind_sequences.sql`, which holds the advance body and is pinned to the migration by a test. A new `make seed-gravi` target runs it after `scripts/seed_gravi_mock_data.sql`.
- Docs:
  - a runbook section in `_WIKI/SUPABASE/README.md`;
  - a restore note in `_WIKI/SCHEDULEDJOBS/weekly-backup.md`;
  - the `make check` descriptions.

**Out of scope, filed or asked separately**

- **`make load-test-data`.** It takes its database URL from the caller's shell and inserts explicit ids into 13 sequence-backed tables. It needs a localhost-only URL and a sequence reset. A draft issue goes to the user for approval before it is posted.
- **Run 2's 5 stale `failed` rows,** and the retry gap behind them.

## Impact

- **Affected specs:** a new capability, `database-sequence-integrity`, with all requirements ADDED. It references `deploy-health-check` and `deploy-migrations` without modifying them.
- **Affected code, PR 1:**
  - `supabase/migrations/<ts>_advance_lagging_id_sequences.sql`
  - `supabase/rollbacks/<ts>_advance_lagging_id_sequences_rollback.sql`
  - `tests/integration/sequence_fixtures.py`
  - `tests/integration/test_advance_lagging_id_sequences.py`
  - `tests/unit/test_advance_lagging_id_sequences_migration_files.py`
  - `.claude/commands/database-migration.md`
- **Affected code, PR 2:**
  - `scripts/sql/sequences_behind.sql` and `scripts/sql/advance_behind_sequences.sql`
  - `scripts/check_health.py`
  - `scripts/seed_gravi_mock_data.sql` (header only)
  - `Makefile` (`seed-gravi`, `check` help text)
  - `.github/workflows/deploy.yml` and `.github/workflows/pr-checks.yml` (a step name)
  - `README.md`, `_WIKI/SUPABASE/README.md` and `_WIKI/SCHEDULEDJOBS/weekly-backup.md`
  - Tests:
    - `tests/integration/test_sequences_behind_check.py`
    - `tests/unit/test_check_health.py`
    - `tests/unit/test_deploy_sequence_check.py`
    - `tests/unit/test_advance_body_copies.py`
- **Data:** in prod, 21 sequences are advanced. No row changes.
- **Issue:** Part of #1022. It stays open until a read-only prod check after the promotion shows 0 behind (tasks §5).
