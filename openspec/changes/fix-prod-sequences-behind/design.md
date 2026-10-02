## Context

bloom#1022: in prod, 21 `public` id sequences are behind `max(id)`. The issue body has the full table of `max(id)` against `last_value` / `is_called`, taken 2026-10-02. Staging has 0 of 68 behind.

A read-only prod check on 2026-10-02 (`BEGIN TRANSACTION READ ONLY … ROLLBACK`, as `postgres`) found:

- 65 sequence-backed columns in `public`. Prod is about 15 migrations behind staging, which has 68.
- Every sequence **and** its table is owned by `postgres`.
- `has_sequence_privilege('postgres', seq, 'UPDATE')` is true for all 65.
- No sequence has an increment other than 1 or cycles.
- `postgres` is not a superuser (`rolsuper = f`).

Dev has one behind sequence: `cyl_scanners` (`max` 1, `last_value` 1, `is_called` false). The cause is `supabase/_seed.sql`, which inserts ids 1 and 2 explicitly.

Migrations are applied by `supabase db push` as role `postgres` (after `SET SESSION ROLE postgres`), one transaction per file. Deploy then runs `supabase/grants/schema_grants.sql` as `supabase_admin`.

## Goals / Non-Goals

**Goals**

- Advance every behind `public` sequence to its data, through the normal migration path. No hand-run SQL against prod.
- Never move a sequence backwards and never touch one that isn't behind. Re-applying the migration changes nothing.
- Make a recurrence visible: a read-only check that fails the deploy, plus a dev/CI check.

**Non-Goals**

- Supabase-managed schemas (`auth`, `storage`, `realtime`, `net`, `pgmq`, `supabase_functions`). Their sequences belong to Supabase. Bloom creates no schema of its own; there is no `CREATE SCHEMA` in `supabase/migrations/`.
- Correcting run 2's 5 stale `failed` rows in `cyl_pipeline_run_scans`.
- The retry-can't-update-a-closed-row gap. That is proposed as a separate issue.
- Finding out how prod's data was originally loaded. It isn't recorded, and the user doesn't know.

## Decisions

### D1. "Behind" means the next value handed out is already taken

The value a sequence hands out next is `last_value + increment` when `is_called`, and `last_value` when not. For increment 1:

```
next_value := CASE WHEN is_called THEN last_value + 1 ELSE last_value END
behind     := max(col) IS NOT NULL AND max(col) >= next_value
```

When behind, the migration runs `setval(seq, max(col), true)`, so the next default insert gets `max + 1`. Because `max >= next_value > previous handed-out value`, the sequence only moves forward.

**Rejected: the issue's sketch, `setval(seq, GREATEST(max, last_value), true)` on every non-empty table.** It also rewrites sequences that are already ahead. A sequence at `last_value` 50 that has never been called, over rows 1–3, would become called, and its next value would jump from 50 to 51. That breaks "an ahead sequence is untouched" and makes re-application non-trivial to reason about. Touching only behind sequences makes "a no-op where nothing is behind" literally true: zero `setval` calls.

**Equal cases:**

- `is_called` with `last_value = max`: the next value is `max + 1`, so it is not behind and is left alone.
- Not called with `last_value = max`: the next value is `max`, which is taken, so it is behind and advanced. That is dev's `cyl_scanners`.

### D2. Generic over `pg_get_serial_sequence` on `public`

The loop covers every `(table, column)` in `public` with:

- `relkind IN ('r','p')` and not `relispartition`;
- a live column (`attnum > 0`, not dropped);
- `pg_get_serial_sequence(...)` not null.

`pg_get_serial_sequence` covers both `serial` and `GENERATED … AS IDENTITY`. It resolves through the column's ownership dependency, so a sequence a column merely calls in its `DEFAULT` without owning it isn't seen. No Bloom table uses one. Every name goes through `format('%I')` / `regclass`, so quoting is safe.

LangChain's runtime `checkpoint*` tables in `public` are included if they have a sequence. That's harmless, because advancing a behind sequence is always correct.

### D3. Fail loudly on anything the migration can't fix correctly

`RAISE EXCEPTION`, which rolls the file back and fails `db push` and the deploy, happens when a **behind** sequence:

- isn't advanceable by `current_user` (`NOT has_sequence_privilege(seq, 'UPDATE')`). The message names the sequence and its owner;
- has `seqincrement <= 0`, so D1's arithmetic doesn't hold;
- has `max(col) > seqmax`. `setval` raises on its own here; no extra code is needed.

These checks only apply to behind sequences, so an unusual sequence that isn't behind never blocks a deploy.

The user chose fail over skip-and-warn (2026-10-02). A warning in a deploy log is easy to miss, and a skipped sequence still collides on its next insert. None of these cases exists in prod today (see Context).

### D4. Lock each behind table, and re-check after locking

Prod takes writes during a deploy. Between reading `max` and calling `setval`, a concurrent default insert could take the old `next_value`. The migration's `setval(max, true)` would still be correct, since the next value would be `max + 1`. But an insert that supplies its own id above the old `max` would leave the sequence behind again.

So, for each sequence first found behind, the migration:

1. Runs `LOCK TABLE … IN SHARE ROW EXCLUSIVE MODE`. That blocks `INSERT`/`UPDATE`/`DELETE`, but not reads, until the migration's transaction commits.
2. Reads `max` and the sequence state again under the lock.
3. Calls `setval` only if the table is still behind.

`SET LOCAL lock_timeout = '10s'` stops the deploy hanging behind a long transaction. A timeout fails the migration loudly, as D3 does, and it is safe to retry.

Only behind tables are locked. On staging and dev after the first apply, that is none. On prod it is 20 small-to-medium tables, each `max` coming from an index-only scan of the primary key.

Plain `nextval()` calls that don't insert aren't blocked by the lock. A concurrent one can only move the sequence forward, and `setval` then sets it to `max`. That is never behind, though it could re-issue a value a racing `nextval()` caller already holds but never inserted. No Bloom code calls `nextval()` directly, so this is accepted.

### D5. Reporting

Each advanced sequence gets one `RAISE NOTICE`:

```
advanced public.<seq> for <table>.<col>: next value <old_next> -> <max+1> (max(<col>) = <max>)
```

The run ends with a summary:

```
advance_lagging_id_sequences: <n> of <m> sequences advanced
```

The tests assert on both. In prod's deploy log, the notices are the record of what changed, and the "after" check in tasks.md confirms them.

### D6. Rollback is a documented no-op

`supabase/rollbacks/<ts>_advance_lagging_id_sequences_rollback.sql`:

- has a header explaining that the old values were exactly the bug, so moving a sequence back behind its data only reintroduces the collisions;
- contains no `setval`;
- keeps the repo's existing rollback convention: "STAGING HOT-APPLY ONLY" plus `supabase migration repair --status reverted <ts>`, so the migration can be re-applied.

### D7. Tests use scratch tables, because `setval` is not transactional

`setval` and `nextval` are not undone by `ROLLBACK`. If a test ran the migration body against real tables, it could leave real sequences permanently advanced on the shared dev or CI database.

So each test creates its own `public._seq1022_*` tables inside the open transaction. Their sequences are new objects in that transaction, so the final `pg_conn.rollback()` drops them. The migration body (read with `sql_body`, `BEGIN`/`COMMIT` stripped) then runs under `SET LOCAL ROLE postgres` to match deploy. The generic loop picks the scratch tables up, which also proves it isn't a hard-coded list.

Running the body inside a test also visits real `public` tables, but it advances only behind ones:

- On CI, after `db push`, none are behind (test T9 asserts this).
- On dev, the only behind table is `cyl_scanners`, and advancing it is the intended fix.

The test asserts the NOTICE lines that name the scratch tables, so real-table notices don't make it flaky.

**The privilege-failure test (T8).** The test connection is `supabase_admin`, which is not `postgres`. It creates a scratch table as `supabase_admin` without `SET ROLE`, leaves its sequence behind, then runs the body as `postgres`. That runs inside a savepoint, and the test expects the exception naming the sequence and owner `supabase_admin`.

### D8. Guard (PR 2): one query, two callers, failing the deploy without a code rollback

`scripts/sql/sequences_behind.sql` holds a single plain `SELECT` with no `DO` block and no function:

- It joins the `pg_get_serial_sequence` column set (D2) to `pg_sequences`, whose `last_value` is NULL until the sequence is first called.
- It gets each table's `max` from `query_to_xml(format('SELECT max(%I) AS m FROM %I.%I', …))`.

A plain `SELECT` is needed because it has to run inside `BEGIN TRANSACTION READ ONLY`, and even a `pg_temp` function can't be created in a read-only transaction (checked on dev, 2026-10-02). It returns one row per behind sequence: table, column, `max` and next value. Both callers use this file:

- **`scripts/check_health.py`:** `check_sequences(conn)` appends one problem per row, following the existing pattern (`check_roles`, `check_schema_usage`). It runs in `make check`, and CI's `dev-stack-smoke` runs `make check` after migrations and seeds.
  - `supabase/_seed.sql` and `scripts/seed_gravi_mock_data.sql` gain a `setval` after their explicit-id inserts, in the existing style of `scripts/seed_cyl_mock_data.sql`. Otherwise the check fails dev correctly but noisily.
- **`deploy.yml`:** a step "Check id sequences are not behind (production|staging)" runs after "Apply schema-USAGE grants", the same `compose exec -T db-prod psql -U supabase_admin` pattern. It runs inside `BEGIN TRANSACTION READ ONLY`. Any row prints `::error title=Id sequences behind their data::…` with the rows, and the step exits 1.
  - **Code rollback is excluded.** "Rollback on failure" (`if: failure()`) reverts code to the previous SHA. That can't fix a sequence, and it would undo an unrelated deploy. The guard step gets an `id` (`seq_check_prod` / `seq_check_staging`), and the Rollback step's condition becomes `failure() && steps.seq_check_<env>.outcome != 'failure'`. The deploy is still red; the code stays.
  - The step comes **after** the smoke test, the migrations and the grants, so every earlier step's failure still rolls back exactly as today. A failed earlier step skips this one: it never runs, so its outcome is `skipped`, not `failure`.

The migration in PR 1 can't import `scripts/sql/sequences_behind.sql`, because the isolation lint forbids `scripts/` in a migration PR and the logic must also run inside `db push`. So the "behind" predicate exists twice. PR 2 adds an integration test that builds the same scratch fixtures as PR 1's tests and asserts the guard query flags exactly the cases the migration advances. If the two definitions drift, CI fails.

## Risks / Trade-offs

- **Lock waits during the prod deploy (D4).** Mitigated by locking only behind tables, holding the locks for milliseconds per table, and `lock_timeout` turning a long wait into a loud, retryable failure.
- **The prod fix is unprovable in CI.** CI's database is empty (the bloom#780 pattern). Mitigation: "Part of #1022" only; read-only before/after checks on staging and prod, recorded in tasks.md; #1022 closed by hand after the prod check, with the user's approval.
- **Guard false positives on dev from seeds.** Mitigated by fixing the two seed scripts in PR 2.
- **A deploy blocked by data drift.** That is intended (user decision, 2026-10-02). The red deploy keeps the new code, per D8.

## Migration Plan

1. PR 1 merges to `staging`. The staging deploy applies it as a no-op (0 behind).
2. Read-only check on staging: 0 behind, unchanged.
3. Normal staging→main promotion. The prod deploy applies the migration, and its log shows 20 `advanced …` notices.
4. Read-only check on prod, with the user's OK: 0 behind. Record it in tasks.md.
5. PR 2 (guard) follows, code-only against this change.
6. Close #1022 by hand with the user's approval. Archive after PR 2 deploys.

## Open Questions

- After prod is verified, should a line go into `talmolab/sleap-roots-pipeline` `docs/bloom-integration/roadmap.md`, where prod-run history lives? Ask the user then.
- Filing the "retry can't update a row closed as `failed`" issue, and correcting run 2's 5 rows: each needs the user's go-ahead.
