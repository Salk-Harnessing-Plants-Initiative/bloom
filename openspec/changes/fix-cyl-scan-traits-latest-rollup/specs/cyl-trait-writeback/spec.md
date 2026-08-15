## ADDED Requirements

### Requirement: cyl_scan_latest_source is maintained on every write

Bloom SHALL maintain a `cyl_scan_latest_source` table (`scan_id BIGINT PRIMARY KEY REFERENCES
cyl_scans(id) ON DELETE CASCADE`, `max_source_id BIGINT`) holding, for every scan that has at least one
`cyl_scan_traits` row, that scan's current `max(source_id)` — the same value `cyl_scan_traits_source`'s
`is_latest` column is defined against (see the `cyl-trait-read` capability). This table SHALL be kept
correct by a trigger on `cyl_scan_traits` covering every write path that can change what "latest" means
for a scan — inserts, updates, and deletes — regardless of whether the write came through the write-back
RPC or `bloom_admin`'s break-glass direct-table access. The maintaining write SHALL be serialized per
`scan_id` (e.g. via an advisory lock scoped to `scan_id`) so that two concurrent writers delivering data
for the same scan cannot leave `max_source_id` reflecting only one writer's data instead of the true
combined maximum. Pre-existing rows SHALL be backfilled by a single aggregate query
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
