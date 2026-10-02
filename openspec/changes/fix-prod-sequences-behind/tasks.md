Part of #1022. It stays open until §5. No commit message, PR title or PR body may put a closing keyword next to `#1022`. Squash commit bodies reach `main`, where GitHub acts on them.

**Landing plan:**

- **PR 1** contains this proposal plus §1–§2, the migration. It touches only migrations, rollbacks, `tests/`, and Markdown.
- **PR 2** is §3, the guard. It is code-only, branched from `staging` after PR 1 merges.
- **§4–§5** are operator checks after the deploys.

**TDD:** every implementation task comes after its tests have been run red.

## 1. Migration tests first (PR 1)

Integration tests live in `tests/integration/test_advance_lagging_id_sequences.py`, with scratch fixtures in `tests/integration/sequence_fixtures.py` (design D7). Each test:

- runs in one open transaction, using savepoints only;
- builds `public._seq1022_<uuid>_*` scratch tables;
- runs the body from `sql_body()` under `SET LOCAL ROLE postgres`;
- captures notices with `add_notice_handler`;
- snapshots every real sequence's `(last_value, is_called)` before and after, and asserts every non-behind real sequence is unchanged;
- calls `pg_conn.rollback()` at teardown.

**Warning:** on dev, the first green run advances the real `cyl_scanners`, because `setval` survives rollback. That is why §2.2 runs before §2.3.

- [ ] 1.1 Fixture helpers in `sequence_fixtures.py`:
  - make identity, serial or bigint scratch tables with a quoted mixed-case name and a column that may be non-`id`;
  - insert explicit ids (`OVERRIDING SYSTEM VALUE` for identity columns);
  - call `setval(seq, v, called)`;
  - snapshot every `public` sequence;
  - parse NOTICEs.
- [ ] 1.2 T1, fully behind identity (ids 1–5, sequence at 1, not called):
  - the next default id is 6;
  - the NOTICE matches the D5 format with old 1, new 6 and max 5.
- [ ] 1.3 T2, partly behind serial (ids 1–10, `setval(4, true)`): next id 11, and the NOTICE says old 5.
- [ ] 1.4 T3, equal and not called (`max` 3, `setval(3, false)`): next id 4.
- [ ] 1.5 T4, equal and called (`max` 3, `setval(3, true)`): unchanged, and no NOTICE names it.
- [ ] 1.6 T5, ahead: rows 1–3 with `setval(50, true)`, and another with `setval(50, false)`. Both are unchanged, including `is_called`.
- [ ] 1.7 T6, empty table: unchanged, and the first default insert gets 1.
- [ ] 1.8 T7, increment: rows 1–25, `INCREMENT BY 10`, `setval(20, true)`. The sequence is unchanged.
- [ ] 1.9 T8, large ids and quoting: a mixed-case bigint table with column `"Id"` holding 3000000000 gives next 3000000001. A table with two sequence columns has both columns evaluated independently.
- [ ] 1.10 T9, partitions: a behind partitioned parent with two partitions gets exactly one NOTICE naming the parent, and none naming a partition.
- [ ] 1.11 T10, schema scope: a behind table in a throwaway schema created inside the transaction is unchanged and not counted in the summary's `m`.
- [ ] 1.12 T11, locks: after the body runs, `pg_locks` for this backend holds `ShareRowExclusiveLock` on exactly the behind scratch tables (plus `cyl_scanners` on a first dev run), and never on the T4–T6 tables.
- [ ] 1.13 T12, re-apply:
  - apply the body twice;
  - the second run has no `advanced` NOTICE and its summary says 0 advanced;
  - every scratch and real sequence is identical between the two runs.
- [ ] 1.14 T13, can't advance means fail, and nothing changes:
  - **Setup, as `supabase_admin`:** an advanceable behind table A that sorts first; a behind table B with `REVOKE UPDATE ON SEQUENCE … FROM postgres`; and the precondition `NOT has_sequence_privilege('postgres', B_seq, 'UPDATE')`.
  - **Failure case:** inside a savepoint, run `SET LOCAL ROLE postgres` and the body. It raises with B's sequence and `supabase_admin` in the message, and A's sequence is unchanged.
  - **Non-behind case:** after `ROLLBACK TO SAVEPOINT`, run `SET LOCAL ROLE postgres` again. A table C that is not behind, with the same revoke, runs cleanly.
- [ ] 1.15 T14, non-positive increment: a behind table with `INCREMENT BY -1` raises, naming the sequence and the increment. A not-behind one with the same increment is ignored.
- [ ] 1.16 T15, seqmax: rows 1–10 with `ALTER SEQUENCE … MAXVALUE 5` raises, naming the sequence, before any `setval`. Pair it with an advanceable behind table that sorts first, and assert that table is unchanged.
- [ ] 1.17 T16, migrated DB is clean: with no scratch tables, the "behind" predicate over the real `public` schema returns no rows.
  - On CI this holds after `db push`.
  - On dev it holds after §2.2.
- [ ] 1.18 Unit tests in `tests/unit/test_advance_lagging_id_sequences_migration_files.py`. They need no DB and follow `test_cyl_noop_redelivery_migration_files.py`. Each pair of files exists exactly once. The rollback contains no `setval`, `nextval` or `ALTER SEQUENCE` outside comments, has a header explaining the no-op, and keeps the STAGING HOT-APPLY ONLY / `migration repair` convention. The migration:
  - has top-level `BEGIN`, `SET LOCAL lock_timeout = '5s'` and `COMMIT`;
  - has its `LOCK TABLE` before the first `setval`;
  - contains no `ALTER TABLE`, `CREATE` or `DROP` outside strings or comments (so `No schema changes.` holds).
- [ ] 1.19 Red phase, part one: run §1 and confirm it fails because no file matches `*_advance_lagging_id_sequences.sql`.
- [ ] 1.20 Red phase, part two: in a throwaway local migration file (never committed), implement the rejected `GREATEST` sketch from design D1. Confirm T4, T5 (the not-called case), T6, T7 and T12 fail for the right reason, then delete the file.
- [ ] 1.21 Commit the tests locally. Do not push red.

## 2. Migration (PR 1)

- [ ] 2.1 Run `make new-migration name=advance_lagging_id_sequences`, which gives a current timestamp later than the newest on `origin/staging`. Write the file following design D1–D5, in steps:
  - (a) the predicate and loop, making T1–T10 and T16 green;
  - (b) pass 1 checks and the raise-before-`setval` passes, making T13–T15 green;
  - (c) the single `LOCK TABLE` and the re-check, making T11 green;
  - (d) the NOTICE and summary format, making T1, T2 and T12 green.

  Then write the rollback (D6), making 1.18 green.
- [ ] 2.2 **Before any green test run on dev**, run `wsl.exe -d Ubuntu -e sh -c 'PATH=$HOME/.local/bin:$PATH make migrate-local'` (retry if WSL start-up times out). Record:
  - whether `db push` prints the NOTICEs. If it doesn't, change §4.2 and §5.2 to rely on the read-only checks only;
  - the summary line, which should show `cyl_scanners` advanced and `1 of <m>`;
  - a re-run of the predicate, which should show 0 behind.
- [ ] 2.3 Run `uv run --extra test pytest tests/integration/test_advance_lagging_id_sequences.py tests/unit/test_advance_lagging_id_sequences_migration_files.py -v` until it is green, then the full `tests/integration/` and `tests/unit/` suites.
- [ ] 2.4 Add the rule to `.claude/commands/database-migration.md`: an `INSERT` that supplies ids to an identity or serial column doesn't move its sequence, so reset it in the same migration with `setval(pg_get_serial_sequence('public.<t>','<col>'), max(<col>))`.
- [ ] 2.5 Replace `<ts>` in `proposal.md` with the real timestamp.
- [ ] 2.6 Run the following and confirm they all pass, with no diff from the first two:
  - `make gen-types`
  - `make erd`
  - `./scripts/lint_migrations.sh origin/staging`
  - `python3 scripts/lint_migration_isolation.py origin/staging`
  - `make pr-body-check BODY=<file>` with `No schema changes.`
- [ ] 2.7 Run `/pre-merge`. Commit, then push once everything is green. Open the PR to `staging` with title `Advance prod's lagging id sequences with a forward-only migration (Part of #1022)`.
- [ ] 2.8 Before asking for the merge, re-check that `git ls-tree --name-only origin/staging supabase/migrations/ | tail -1` is older than ours. If it isn't, `git mv` the migration and rollback to a new timestamp and update the rollback header's `<ts>`.

## 3. Guard (PR 2, code-only)

- [ ] 3.1 Tests first, in `tests/integration/test_sequences_behind_check.py`. Using the `sequence_fixtures` helpers:
  - the guard's rows equal the set the migration body's NOTICEs advance;
  - after the body, the guard returns no scratch rows;
  - the guard runs with `default_transaction_read_only=on` without error.
- [ ] 3.2 Tests first, unit:
  - `sequence_problems(rows)` gives one problem per row, naming table, column, `max` and next value, and none for no rows;
  - `main()` includes the check, and its exit code is 1 when there are problems;
  - `scripts/sql/sequences_behind.sql` is a single `SELECT` containing no `CREATE`, `DO`, `FUNCTION`, DML or `setval`;
  - `check_health.py` and `deploy.yml` both reference that file and don't inline the query.
- [ ] 3.3 Tests first, in `tests/unit/test_seed_sequence_reset.py`:
  - `scripts/sql/advance_behind_sequences.sql` is identical to the migration's `DO` block;
  - each loader (`make load-test-data`, `supabase/_seed.sql`, `scripts/seed_gravi_mock_data.sql`) runs or includes it after its last explicit-id insert.
- [ ] 3.4 Tests first, in `tests/unit/test_deploy_sequence_check.py`. For each of the prod and staging jobs:
  - the seq-check step comes after "Rollback on failure" and has no `if:`;
  - it uses `-U supabase_admin`, `ON_ERROR_STOP=1` and `default_transaction_read_only=on`, and reads the SQL file on stdin;
  - it has both `::error` titles;
  - Rollback's `if:` is unchanged. `test_deploy_staging_supersede_and_pin.py` must stay green.
- [ ] 3.5 Implement `scripts/sql/sequences_behind.sql` (design D8). It reads `last_value` and `is_called` from the sequence relation through `query_to_xml`, not from `pg_sequences`.
- [ ] 3.6 Implement `sequence_problems` and `check_sequences` in `scripts/check_health.py`, and update its docstring.
- [ ] 3.7 Add `scripts/sql/advance_behind_sequences.sql` and wire it into the three loaders. `make load-test-data` runs it through `compose exec … psql`.
- [ ] 3.8 Add the `deploy.yml` steps for prod and staging, using each environment's grants-step compose command.
- [ ] 3.9 Docs:
  - `make check` descriptions in `README.md`, the Makefile and the `pr-checks.yml` step name;
  - a `_WIKI` runbook entry, "Id sequences behind their data": what the annotation means, that the fix is a new migration re-running the advance body and never a hand `setval` on prod, and to check deploy-gated issues by hand;
  - in `_WIKI/SCHEDULEDJOBS/weekly-backup.md`: after a restore, run `scripts/sql/sequences_behind.sql` and expect 0 rows. Dumps from before PR 1's prod deploy carry 21 behind sequences.
- [ ] 3.10 Manual check: on dev, run `make load-test-data && make check`, and record that it reports no behind sequences.
- [ ] 3.11 Run `/pre-merge`, including `pre-commit run --all-files` and the full unit suite. Open PR 2 to `staging` with title `Fail deploy and make check when an id sequence is behind its data (Part of #1022)`.

## 4. Staging verification (after PR 1's staging deploy)

- [ ] 4.1 Before: on 2026-10-02, staging had 0 of 68 behind (issue #1022).
- [ ] 4.2 The staging deploy log shows the migration applied, with summary `0 of <m> advanced`. Record `m`.
- [ ] 4.3 Run the read-only predicate against `bloom_v2_staging-db-prod-1` over ssh, with the SQL on stdin and no `$$` in a double-quoted remote command. Expect 0 behind, and record the result here.

## 5. Prod verification (after the curated staging→main promotion; each prod read needs the user's OK)

- [ ] 5.1 Before: on 2026-10-02, 21 behind (issue #1022's table) out of 65. See design.md Context for the ownership and privilege facts.
- [ ] 5.2 The prod deploy log shows 21 `advanced …` NOTICEs and the summary `21 of <m>`. If the NOTICEs aren't printed (see 2.2), rely on 5.3.
- [ ] 5.3 Run the read-only predicate against `bloom_v2_prod-db-prod-1`. Expect 0 behind, and record the result here. No hand `setval` or other write on prod.
- [ ] 5.4 With the user's approval, close issue 1022 by hand, linking the 5.3 evidence.
- [ ] 5.5 Ask the user whether to add a line to `talmolab/sleap-roots-pipeline` `docs/bloom-integration/roadmap.md`.
- [ ] 5.6 Ask the user about filing the "write-back retry can't update a row closed as `failed`" issue, and about correcting run 2's 5 rows. Both are out of scope here.
- [ ] 5.7 After PR 2's deploy step has run green on staging and on prod: `/openspec:archive fix-prod-sequences-behind`.
