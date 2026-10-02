Part of #1022. There are no closing keywords anywhere, because CI's database is empty and cannot prove the prod fix (the bloom#780 pattern). #1022 is closed by hand after §5, with the user's approval.

**Landing plan:**

- **PR 1:** this proposal plus §1–§2, the migration. It changes only migrations, rollbacks, `tests/integration` and Markdown, per `.claude/commands/database-migration.md` "Migration PRs".
- **PR 2:** §3, the guard. It is code-only against this change.
- §4–§5 are operator steps after deploys.

**TDD:** every implementation task is preceded by its tests, run red, then made green.

## 1. Migration tests first (PR 1, `tests/integration/test_advance_lagging_id_sequences.py`)

These are written before the migration exists and run red; they fail at "no file matches `*_advance_lagging_id_sequences.sql`". Fixtures follow `test_cyl_noop_redelivery_scan.py`:

- Use `pg_conn` with one open transaction, savepoints only, and `pg_conn.rollback()` at teardown.
- `SET LOCAL ROLE postgres` mirrors `db push`.
- Scratch tables are `public._seq1022_*`, created inside the transaction so their sequences are dropped on rollback (design D7).
- The body comes from `sql_body()`, and notices are captured with `add_notice_handler`.

Tests:

- [ ] 1.1 **T1, fully behind identity:** ids 1–5 inserted `OVERRIDING SYSTEM VALUE`, sequence at 1 and not called. After the body runs, a default insert returns 6, and a NOTICE names the table.
- [ ] 1.2 **T2, partly behind serial:** ids 1–10, `setval(seq, 4, true)`. After the body runs, the next default id is 11.
- [ ] 1.3 **T3, equal and not called:** `max` 3, `setval(seq, 3, false)`. Advanced; the next id is 4.
- [ ] 1.4 **T4, equal and called:** `max` 3, `setval(seq, 3, true)`. `last_value` and `is_called` are unchanged, and no NOTICE names the table.
- [ ] 1.5 **T5, ahead:** rows 1–3 with `setval(seq, 50, true)`, and a second table with `setval(seq, 50, false)`. Both are unchanged, including `is_called`.
- [ ] 1.6 **T6, empty:** the table is unchanged, and the first default insert returns 1.
- [ ] 1.7 **T7, re-apply:** apply the body twice. The second run emits no `advanced` NOTICE, its summary reports 0 advanced, and every scratch sequence's `(last_value, is_called)` is identical between runs.
- [ ] 1.8 **T8, can't advance means fail:**
  - A behind scratch table created as `supabase_admin` (no `SET ROLE`), with the body then run as `postgres` inside a savepoint, raises an exception naming the sequence and `supabase_admin`.
  - Another scratch table that is **not** behind, owned the same way, runs cleanly.
- [ ] 1.9 **T9, migrated DB is clean:** with no scratch tables, the "behind" predicate over real `public` returns no rows after `db push`. On CI this proves no migration leaves a sequence behind. On dev it holds once the migration has advanced `cyl_scanners`.
- [ ] 1.10 **T10, generic:** the migration file contains no table name from #1022's list, and T1–T8 pass on tables invented by the test.
- [ ] 1.11 **T11, rollback:**
  - The rollback file exists as `*_advance_lagging_id_sequences_rollback.sql`.
  - It contains no `setval`, `ALTER SEQUENCE` or `nextval` outside comments.
  - Its header says why it is a no-op and carries the STAGING HOT-APPLY ONLY / `migration repair` convention.
- [ ] 1.12 **T12, lock timeout:** the body sets a `lock_timeout`. Assert `current_setting('lock_timeout')` inside the transaction after the body runs, or check the file text.
- [ ] 1.13 Run the file and confirm it is red, then commit the tests.

## 2. Migration (PR 1)

- [ ] 2.1 Create the migration with `make new-migration name=advance_lagging_id_sequences`, using a current timestamp later than the newest on `origin/staging`. It is one `DO` block, as design D1–D5 describes:
  - `SET LOCAL lock_timeout = '10s'`;
  - generic loop;
  - `CASE` in parentheses inside `IF`, because PL/pgSQL ends an `IF` condition at the first `THEN`;
  - lock and re-check;
  - exceptions for privilege, increment and seqmax;
  - per-sequence NOTICE and summary NOTICE.
- [ ] 2.2 Write the rollback `supabase/rollbacks/<ts>_advance_lagging_id_sequences_rollback.sql` (D6).
- [ ] 2.3 Make §1 green. Run the full `tests/integration/` suite locally.
- [ ] 2.4 Run `make migrate-local` in WSL against dev. Record the NOTICE output: `cyl_scanners` advanced, and 1 of 68 in the summary. Re-run the predicate and get 0 behind.
- [ ] 2.5 Run `make gen-types` and `make erd`, and confirm no diff. If `make erd` refuses because of dev/checkout skew, use CI's artifact.
- [ ] 2.6 PR body: `No schema changes.` checked with `make pr-body-check BODY=…`. Say "Part of #1022" and list the follow-ups.
- [ ] 2.7 Run `/pre-merge` and open the PR to `staging`. Never self-merge.

## 3. Guard (PR 2, code-only)

- [ ] 3.1 **Tests first, `tests/integration/test_sequences_behind_check.py`:** with the same scratch fixtures as §1, `scripts/sql/sequences_behind.sql` returns exactly the T1–T3 fixtures (and T8's), never T4–T6. It runs inside `BEGIN TRANSACTION READ ONLY` without error.
- [ ] 3.2 **Tests first, unit tests for `check_sequences`:** rows map to one problem each, naming the table; no rows means no problems.
- [ ] 3.3 Add `scripts/sql/sequences_behind.sql`: one plain `SELECT`, using `query_to_xml` for per-table `max`, with no function and no `DO` (design D8).
- [ ] 3.4 Add `check_sequences(conn)` to `scripts/check_health.py` and wire it into `main`.
- [ ] 3.5 **Seed fixes, test first** (`make check` after seeding, or a test that applies the seed in a transaction and runs the query): `supabase/_seed.sql` and `scripts/seed_gravi_mock_data.sql` reset each sequence they bypass.
- [ ] 3.6 Add `deploy.yml` steps "Check id sequences are not behind (production|staging)" after the grants steps, with ids `seq_check_prod` / `seq_check_staging`. They use the read-only transaction and an `::error` annotation listing the rows. The Rollback step's condition gains `&& steps.seq_check_<env>.outcome != 'failure'`.
  - The tests pin the step order, the read-only transaction and the rollback condition. Use the existing `tests/unit` deploy.yml-parsing pattern.
- [ ] 3.7 Run `/pre-merge`, then open PR 2 to `staging` ("Part of #1022").

## 4. Staging verification (after PR 1's staging deploy)

- [ ] 4.1 **Before:** 2026-10-02, issue #1022 says staging has 0 of 68 behind.
- [ ] 4.2 Check that the staging deploy log shows the migration applied with summary `0 of 68 advanced`.
- [ ] 4.3 Re-run the read-only predicate on `bloom_v2_staging-db-prod-1`, with SQL on stdin and no `$$` in a double-quoted remote command. Expect 0 behind, and record it here.

## 5. Prod verification (after the staging→main promotion; each prod read needs the user's OK)

- [ ] 5.1 **Before:** 2026-10-02, 20 behind (issue #1022's table minus `cyl_trait_sources`). That day's read-only check found 65 sequences, all owned by and advanceable by `postgres`, all with increment 1.
- [ ] 5.2 Check that the prod deploy log shows the 20 `advanced …` notices, and record the summary line.
- [ ] 5.3 Re-run the read-only predicate on `bloom_v2_prod-db-prod-1`. Expect 0 behind, and record it here. **No hand-run `setval` or other write against prod.**
- [ ] 5.4 With the user's approval, close #1022 by hand, linking the §5.3 evidence.
- [ ] 5.5 Ask the user whether to add a line to `talmolab/sleap-roots-pipeline` `docs/bloom-integration/roadmap.md`.
- [ ] 5.6 Ask the user about filing the separate issue for "write-back retry can't update a row closed as `failed`", and about correcting run 2's 5 rows. Both are out of scope here.
- [ ] 5.7 After PR 2 deploys: `/openspec:archive fix-prod-sequences-behind`.
