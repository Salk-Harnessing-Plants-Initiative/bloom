Part of #1022. The issue stays open until §5.

No commit message, PR title or PR body may put a closing keyword next to the issue number. Squash commit bodies reach `main`.

**Landing plan:**

- **PR 1:** this proposal plus §0–§2. It touches only migrations, rollbacks, `tests/` and Markdown.
- **PR 2:** §3, code only, branched from `staging` after PR 1 merges.
- **§4–§5:** operator checks.

**TDD:** tests run red before each implementation step.

**Dev-safety rule:** until §2.2 has run, nothing executes the real advance body on dev. Before §2.2, the only body that may run on dev is the red-phase sketch, and it is restricted to `_seq1022_%` tables (design D8).

## 0. Setup

- [x] 0.1 Copy `.env.dev` from the main checkout into the worktree. It is gitignored, and without it `pg_conn` can't connect.
- [x] 0.2 Rebase onto the latest `origin/staging` after `git fetch origin staging`.

## 1. Tests first (PR 1)

Scratch fixtures go in `tests/integration/sequence_fixtures.py`, and the integration tests in `tests/integration/test_advance_lagging_id_sequences.py` (design D8). Each test:

- runs in one open transaction, using savepoints only;
- builds `public._seq1022_<uuid>_*` tables with no foreign keys;
- loads the body from the migration with `sql_body()` (from `cyl_recipe_helpers.py`), or the D8 sketch when `SEQ1022_RED_SKETCH=1`;
- runs it under `SET LOCAL ROLE postgres`;
- captures NOTICEs;
- snapshots every real sequence before and after, and asserts no real sequence decreased and only NOTICE-named ones changed;
- calls `rollback()` at teardown.

Tasks:

- [x] 1.1 Fixture helpers:
  - scratch identity, serial or bigint tables, with an optional mixed-case name and a non-`id` column;
  - explicit-id inserts (`OVERRIDING SYSTEM VALUE`);
  - `setval(seq, v, called)`, `ALTER SEQUENCE` increment and maxvalue;
  - a four-role revoke (`postgres`, `anon`, `authenticated`, `service_role`) with a precondition assert;
  - the snapshot;
  - a NOTICE parser.
- [x] 1.2 **T1, fully behind identity:** ids 1–5, sequence at 1, not called. The next id is 6, and the NOTICE gives old 1, new 6, max 5.
- [x] 1.3 **T2, partly behind serial:** rows 1–10, `setval(4, true)`. The next id is 11, and the NOTICE gives old 5.
- [x] 1.4 **T3, equal and not called:** the next id is 4.
- [x] 1.5 **T4, equal and called:** the sequence is unchanged, and no NOTICE names it.
- [x] 1.6 **T5, ahead:** `setval(50, true)` and `setval(50, false)` over rows 1–3. Both are unchanged, including `is_called`.
- [x] 1.7 **T6, empty table:** unchanged, and the first insert gets 1.
- [x] 1.8 **T7, increment decides behind:** rows 1–25, `INCREMENT BY 10`, `setval(20, true)`. Unchanged.
- [x] 1.9 **T7b, increment sets the landing:** rows 1–25, `INCREMENT BY 5`, `setval(20, true)`. The next id is 30.
- [x] 1.10 **T8, large ids and quoting:**
  - a mixed-case table with a bigint `"Id"` holding 3000000000 gets next id 3000000001;
  - a table with two behind sequence columns has both advanced.
- [x] 1.11 **T9, partitions:** one NOTICE names the parent and none names a partition. On PG 15 this can't go red without the `relispartition` filter, so it is a regression guard only.
- [x] 1.12 **T10, schema scope:** a behind table in a throwaway schema is unchanged and not counted in `<m>`.
- [x] 1.13 **T11, locks:** compare `pg_locks` for this backend (`mode = 'ShareRowExclusiveLock'`, relation `relkind IN ('r','p')`) with the set the pre-run predicate reported behind, plus those tables' partitions. This proves the lock statement ran, not that it blocks anyone.
- [x] 1.14 **T12, re-apply:**
  - the second run emits no `advanced` NOTICE and summarises 0 advanced, which also covers the empty-lock-list path;
  - every snapshot is identical between the two runs.
- [x] 1.15 **T13, cannot advance:**
  - setup: an advanceable behind table A that sorts first, and a behind table B whose sequence has the four-role revoke, with `NOT has_sequence_privilege('postgres', B_seq, 'UPDATE')` asserted first;
  - run in a savepoint: it raises, naming B's sequence and `supabase_admin`, and A is unchanged;
  - after `ROLLBACK TO SAVEPOINT`, set the role again.
- [x] 1.16 **T14, cannot lock:** A sorts first, and table B has a four-role revoke of `UPDATE, DELETE, TRUNCATE`. The run raises, naming B and its owner, and A is unchanged.
- [x] 1.17 **T15, descending:** a table with rows 1–5 whose sequence has `INCREMENT BY -1` is skipped: it is unchanged, and no NOTICE names it. This changed from "raises" after the PR review (user decision, 2026-10-02).
- [x] 1.18 **T16, maximum:** A sorts first, and table B has explicit rows 1–10 followed by `MAXVALUE 5`. The run raises, naming B's sequence, and A is unchanged.
- [x] 1.19 **T17, unadvanceable but not behind:** a not-behind sequence with the four-role revoke. The run completes, and it is unchanged. Its former descending case was dropped once T15 covered descending sequences.
- [x] 1.19b **T19, search_path restored:** after the body runs, `SHOW search_path` equals its value before the run.
- [x] 1.20 **T18, migrated DB is clean:** the predicate over real `public` returns 0 rows. Skip it unless `CI` is set: on dev it depends on what was loaded locally.
- [x] 1.21 **File-text unit tests** in `tests/unit/test_advance_lagging_id_sequences_migration_files.py`, which need no DB (pattern: `test_cyl_noop_redelivery_migration_files.py`):
  - each file exists exactly once;
  - the rollback contains no `setval`, `nextval` or `ALTER SEQUENCE` outside comments, and keeps the header convention;
  - the migration has a top-level `BEGIN`, `SET LOCAL lock_timeout = '5s'` and `COMMIT`;
  - it has a `DO $advance$` block;
  - its `LOCK TABLE` comes before the first `setval`;
  - it contains no `CREATE`, `ALTER TABLE` or `DROP` outside strings or comments;
  - it contains none of #1022's 21 table names.
- [x] 1.22 **Red, part one:** run §1. Every test must fail because the file is missing. Use `assert`, not skip.
- [x] 1.23 **Red, part two:** run with `SEQ1022_RED_SKETCH=1`. Confirm:

  - T5 (the not-called case) and T7 fail on sequence state;
  - T14 fails with "did not raise";
  - T15 fails on sequence state: the sketch advances the descending sequence (re-checked after the review of `e86830e6`);
  - T13 and T16 hit unhandled `InsufficientPrivilege` and `NumericValueOutOfRange` errors instead of the designed exception;
  - T17 fails on a permission error.

  Corrected after the PR review: an earlier version said T13–T16 all failed because nothing raised.

  The sketch emits no NOTICE, so failures in T1–T4, T6 and T12 don't count.

- [x] 1.24 Commit locally. Don't push red.

## 2. Migration (PR 1)

- [x] 2.1 Run `make new-migration name=advance_lagging_id_sequences` and write the migration (design D1–D6), then the rollback (D7). Make only the unit tests in 1.21 green. **Do not run the integration tests yet** (dev-safety rule).
- [x] 2.2 **Dev evidence, which is the first real body run on dev.** Steps:

  - read-only: snapshot every `public` sequence into the scratchpad;
  - confirm the predicate reports exactly `cyl_scanners`. If it reports anything else, stop and ask the user;
  - run `wsl.exe -d Ubuntu -e sh -c 'PATH=$HOME/.local/bin:$PATH make migrate-local'` from the worktree (design D9). Retry if WSL start-up times out. Record `supabase --version`;
  - record the NOTICE (`cyl_scanners`, old 1, new 2), the summary `1 of <m>` if `db push` prints them, and a predicate showing 0 behind;
  - with the user's OK, clean the ledger: delete the `schema_migrations` rows `20261001230000` and `20261002135631` as `supabase_admin`, keep `20261001220000`, and record the result.

  **Recorded 2026-10-02:**

  - **Before:** a read-only snapshot of 68 sequence-backed columns. Exactly one was behind: `public.cyl_scanners_id_seq` (`last_value` 1, not called, `max` 1).
  - **Apply:** `make migrate-local` with supabase CLI v2.92.1, the same version deploy pins. It applied `20261001220000`, `20261001230000` and `20261002135631`, with no errors. `db push --debug` prints the NOTICEs in its wire trace:
    - `advanced public.cyl_scanners_id_seq for public.cyl_scanners.id: next value 1 -> 2 (max = 1)`
    - `advance_behind_sequences: 1 of 68 sequences advanced`
  - **After:** the snapshot differs only in `cyl_scanners_id_seq` (1, called), and 0 sequences are behind.
  - **Ledger:** with the user's OK, deleted `20261001230000` and `20261002135631`. Dev's history now ends at `20261001220000`, matching the main checkout.

- [x] 2.3 Run `uv run --extra test pytest tests/integration/test_advance_lagging_id_sequences.py tests/unit/test_advance_lagging_id_sequences_migration_files.py -v` until green, then the full `tests/integration/` and `tests/unit/` suites.

  **Recorded 2026-10-02 (Windows host):**

  - **New tests:** 25 passed. T18 is CI-only, and also passed on dev with `CI=1`.
  - **Full suites:** every other failure is environment-bound, for one of these reasons:

    - HTTP through Caddy or PostgREST is refused from the Windows host;
    - git-subprocess and bash tests;
    - `os.geteuid`.

    Or it fails identically from the main checkout, without this change: `test_view_excludes_deleted_experiments` and `test_concurrent_first_calls_from_same_new_identity_do_not_lose_an_update`. CI on Linux is authoritative.

  **If 2.3 exposes a bug after 2.2:**

  - edit the file (it isn't pushed);
  - restore any real sequence that a bug moved wrongly to its 2.2 snapshot value (dev only);
  - delete the `20261002135631` ledger row if it's still there;
  - repeat 2.2 and 2.3.

- [x] 2.4 In the main session (subagents can't write `.claude/`), add `### Insert rows with explicit ids` under "Common SQL Patterns" in `.claude/commands/database-migration.md`. It says an explicit-id insert doesn't move the sequence, and that such a migration must also carry the advance body (design D6). Not a bare `setval(max)`, which can move a sequence backwards.
- [x] 2.5 Replace the timestamp placeholder in this change's files with the real timestamp (`20261002135631`).
- [x] 2.6 Run the following and confirm they all pass, with no diff from the first two:
  - `make gen-types`
  - `make erd`
  - `./scripts/lint_migrations.sh origin/staging`
  - `python3 scripts/lint_migration_isolation.py origin/staging`
  - `make pr-body-check BODY=<file>` with `No schema changes.`
- [ ] 2.7 Run `/pre-merge`. Commit, then push once green with the user's go-ahead. Open the PR to `staging` titled `Advance prod's lagging id sequences with a forward-only migration (Part of #1022)`.
- [ ] 2.8 Before the merge, run `git fetch origin staging` and check the newest migration there. If it is later than ours, `git mv` the migration and rollback to a new timestamp, and replace `20261002135631` everywhere: the change files, the migration and rollback headers, and the rollback's `migration repair` line.

- [x] 2.9 `/review-pr` round 1 on #1029, at head `b146929f`.

  **Result:** 5/5 subagents completed with no blocking issues. The review was posted as a comment with the user's OK.

  **Fixed:**

  - the header no longer describes PR 2's copy and pin test as existing;
  - T13, T14 and T16 assert each failure message's prefix, and T14 checks its precondition;
  - T8 asserts that the NOTICE quotes mixed-case names;
  - every test skips when a real sequence is already behind;
  - descending sequences are skipped (user decision), and T15 was rewritten to match;
  - the body pins `search_path`, with a new unit test;
  - the "became behind" check runs first in the behind branch;
  - the lock message says "lacks all of";
  - the `database-migration.md` rule was rewritten to use a `*_readvance_id_sequences_<reason>.sql` migration;
  - design D3, D8 and tasks 1.23 were corrected.

  **Known gaps:** the `lock_timeout` path and the stage-2 re-check are untested (design D8).

  **Prod pre-checks:** moved to §5.0.

- [x] 2.10 A targeted review of `e86830e6` (the post-review fixes), with probes on dev, found nothing blocking. It confirmed that the `search_path` pin resolves every name and resets at COMMIT. Fixed:

  - the test oracle `behind()` now skips descending sequences, as the spec says;
  - the body restores the caller's `search_path` as its last statement (new T19, and the unit test now pins first and last statement);
  - descending sequences are skipped before any read, so they need no privileges;
  - T15's red-phase line and T17's scope were corrected.

## 3. Guard (PR 2, code only)

- [x] 3.1 **Tests first, integration** (`tests/integration/test_sequences_behind_check.py`, using `sequence_fixtures`):
  - the guard's rows equal the set the body's NOTICEs advance;
  - afterwards, the guard returns no scratch rows;
  - it runs under `default_transaction_read_only=on`.
- [x] 3.2 **Tests first, unit** (`tests/unit/test_check_health.py`):
  - `sequence_problems` gives one problem per row, naming table, column, max and next value;
  - no rows gives no problems;
  - `main()` exits 1 when there are problems;
  - the SQL file is a single `SELECT` with no `CREATE`, `DO`, `FUNCTION`, DML or `setval`.
- [x] 3.3 **Tests first, unit** (`tests/unit/test_advance_body_copies.py`):
  - every `DO $advance$` block in `scripts/sql/advance_behind_sequences.sql` and in `supabase/migrations/*_readvance_id_sequences_*.sql` equals the migration's, compared line by line;
  - the `seed-gravi` target pipes the seed and then the advance script, and the seed header names the target.
- [x] 3.4 **Tests first, unit** (`tests/unit/test_deploy_sequence_check.py`). For each of the prod and staging jobs:
  - the step is the last one before Cleanup, after "Rollback on failure";
  - it has no `if:`;
  - it has `timeout-minutes`;
  - its psql call has `-X -At`, `ON_ERROR_STOP=1`, `-e PGOPTIONS=` with `default_transaction_read_only=on`, and the SQL file on stdin;
  - it has both `::error` titles;
  - Rollback's `if:` is unchanged, and `test_deploy_staging_supersede_and_pin.py` stays green.
- [x] 3.5 Write `scripts/sql/sequences_behind.sql` (design D10).
- [x] 3.6 Add `sequence_problems` and `check_sequences` to `scripts/check_health.py`, wire them into `main`, and update the docstring (lines 4–15).
- [x] 3.7 Add `scripts/sql/advance_behind_sequences.sql`, the `make seed-gravi` target, and the seed header line.
- [x] 3.8 Add the `deploy.yml` steps for prod and staging (design D10), each with the "must stay after Rollback" comment.
- [x] 3.9 Docs:
  - the `make check` text in `README.md:76`, `Makefile:32` and the `pr-checks.yml:1321` step name;
  - an `## Id sequences behind their data` section in `_WIKI/SUPABASE/README.md`. It explains the annotation, says the fix is a `*_readvance_id_sequences_<reason>.sql` migration and never a hand `setval` on prod, and says to check deploy-gated issues by hand. For dev, it gives the `psql < scripts/sql/advance_behind_sequences.sql` one-liner;
  - a bullet in `_WIKI/SCHEDULEDJOBS/weekly-backup.md` under "Notes on what this does not do": a restore reproduces the dumped sequence state, dumps from before PR 1's prod deploy carry 21 behind sequences, so run `scripts/sql/sequences_behind.sql` after any restore.
- [ ] 3.10 Manual check: on dev, run `make seed-gravi && make check`. Record that there are no behind sequences.
- [ ] 3.11 Run `/pre-merge` (including `pre-commit run --all-files` and the full unit suite). Open PR 2 to `staging` titled `Fail deploy and make check when an id sequence is behind its data (Part of #1022)`.
- [x] 3.12 Draft the `make load-test-data` issue: localhost-only URL derived from `.env.dev`, a sequence reset that runs even after a partial load, and the exit code passed on. Show it to the user, and post it only with their OK. **Filed as #1033** (2026-10-03, with the user's OK).

## 4. Staging verification (after PR 1's staging deploy)

- [x] 4.1 Before: on 2026-10-02, staging had 0 of 68 behind.
- [x] 4.2 The staging deploy log: record whether `advance_behind_sequences: 0 of <m>` appears. That answers whether `db push` prints NOTICEs (design D5).
- [x] 4.3 Run the read-only predicate against `bloom_v2_staging-db-prod-1` over ssh, with the SQL on stdin and no `$$` in a double-quoted remote command. Expect 0 behind, and record the result.

  **Recorded 2026-10-03:**

  - **Deploy:** #1029 merged as `5ca8aed5`. Staging deploy run 37141822090 (Deploy to Staging: success) applied `20261002135631` at 18:03Z.
  - **4.2:** its log shows `advance_behind_sequences: 0 of 68 sequences advanced`, so `db push --debug` does put the NOTICEs in the deploy log.
  - **4.3:** the read-only predicate over staging's 68 sequence-backed columns found 0 behind, and `20261002135631` is in staging's `schema_migrations`.

## 5. Prod verification (after the curated staging→main promotion; each prod read needs the user's OK)

- [ ] 5.0 Before approving the prod deploy (read-only, with the user's OK; raised in the PR review):

  - ~~Ask whether an external writer sends `id`~~: **answered from the code** on 2026-10-02 (design Context). Neither `bloom-desktop` nor `bloom-desktop-pilot` ever sends an `id`, so the high ids are historical rows copied in with their ids, and no live writer brings the collisions back. The distribution query (`count`, `min`, `max` above each sequence) is now optional context only.
  - List every `public` column whose default calls `nextval` but which `pg_get_serial_sequence` doesn't find. Expect 0.
  - Take a fresh snapshot of every sequence (`last_value`, `is_called`, `max`), then take it again after the deploy, and diff the two.

- [ ] 5.1 Before: on 2026-10-02, 21 of 65 behind (design Context).
- [ ] 5.2 Approve the prod deploy outside 05:00–06:30 UTC (pg_cron). If 4.2 showed NOTICEs, record the 21 `advanced` lines and `21 of <m>`.
- [ ] 5.3 Run the read-only predicate against `bloom_v2_prod-db-prod-1`. Expect 0 behind, and record the result. No hand `setval` or other write on prod.
- [ ] 5.4 With the user's approval, close the issue by hand, linking 5.3.
- [ ] 5.5 Ask about adding a line to `talmolab/sleap-roots-pipeline` `docs/bloom-integration/roadmap.md`.
- [ ] 5.6 Ask about filing the write-back retry-gap issue, and about correcting run 2's 5 rows. **Filed 2026-10-03** with the user's OK: #1034 (retry gap) and #1035 (run 2's rows; the data fix itself still needs its own go-ahead). Also #1036 (types drift, low priority).
- [ ] 5.7 After PR 2's step has run green on staging and prod, run `/openspec:archive fix-prod-sequences-behind`.
