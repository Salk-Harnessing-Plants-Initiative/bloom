## Context

### The incident

Prod's first successful cylinder pipeline run (run 2, Workflow `sleap-roots-pipeline-x68sv`, 2026-10-02) failed 5 of its 24 write-backs on `cyl_trait_sources_pkey`. The table held legacy ids 1–5 with its sequence still at 1. Argo's retry wrote the five as sources 25–29, and that sequence is now past its data.

### Prod's state

A read-only check (issue #1022, 2026-10-02) found **21 other tables behind**:

- **18 at 1**, among them `species`, `people`, `cyl_scanners`, `cyl_datasets`, `cyl_qc_*`, `scrna_*` and `ortho_gene_id_map`. The next default-id insert into any of them collides. That includes `scripts/ingest_scrnaseq.py`, which inserts `scrna_datasets` and `scrna_cells` with default ids.
- **3 partly behind:** `cyl_experiments`, `phenotypers` and `cyl_scientists`.

A second read-only check (`BEGIN TRANSACTION READ ONLY … ROLLBACK`, run as `postgres`) found 65 sequence-backed `public` columns. For every one of them:

- the sequence and its table are owned by `postgres`;
- `postgres` has `UPDATE` on the sequence;
- the increment is 1, and the sequence doesn't cycle.

### Staging and dev

**Staging** has 0 of 68 behind. On staging, 58 of those 68 columns are identity and 10 are `serial`/`bigserial`; 63 are `bigint`. Prod has fewer columns because 13 June–July embedtree/OrthoVec migrations are deliberately held out of `main` (68ee1e08).

**Dev** has one behind: `cyl_scanners`, with `max` 1 and `last_value` 1, never called.

### How sequences fall behind

Identity (`BY DEFAULT`) and `serial` columns both accept explicit ids without moving the sequence. Prod's original load is unrecorded. The repo has loaders that do the same:

- **`make load-test-data`** upserts `test_data/*.csv` through PostgREST. That covers 13 sequence-backed tables, 7 of them on prod's list (plus `cyl_trait_sources`).
- **`scripts/seed_gravi_mock_data.sql`** inserts explicit ids into 8 tables: `cyl_scientists` and 7 `gravi_*` tables.
- **`supabase/_seed.sql`** inserts `cyl_scanners` ids 1 and 2. Nothing has referenced it since f65cef5c (there is no `supabase/config.toml`), so it is treated as dead and left alone.

None of them resets sequences. The weekly backup's plain `pg_dump` restores sequence state exactly, so any dump taken before this fix carries the 21 behind sequences.

### How migrations run

- **Deploy** runs `supabase db push` (CLI 2.92.1, inside `db-prod`). It connects as `supabase_admin` and applies files as `postgres` (`SET SESSION ROLE`), one transaction per file. The database's `statement_timeout` is 5 minutes.
- **`postgres` privileges:**
  - It isn't a superuser.
  - It is a member of `pg_read_all_data`, `anon`, `authenticated` and `service_role`.
  - Supabase's default privileges give those three roles, and `postgres`, all rights on new sequences and tables in `public`.

### Dev DB ledger

The shared dev DB's migration history stops at `20261001180000` (checked 2026-10-02). `make migrate-local` from this worktree would also apply `20261001220000` and staging's `20261001230000`. The main checkout, which serves the dev stack, lacks the latter, so its next `migrate-local` would refuse with "Remote migration versions not found in local migrations directory".

## Goals / Non-Goals

**Goals**

- Advance every behind `public` sequence through the normal migration path. No hand-run SQL against prod.
- Never move a sequence backwards, and never touch one that isn't behind. Re-applying changes nothing.
- A run that can't finish changes nothing.
- Make a recurrence visible:
  - a deploy check;
  - a `make check` check;
  - a gravi seed that resets what it bypasses.

**Non-Goals**

- Supabase-managed schemas. Bloom creates no schema of its own.
- `make load-test-data`. It needs host safety as well as a reset, and goes to a separate issue.
- `supabase/_seed.sql`, which nothing runs.
- Run 2's 5 stale `failed` rows, and the write-back retry gap.
- Reconstructing how prod was loaded.

## Decisions

### D1. "Behind", computed safely

The spec defines "behind". The implementation:

- **Reads from the sequence relation itself.** It takes `last_value` and `is_called` from the sequence, and `seqincrement` and `seqmax` from `pg_sequence`. It does not use `pg_sequences.last_value`, which is NULL until a sequence is first called, and also when the role lacks privilege.
- **Does the arithmetic in `numeric`.** `last_value + increment` on a bigint sequence at its maximum would otherwise overflow, and a non-behind sequence would raise.
- **Parenthesises any `CASE` inside an `IF`.** PL/pgSQL ends an `IF` condition at the first `THEN`.

A behind sequence gets `setval(seq, max(col), true)`.

Descending sequences (negative increment) are skipped: they count down from their start, so "behind" doesn't apply. None exist in dev, staging or prod. The user chose skipping over failing on 2026-10-02, so that the PR 2 guard wouldn't flag a healthy one forever.

**Rejected: the issue's sketch,** `setval(seq, GREATEST(max, last_value), true)` on every non-empty table. It rewrites ahead sequences:

- One at `last_value` 50, never called, over rows 1–3, jumps from 50 to 51.
- With `INCREMENT BY 10`, it misjudges which sequences are behind.

### D2. Generic over `pg_get_serial_sequence` on `public`

The body visits every column in schema `public` that meets all of these:

- `relkind IN ('r','p')` and `NOT relispartition`;
- the column is live;
- `pg_get_serial_sequence(...)` is not null.

It visits them in table, then column order. On PG 15, `pg_get_serial_sequence` returns NULL for child partitions anyway, so `NOT relispartition` is a guard for later versions. Every name is formatted with `%I`.

### D3. Stages: fail before changing anything

`setval` is not transactional. So the body works in stages, and every way it can fail happens before the first `setval`:

1. **Find and check.** Read `max` and the sequence state, and compute the behind set. For each behind sequence, raise if any of these holds:

   - `current_user` lacks `UPDATE` on the sequence. The message names the sequence and its owner.
   - `current_user` lacks all of `UPDATE`, `DELETE` and `TRUNCATE` on the table. `LOCK … SHARE ROW EXCLUSIVE` needs any one of them, which is also what the multi-privilege `has_table_privilege` tests. The message names the table and its owner.
   - `max + increment > seqmax`, so the next insert would fail anyway.

   A read-permission error also lands here, because this stage only reads.

2. **Lock.** Issue one `LOCK TABLE <distinct behind tables> IN SHARE ROW EXCLUSIVE MODE`. It is skipped when there is nothing to lock. It waits at most `lock_timeout` (5s).
3. **Re-check under the lock.** Recompute stage 1 for the locked tables, checking first whether a table outside the lock set has become behind. Then:
   - re-raise any of its failures;
   - raise if a table outside the lock set has become behind meanwhile. That would take a concurrent explicit-id import, and a retry handles it.
4. **Advance.** `setval` each sequence that is still behind, and emit a NOTICE for each.

These checks run only for behind sequences, so a sequence that isn't behind never causes a failure, provided `postgres` can read it. The user chose fail over skip-and-warn on 2026-10-02.

### D4. Locking

The risk being locked out: a concurrent insert that supplies its own id above the old `max`, landing between stage 1 and stage 4.

`SHARE ROW EXCLUSIVE` blocks writes and `VACUUM`. It doesn't block plain reads, `FOR UPDATE`/`FOR SHARE` reads, or the foreign-key checks that child tables take (`FOR KEY SHARE`). While the lock request waits in the queue, later writers queue behind it; readers never do.

The file follows house style: a top-level `BEGIN; SET LOCAL lock_timeout = '5s'; … COMMIT;`. The timeout can trip in three ways:

- behind a long app transaction;
- behind an anti-wraparound vacuum on `scrna_counts` or `scrna_genes`;
- in a deadlock, which Postgres resolves in about 1s.

In each case the run aborts having changed nothing, and the deploy can be retried. Taking all the locks in one statement, after stage 1, keeps the window small.

Prod's pg_cron jobs run daily at 06:00 UTC and on Sundays at 05:00 UTC, and write tables that aren't behind. The prod deploy should still avoid 05:00–06:30 UTC.

### D5. Reporting

Each advanced sequence gets one NOTICE:

```
advanced <seq> for public.<table>.<col>: next value <old> -> <new> (max = <max>)
```

- `<seq>` is `pg_get_serial_sequence`'s text, which is schema-qualified and quoted where needed.
- `<table>` and `<col>` are formatted with `%I`.

The run ends with a summary:

```
advance_behind_sequences: <n> of <m> sequences advanced
```

`<m>` counts the sequence-backed columns visited. Tests assert counts relative to their fixtures, never totals.

The local supabase CLI version differs from deploy's pinned 2.92.1. So whether `db push` prints NOTICEs into the deploy log is settled by the staging deploy's log (task 4.2). If it doesn't, the read-only checks are the only evidence.

### D6. The advance body, its copies, and re-runs

The body is the migration's `DO $advance$ … $advance$;` block. Its file-level wrapper (`BEGIN`, `SET LOCAL lock_timeout`, `COMMIT`) sits outside the block.

PR 2 copies the block to `scripts/sql/advance_behind_sequences.sql`, wrapped there in its own `BEGIN; SET LOCAL lock_timeout …; COMMIT;`. A unit test compares the two blocks line by line, from `DO $advance$` through `$advance$;`. The isolation lint stops PR 1 from shipping `scripts/`, and the body has to run inside `db push`, so a copy is unavoidable.

If a later deploy goes red because a sequence is behind, the fix is a new migration with a different suffix (`*_readvance_id_sequences_<reason>.sql`) containing the same block, and the pin test covers it as well. Never fix it with a hand `setval` on prod.

### D7. Rollback is a documented no-op

The rollback contains no statement that changes a sequence. Its header explains that the previous values were the bug, and it keeps the STAGING HOT-APPLY ONLY / `migration repair` convention.

### D8. Tests

**Why scratch tables.** `setval` survives rollback, so the tests build their own throwaway tables inside the transaction. Rollback drops those tables and their sequences. Each test also snapshots every real sequence before and after the body runs, and asserts:

- no real sequence decreased;
- the only real sequences that changed are the ones named in an `advanced` NOTICE.

That tolerates a dev worker inserting during a test.

**Privileges in tests.** `postgres` inherits rights from `anon`, `authenticated` and `service_role`, and reads through `pg_read_all_data`. So to remove a right in a test, revoke it from all four: `REVOKE … FROM postgres, anon, authenticated, service_role`. Reads keep working. Probes on 2026-10-02 confirmed that revoking from `postgres` alone leaves `UPDATE` in place.

On dev, `postgres` is also a member of `bloom_writer`, which holds rights on existing tables and sequences. The four-role revoke is enough for scratch objects that `supabase_admin` creates, and T13/T14 assert the precondition before relying on it.

**What stays untested.** The `lock_timeout` path and stage 3's re-check aren't exercised. Testing them needs committed scratch tables and a second connection, which these tests avoid, so they are covered by file-text tests and review only. This was recorded as a known gap after the PR review on 2026-10-02.

**Real behind sequences.** If a real `public` sequence is already behind, every test skips with a message naming it, because running the body would advance it for good. Apply the migration first.

**Red phase.** The rejected `GREATEST` sketch lives only as a string in the test module. An env var `SEQ1022_RED_SKETCH=1` selects it, and it is restricted to `_seq1022_%` tables, so it never touches a real sequence. It is never a file under `supabase/`.

### D9. Applying to dev with `make migrate-local`, then cleaning the shared ledger

The user chose this on 2026-10-02. Dev gets the migration from `make migrate-local`, run from the worktree, which is the real `db push` path. Because of the dev ledger problem (Context), that push also applies:

- `20261001220000`, which the main checkout has;
- staging's `20261001230000`, which the main checkout lacks.

It is the first execution of the body on dev, so it produces the `cyl_scanners` evidence.

**Cleaning up.** After the evidence is recorded, the user OKs deleting, as `supabase_admin`, the `schema_migrations` rows the main checkout has no file for: `20261001230000` and this migration's `20261002135631`. That is what `migration repair --status reverted` does. The `20261001220000` row stays, because the main checkout has that file.

**Why deleting is safe.** Once the main checkout reaches a staging that contains both files, its `migrate-local` re-applies them:

- this migration is idempotent, and advances 0;
- `20261001230000` is re-runnable: its constraint is dropped if it exists and then re-added, the old function signature is dropped if it exists, and the function uses `CREATE OR REPLACE`.

The database objects those two migrations created stay in place between the cleanup and that re-apply.

### D10. Guard (PR 2)

**The query.** `scripts/sql/sequences_behind.sql` is a single `SELECT`, with no `DO` block, function or `CREATE`. It reads each table's `max` and each sequence's `last_value, is_called` through `query_to_xml(format(…))`, and takes `seqincrement` from `pg_sequence`. It works in a read-only transaction; a probe on 2026-10-02 confirmed this. It returns one row per behind sequence.

**The deploy step.** "Check id sequences are not behind (<env>)" sits after "Rollback on failure" and before Cleanup, with `timeout-minutes: 5` and a comment explaining why it must come after Rollback. It reuses each environment's grants-step `compose exec`. Unlike that step, it:

- forwards `-e PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=60s'`, because compose doesn't pass host environment variables into the container;
- runs `psql -X -At -v ON_ERROR_STOP=1 < scripts/sql/sequences_behind.sql`.

What it reports:

- **Rows returned:** one `::error` giving the count; up to 9 more `::error` lines, one per row, since GitHub shows 10 annotations per step; and every row in the log. Then it exits 1.
- **ssh or psql failure:** a separate `::error title=Sequence check could not run`.

Rollback's conditions stay unchanged on both environments. Staging's is pinned by `test_deploy_staging_supersede_and_pin.py`. An earlier failure skips the check, because it has no `if:`.

**`make check`.** A pure `sequence_problems(rows)` turns query rows into problems, `check_sequences(conn)` runs the query, and `main` includes the check. CI runs `make check` before seeding, so there it confirms the migrated state.

**The gravi seed.** A new `make seed-gravi` target pipes `seed_gravi_mock_data.sql` and then `advance_behind_sequences.sql` into `db-dev`'s psql. The seed's header points to the target. A `\i` include won't work, because the seed is fed through stdin and `scripts/` isn't mounted in the container.

**Agreement test.** It runs the guard over the shared scratch fixtures, then the body. It asserts:

- the guard's rows equal the set the NOTICEs report as advanced;
- the guard returns no scratch rows afterwards.

## Risks / Trade-offs

- **Lock waits or deadlock during the prod deploy.** Mitigated by D3/D4: one lock statement over behind tables only, a 5s timeout, and nothing changed on failure, so a retry is safe. There is also guidance on deploy timing.
- **The prod fix can't be proven in CI,** because CI's database is empty. Read-only before/after checks cover it (tasks §4–§5).
- **A red deploy from data drift** keeps the code, by the user's decision. Clearing it takes a re-advance migration (D6). A red sequence check doesn't reopen deploy-gated issues, which are keyed on migration failure only; the runbook says to check them by hand.
- **`make load-test-data` still leaves sequences behind on dev** until its separate issue is fixed. `make check` flags it after PR 2, and the runbook gives the one-line `psql < scripts/sql/advance_behind_sequences.sql` fix.

## Migration Plan

1. PR 1 merges to `staging`. The staging deploy advances 0, and its log answers whether `db push` prints NOTICEs.
2. A read-only staging check finds 0 behind.
3. A curated staging→main promotion, with embedtree held out. Avoid 05:00–06:30 UTC. The prod deploy advances 21.
4. A read-only prod check, with the user's OK, finds 0 behind.
5. PR 2 is branched from `staging` after PR 1 merges, and deploys.
6. The issue is closed by hand with the user's approval, and the change is archived after PR 2's step has run green on prod.
