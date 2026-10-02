## Why

In prod, 21 `public` id sequences are behind their data (bloom#1022), so inserts that let the database pick the id collide with existing rows. Prod's first pipeline write-back hit this on a 22nd table, `cyl_trait_sources`, which has since moved past its data. Nothing advances the 21, and nothing stops an import or seed that supplies explicit ids from bringing the problem back.

## What Changes

The change lands in two PRs. design.md has the context and the reasons for each decision.

**PR 1: migration (carries this proposal)**

- **A new forward-only migration, `<ts>_advance_lagging_id_sequences.sql`:**
  - It visits every sequence-backed column in `public`, found through `pg_get_serial_sequence` rather than a list.
  - It advances only the sequences that are behind, as the spec defines it. It sets each one so the next default insert gets `max + 1` and never moves a sequence backwards.
  - It reports each change with `RAISE NOTICE`.
  - It works in two passes: it finds, checks and locks every behind table, then advances. So when it can't advance a behind sequence correctly, it raises before changing anything.
  - In prod it advances 21 sequences. On staging it does nothing. It is safe to re-apply.
- **A no-op rollback:** `supabase/rollbacks/<ts>_advance_lagging_id_sequences_rollback.sql`, with a header saying why it does nothing.
- **Tests, written first:** integration tests on scratch tables (`tests/integration/`) and file-text tests (`tests/unit/`).
- **A short rule in `.claude/commands/database-migration.md`:** an insert that supplies ids to an identity or serial column must reset its sequence.
- **No schema change:** the PR body says `No schema changes.`

**PR 2: guard (code-only, against this same change)**

- `scripts/sql/sequences_behind.sql`, a single read-only query, run in two places:
  - a deploy step on prod and staging after "Rollback on failure". It fails the deploy, but keeps the new code, when any sequence is behind;
  - `check_sequences` in `scripts/check_health.py` (`make check`).
- `scripts/sql/advance_behind_sequences.sql`, a copy of the migration's body, pinned to it by a test. The dev data loaders run it after inserting explicit ids:
  - `make load-test-data`
  - `supabase/_seed.sql`
  - `scripts/seed_gravi_mock_data.sql`
- **Docs:**
  - a `_WIKI` runbook entry for a red sequence check;
  - a restore note in `_WIKI/SCHEDULEDJOBS/weekly-backup.md`;
  - `make check` descriptions in `README.md`, the Makefile and `check_health.py`.

## Impact

- **Affected specs:** a new capability, `database-sequence-integrity`, all requirements ADDED. It cross-references `deploy-health-check` and `deploy-migrations` and modifies neither.
- **Affected code:**
  - **PR 1:**
    - `supabase/migrations/<ts>_advance_lagging_id_sequences.sql`
    - `supabase/rollbacks/<ts>_advance_lagging_id_sequences_rollback.sql`
    - `tests/integration/test_advance_lagging_id_sequences.py`
    - `tests/integration/sequence_fixtures.py` (scratch fixtures shared with PR 2)
    - `tests/unit/test_advance_lagging_id_sequences_migration_files.py`
    - `.claude/commands/database-migration.md`
  - **PR 2:**
    - `scripts/sql/sequences_behind.sql`
    - `scripts/sql/advance_behind_sequences.sql`
    - `scripts/check_health.py`
    - `scripts/load_test_data.py` and/or the `Makefile` `load-test-data` target
    - `supabase/_seed.sql`
    - `scripts/seed_gravi_mock_data.sql`
    - `.github/workflows/deploy.yml`
    - `README.md`
    - `_WIKI/SCHEDULEDJOBS/weekly-backup.md`
    - a `_WIKI` deploy runbook page
    - `tests/integration/test_sequences_behind_check.py`
    - `tests/unit/test_check_health.py`
    - `tests/unit/test_deploy_sequence_check.py`
    - `tests/unit/test_seed_sequence_reset.py`
- **Data:** in prod, 21 sequences are advanced. No row changes.
- **Issue:** Part of #1022. It stays open until a read-only prod check after the promotion shows 0 behind (tasks §5).
