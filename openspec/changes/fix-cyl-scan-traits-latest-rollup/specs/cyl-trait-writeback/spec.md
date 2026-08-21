## ADDED Requirements

### Requirement: cyl_scan_latest_source is maintained on every write

Bloom SHALL maintain a `cyl_scan_latest_source` table (`scan_id BIGINT PRIMARY KEY REFERENCES
cyl_scans(id) ON DELETE CASCADE`, `max_source_id BIGINT`) holding, for every scan that has at least one
`cyl_scan_traits` row, that scan's current `max(source_id)` — the same value `cyl_scan_traits_source`'s
`is_latest` column is defined against (see the `cyl-trait-read` capability). This table SHALL be kept
correct by a trigger on `cyl_scan_traits` covering every write path that can change what "latest" means
for a scan — inserts, updates, and deletes — regardless of whether the write came through the write-back
RPC or `bloom_admin`'s break-glass direct-table access. An `UPDATE` that reassigns a row's `scan_id` to a
different scan SHALL recompute `max_source_id` for BOTH the row's new scan and its former scan — not only
the new one — since a row moving away from a scan can change that scan's own maximum just as much as a
row arriving does. The maintaining write SHALL be serialized per `scan_id` (e.g. via an advisory lock
scoped to `scan_id`; a cross-scan reassignment acquiring both scans' locks in a fixed, e.g. sorted, order
to avoid deadlocking against another reassignment moving rows in the opposite direction) so that two
concurrent writers delivering data for the same scan cannot leave `max_source_id` reflecting only one
writer's data instead of the true combined maximum. Pre-existing rows SHALL be backfilled by a single aggregate query
(`INSERT ... SELECT scan_id, max(source_id) ... GROUP BY scan_id`) run inside the same migration
transaction that creates the table and trigger, with concurrent writers to `cyl_scan_traits` blocked
(not silently missed) for the backfill's short duration so no scan can fall into a gap where neither the
backfill nor a live trigger firing populates its row. Row-level security SHALL be enabled on this table
with the same policy set as `cyl_scan_traits` itself (`bloom_admin` full access, `bloom_agent`/
`bloom_user`/`authenticated` read-only, all permissive) — an unauthenticated (`anon`) caller SHALL NOT be
able to read or write this table, regardless of any table-level grant Supabase applies by default to new
tables.

#### Scenario: A fresh insert sets max_source_id for a new scan

- **WHEN** `insert_cyl_result_envelope` delivers the first-ever trait rows for a scan, all from one
  source
- **THEN** `cyl_scan_latest_source` gains a row for that scan with `max_source_id` equal to that source's
  id

#### Scenario: A rerun updates max_source_id to the new higher source

- **WHEN** a scan already has a `cyl_scan_latest_source` row and a rerun delivers trait rows under a new,
  higher `source_id`
- **THEN** that scan's row is updated so `max_source_id` equals the new source's id

#### Scenario: Deleting the current-latest rows promotes the next-highest source

- **WHEN** the current-latest source's rows for a scan are deleted and an older source's rows remain
- **THEN** that scan's `max_source_id` becomes the remaining older source's id

#### Scenario: A direct break-glass write is also maintained

- **WHEN** `bloom_admin` inserts, updates, or deletes `cyl_scan_traits` rows directly (bypassing the
  write-back RPC)
- **THEN** `cyl_scan_latest_source` is still maintained correctly for the affected scan

#### Scenario: Reassigning a row's scan_id recomputes both the old and new scan

- **WHEN** a row holding a scan's current `max_source_id` is `UPDATE`d to a different `scan_id` (e.g. a
  `bloom_admin` correction of a mis-attributed trait row), and the former scan still has other rows
  remaining
- **THEN** the former scan's `max_source_id` falls back to the true maximum of its remaining rows (not
  left stuck at the departed value), and the new scan's `max_source_id` reflects the true maximum across
  its own existing rows plus the newly-arrived one

#### Scenario: Concurrent writers to the same new scan converge to the true maximum

- **WHEN** two concurrent transactions each deliver the first-ever trait rows for the same brand-new
  `scan_id`, under different `source_id`s, with both transactions in flight before either commits
- **THEN** after both commit, that scan's `cyl_scan_latest_source` row holds the higher of the two
  `source_id`s — not whichever transaction happened to commit last with a value it computed before
  seeing the other's data

#### Scenario: Concurrent writers to an existing scan converge to the true maximum

- **WHEN** two concurrent transactions each deliver a rerun's trait rows for the same existing scan_id,
  under different, higher `source_id`s, with both transactions in flight before either commits
- **THEN** after both commit, that scan's `cyl_scan_latest_source` row holds the higher of the two new
  `source_id`s

#### Scenario: Concurrent writers to different scans do not block each other

- **WHEN** two concurrent transactions each write trait rows for two DIFFERENT, unrelated `scan_id`s,
  with one transaction's write held open (uncommitted) while the other's runs
- **THEN** the second transaction's write completes without waiting on the first — the advisory lock
  serializing writes is scoped to each individual `scan_id`, not broadened to something coarser (a fixed
  key, or the whole table) that would serialize unrelated scans against each other

#### Scenario: The one-time backfill matches a live per-scan computation

- **WHEN** the backfill runs against pre-existing `cyl_scan_traits` data
- **THEN** every scan's resulting `max_source_id` equals a hand-computed `max(source_id)` for that scan's
  rows

#### Scenario: A write concurrent with the backfill migration is not lost

- **WHEN** a write-back call attempts to insert `cyl_scan_traits` rows for a scan while the backfill
  migration's transaction is still open
- **THEN** that write is not silently missed — it either blocks until the migration transaction commits
  and then proceeds (seeing the newly-created trigger, which maintains its scan's row correctly), or, if
  it started before the migration and completed before the backfill's own read, is captured directly by
  the backfill

#### Scenario: An unauthenticated caller cannot read or write cyl_scan_latest_source

- **WHEN** an `anon` (unauthenticated) caller selects from or writes to `cyl_scan_latest_source`
- **THEN** a `SELECT` returns zero rows regardless of how much real data exists, and any
  `INSERT`/`UPDATE`/`DELETE` is rejected by row-level security — even though Supabase's default privileges
  grant `anon` a raw table-level `INSERT`/`UPDATE`/`DELETE` on this table, the same as any new
  public-schema table

#### Scenario: An unauthenticated caller cannot TRUNCATE cyl_scan_latest_source

- **WHEN** an `anon` (unauthenticated) caller attempts `TRUNCATE public.cyl_scan_latest_source`
- **THEN** the statement is rejected for lacking `TRUNCATE` privilege — row-level security does not govern
  `TRUNCATE` at all (a Postgres limitation, not a policy gap), so this privilege must be revoked explicitly;
  without it, `anon` could truncate this table despite already being correctly denied `INSERT` by RLS,
  zeroing out `is_latest` for every scan system-wide via `cyl_scan_traits_source`'s join to this table
