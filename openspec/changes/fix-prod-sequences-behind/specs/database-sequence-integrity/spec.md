## ADDED Requirements

### Requirement: Public id sequences MUST NOT be behind their column's data

No sequence-backed column in schema `public` SHALL be behind.

**Sequence-backed columns** are those for which `pg_get_serial_sequence` returns a sequence, whether the column is `serial` or `GENERATED … AS IDENTITY`. A partitioned table is counted once at its parent, never at its child partitions.

**Next value.** A sequence's next value is:

- `last_value + increment` when its `is_called` is true;
- `last_value` when its `is_called` is false.

**Behind.** A sequence is behind when its column has at least one row and `max(col)` is greater than or equal to the sequence's next value.

Only ascending sequences (a positive increment) are in scope: a descending sequence counts down from its start, so "behind" doesn't apply to it.

The sequence-advance body restores this property. The deploy check and `make check` detect a violation. Supabase-managed schemas (`auth`, `storage`, `realtime`, `net`, `pgmq`, `supabase_functions`) are out of scope.

#### Scenario: A freshly migrated database has no behind sequences

- **WHEN** every migration has been applied to an empty database, as in CI
- **THEN** no `public` sequence is behind its column

### Requirement: The sequence-advance body MUST advance exactly the behind sequences

The sequence-advance body is the `DO $advance$ … $advance$;` block used by migration `advance_lagging_id_sequences`, by any later re-advance migration, and by `scripts/sql/advance_behind_sequences.sql`. It SHALL `setval(seq, max(col), true)` every behind `public` sequence, so that the next default insert receives `max(col) + increment`.

It SHALL NOT call `setval` on a sequence that is not behind. Such a sequence keeps its `last_value` and `is_called`.

For each sequence it advances, it SHALL emit one NOTICE:

```
advanced <seq> for public.<table>.<col>: next value <old> -> <new> (max = <max>)
```

- `<seq>` is the text `pg_get_serial_sequence` returns.
- `<table>` and `<col>` are formatted with `%I`.

It SHALL end with the NOTICE `advance_behind_sequences: <n> of <m> sequences advanced`, where `<m>` is the number of sequence-backed columns it visited.

It SHALL lock every behind table against writes, in one statement, before advancing any of them. It SHALL NOT visit child partitions or any schema other than `public`, and SHALL skip descending sequences. It SHALL pin its own `search_path` to `pg_catalog, pg_temp` as its first statement, so a copy behaves the same wherever it runs, and SHALL restore the caller's `search_path` as its last.

#### Scenario: Descending sequences are skipped

- **GIVEN** a table with explicit rows 1–5 whose sequence has `INCREMENT BY -1`, at `last_value` 1, `is_called` false
- **WHEN** the body runs
- **THEN** the sequence is unchanged, no NOTICE names it, and nothing is raised

#### Scenario: A fully behind identity sequence is advanced

- **GIVEN** an identity table holds explicit ids 1–5
- **AND** its sequence is at `last_value` 1, `is_called` false
- **WHEN** the body runs
- **THEN** the next default-id insert receives 6
- **AND** a NOTICE names that sequence and table, with old next value 1, new next value 6, and max 5

#### Scenario: A partly behind serial sequence is advanced

- **GIVEN** a `serial` table holds rows 1–10
- **AND** its sequence is at `last_value` 4, `is_called` true
- **WHEN** the body runs
- **THEN** the next default-id insert receives 11, and the NOTICE gives old next value 5

#### Scenario: A never-called sequence equal to max is advanced

- **GIVEN** a table with `max(id)` 3
- **AND** its sequence is at `last_value` 3, `is_called` false
- **WHEN** the body runs
- **THEN** the next default-id insert receives 4

#### Scenario: A called sequence equal to max is left untouched

- **GIVEN** a table with `max(id)` 3
- **AND** its sequence is at `last_value` 3, `is_called` true
- **WHEN** the body runs
- **THEN** the sequence is unchanged, and no NOTICE names it

#### Scenario: An ahead sequence is left untouched

- **GIVEN** two tables with rows 1–3, each with a sequence at `last_value` 50
- **AND** one sequence has `is_called` true and the other `is_called` false
- **WHEN** the body runs
- **THEN** both sequences are unchanged, including `is_called`

#### Scenario: An empty table is skipped

- **GIVEN** an empty table whose sequence is at `last_value` 1, `is_called` false
- **WHEN** the body runs
- **THEN** the sequence is unchanged, and the first default-id insert receives 1

#### Scenario: Increment decides whether a sequence is behind

- **GIVEN** a table with rows 1–25 and `INCREMENT BY 10`, at `last_value` 20, `is_called` true
- **WHEN** the body runs
- **THEN** it is unchanged, because its next value 30 exceeds 25

#### Scenario: Increment sets where an advanced sequence lands

- **GIVEN** a table with rows 1–25 and `INCREMENT BY 5`, at `last_value` 20, `is_called` true
- **WHEN** the body runs
- **THEN** it is advanced, because its next value 25 is taken
- **AND** the next default-id insert receives 30

#### Scenario: Large ids and quoted names are handled

- **GIVEN** a behind `bigint` identity column named `"Id"`, holding 3000000000, in a mixed-case table
- **AND** a second table with two behind sequence-backed columns
- **WHEN** the body runs
- **THEN** the first table's next default insert receives 3000000001
- **AND** both of the second table's sequences are advanced

#### Scenario: Partitioned tables are visited once at the parent

- **GIVEN** a behind partitioned `public` table whose rows are in two partitions
- **WHEN** the body runs
- **THEN** exactly one NOTICE names the parent, and none names a partition

#### Scenario: Other schemas are not touched

- **GIVEN** a behind table in a schema other than `public`
- **WHEN** the body runs
- **THEN** its sequence is unchanged, and it is not counted in `<m>`

#### Scenario: Only behind tables are locked

- **WHEN** the body has run inside a transaction
- **THEN** among regular and partitioned tables, the transaction holds `ShareRowExclusiveLock` on exactly the tables that were behind before the run, and on their partitions

#### Scenario: Re-applying changes nothing

- **GIVEN** the body has run
- **WHEN** it runs again
- **THEN** no sequence it advanced or left alone changes
- **AND** no `advanced` NOTICE is emitted, and the summary reports 0 advanced

### Requirement: The sequence-advance body MUST fail before changing any sequence when it cannot advance a behind sequence correctly

The body SHALL `RAISE EXCEPTION` **before its first `setval`** when, for any behind sequence:

- `current_user` lacks `UPDATE` on the sequence. The message names the sequence and its owner.
- `current_user` cannot lock the table, because it lacks all of `UPDATE`, `DELETE` and `TRUNCATE` on it. The message names the table and its owner.
- `max(col) + increment` exceeds the sequence's maximum.
- A table that was not locked has become behind by the time the locks are held.

Waiting longer than `lock_timeout` (5s) for the locks also fails the run before any `setval`.

It SHALL NOT skip such a sequence with a warning. When `current_user` can read the sequence and its table, a sequence that is not behind SHALL never cause a failure. A read-permission error, like every other failure, occurs before any `setval`.

#### Scenario: A behind sequence the role cannot advance fails the run and changes nothing

- **GIVEN** an advanceable behind table that sorts first
- **AND** a behind table whose sequence is owned by `supabase_admin`, with all rights revoked from `postgres`, `anon`, `authenticated` and `service_role`
- **WHEN** the body runs as `postgres`
- **THEN** it raises an exception naming that sequence and `supabase_admin`
- **AND** the first table's sequence is unchanged

#### Scenario: A behind table the role cannot lock fails the run and changes nothing

- **GIVEN** an advanceable behind table that sorts first
- **AND** a behind table with `UPDATE`, `DELETE` and `TRUNCATE` revoked from `postgres`, `anon`, `authenticated` and `service_role`
- **WHEN** the body runs as `postgres`
- **THEN** it raises an exception naming that table and its owner
- **AND** the first table's sequence is unchanged

#### Scenario: A behind sequence that would pass its maximum fails

- **GIVEN** an advanceable behind table that sorts first
- **AND** a table with explicit rows 1–10 whose sequence then gets `MAXVALUE 5`
- **WHEN** the body runs
- **THEN** it raises an exception naming the sequence
- **AND** the first table's sequence is unchanged

#### Scenario: Unusual sequences that are not behind are ignored

- **GIVEN** sequences that are not behind and that `postgres` cannot advance, including one with all rights revoked and one with `INCREMENT BY -1`
- **WHEN** the body runs as `postgres`
- **THEN** it completes, and each of those sequences is unchanged

### Requirement: The advance migration's rollback MUST be a documented no-op

The rollback file for `advance_lagging_id_sequences` SHALL contain no `setval`, `nextval` or `ALTER SEQUENCE` outside SQL comments. Its header SHALL explain that restoring the previous values would put sequences back behind their data. It SHALL keep the repository's STAGING HOT-APPLY ONLY / `supabase migration repair` header convention.

#### Scenario: The rollback changes no sequence

- **WHEN** the rollback file is inspected
- **THEN** it contains no `setval`, `nextval` or `ALTER SEQUENCE` outside SQL comments, and its header says why

### Requirement: Every copy of the sequence-advance body MUST match the migration's

`scripts/sql/advance_behind_sequences.sql` and any later re-advance migration SHALL each contain a `DO $advance$ … $advance$;` block identical, line for line, to the one in migration `advance_lagging_id_sequences`.

#### Scenario: The copies match

- **WHEN** each copy's block, from `DO $advance$` through `$advance$;`, is compared with the migration's
- **THEN** they are identical

### Requirement: Deploy MUST fail without reverting code when any public id sequence is behind

Each of the prod and staging deploy jobs SHALL run `scripts/sql/sequences_behind.sql` against the live database. It SHALL do so in a step placed after "Rollback on failure" and before Cleanup, with no `if:` condition and a timeout.

The step SHALL:

- run psql with `-X -At` and `ON_ERROR_STOP`;
- forward `PGOPTIONS` with `default_transaction_read_only=on` and a statement timeout into the database container;
- when rows are returned, emit one `::error` annotation giving the count and up to 9 more naming behind tables, print every row to the log, and fail;
- fail with a distinct annotation when the check itself cannot run.

Because the step comes after "Rollback on failure", its failure SHALL leave the deployed code in place, and Rollback's existing conditions SHALL remain unchanged. See `deploy-migrations` for the earlier database steps and `deploy-health-check` for the other deploy gates.

#### Scenario: No behind sequences

- **WHEN** the query returns no rows
- **THEN** the step passes

#### Scenario: A behind sequence fails the deploy and keeps the code

- **WHEN** the query returns rows
- **THEN** the step fails with annotations naming the behind tables, and the deployed code is not rolled back

#### Scenario: The check cannot run

- **WHEN** ssh or psql fails
- **THEN** the step fails with an annotation saying the check could not run

#### Scenario: Earlier failures still roll back

- **WHEN** a step before "Rollback on failure" fails
- **THEN** Rollback runs as before, and the sequence check is skipped

#### Scenario: The check never writes

- **WHEN** the check's SQL file is inspected
- **THEN** it is a single `SELECT`, with no `CREATE`, `DO`, `FUNCTION`, `INSERT`, `UPDATE`, `DELETE` or `setval`

### Requirement: make check MUST report behind public id sequences

`scripts/check_health.py` SHALL run `scripts/sql/sequences_behind.sql` and report one problem per returned row, naming the table, column, max and next value. `main()` SHALL then exit 1.

#### Scenario: A behind sequence is reported

- **GIVEN** query rows describing one behind sequence
- **WHEN** they are formatted as problems
- **THEN** there is one problem naming that table, and `main()` exits 1

### Requirement: The guard query MUST agree with the sequence-advance body

Given the same fixtures, `scripts/sql/sequences_behind.sql` SHALL return exactly the sequences the sequence-advance body advances.

#### Scenario: The guard and the body agree

- **GIVEN** the shared scratch fixtures
- **WHEN** the guard query runs, and then the body runs
- **THEN** the guard's rows equal the sequences the body's NOTICEs report as advanced
- **AND** afterwards the guard returns no scratch rows

### Requirement: The gravi mock seed MUST leave no public id sequence behind

`make seed-gravi` SHALL feed `scripts/seed_gravi_mock_data.sql` and then `scripts/sql/advance_behind_sequences.sql` to the dev database's psql. The seed file's header SHALL point to that target.

#### Scenario: The seed target resets what the seed bypasses

- **WHEN** the `seed-gravi` target and the seed header are inspected
- **THEN** the target runs the advance script after the seed, and the header names the target
