## ADDED Requirements

### Requirement: is_latest column is maintained on every write, regardless of writer

`cyl_scan_traits` SHALL provide a `NOT NULL` boolean `is_latest` column, and a trigger on the table
(firing on `INSERT`, `UPDATE`, and `DELETE`) SHALL keep it correct for every row of the affected `scan_id`
partition after any write — regardless of whether the write came through the sanctioned write-back RPC
(`insert_cyl_result_envelope`) or a direct table write by a role with table-level access (e.g.
`bloom_admin`'s break-glass grant). `is_latest` for a row SHALL be true iff its `source_id` is
`IS NOT DISTINCT FROM` the maximum `source_id` among all `cyl_scan_traits` rows sharing its `scan_id`
after the write — the same selection rule the `cyl-trait-read` capability's `cyl_scan_traits_source` view
already defines, now backed by this stored column rather than computed per query. The trigger's
maintenance `UPDATE` SHALL only touch rows whose `is_latest` value is actually changing, so the
trigger's own re-firing on that `UPDATE` converges (updates zero rows) rather than recursing
indefinitely.

#### Scenario: A fresh ingest through the write-back RPC sets is_latest correctly

- **WHEN** `insert_cyl_result_envelope` inserts trait rows for a scan with no prior `cyl_scan_traits`
  rows
- **THEN** every inserted row has `is_latest = true`

#### Scenario: A rerun through the write-back RPC flips the prior source's rows to false

- **WHEN** `insert_cyl_result_envelope` inserts a new, higher-`source_id` source's rows for a scan that
  already has an older source's rows
- **THEN** the new source's rows have `is_latest = true` and the older source's rows are updated to
  `is_latest = false`

#### Scenario: A rerun that only re-delivers a subset of traits does not backfill is_latest for the rest

- **WHEN** an older source wrote traits A and B for a scan, and a newer source re-delivers only trait A
- **THEN** the newer source's A row has `is_latest = true`, and both the older source's A row and the
  older/only source's B row have `is_latest = false` — B is not marked latest by virtue of having no
  newer competing row for that same trait

#### Scenario: A direct table write outside the write-back RPC is still maintained

- **WHEN** a role with direct table access (e.g. `bloom_admin`) inserts, updates, or deletes
  `cyl_scan_traits` rows without going through `insert_cyl_result_envelope`
- **THEN** `is_latest` for the affected scan's rows is still correctly maintained by the trigger

#### Scenario: Deleting the latest row promotes the next-highest source

- **WHEN** the row(s) with the current-maximum `source_id` for a scan are deleted
- **THEN** the rows belonging to the next-highest remaining `source_id` for that scan become
  `is_latest = true`

#### Scenario: The maintenance trigger does not recurse indefinitely

- **WHEN** the trigger's maintenance `UPDATE` runs after any write
- **THEN** it converges within a bounded, exact number of re-firings (the second pass finds no row whose
  `is_latest` still disagrees with the recomputed value, so it updates zero rows and the recursion ends —
  verified by counting actual trigger firings for one write, not merely by observing the call completes)

### Requirement: The is_latest-maintaining trigger function is hardened the same way this capability's write-back RPC is

The trigger function that maintains `is_latest` SHALL pin its owner deterministically and harden its
execution environment (`SET search_path` to a fixed, safe value; schema-qualified references throughout
its body) — the same posture this capability's "Write-back RPC ingests a ResultEnvelope" requirement
already mandates for the write-back RPC itself, applied here because this trigger has the identical risk
shape: a `SECURITY DEFINER` object that executes on every write to `cyl_scan_traits`, including writes
from roles (like `bloom_admin`'s break-glass access) that don't go through the RPC at all.

#### Scenario: The trigger function's catalog metadata shows a hardened execution environment

- **WHEN** the trigger function's catalog metadata is introspected
- **THEN** it is `SECURITY DEFINER` with a pinned `search_path`, and every table/function reference in
  its body is schema-qualified

### Requirement: One-time backfill populates is_latest for pre-existing rows without a single long-held lock

A batched, resumable, `CALL`-able procedure SHALL populate `is_latest` for all `cyl_scan_traits` rows
that predate the maintaining trigger, processing bounded `scan_id`-range batches (the batch parameter is
a range *width* on `scan_id`, not a row count) as independently committed transactions so no single
transaction holds a lock for the duration of the full backfill. The procedure SHALL be idempotent:
re-running it (including after an interruption partway through) SHALL converge to the same correct state
as an uninterrupted run. Because the procedure's batching key and its `max(source_id)` grouping key are
the same column (`scan_id`), a scan's rows can never be split across two batches by construction — this
is a structural property of the algorithm, not a case that needs to be independently tested; what does
need testing is that the batching loop itself covers every `scan_id` exactly once with no off-by-one
skip or double-count at a range boundary.

#### Scenario: Backfill sets is_latest correctly across many scans and sources

- **WHEN** the backfill procedure is run against a fixture with multiple scans, each with one or more
  sources
- **THEN** every row's resulting `is_latest` value matches a hand-computed
  `max(source_id) OVER (PARTITION BY scan_id)` (compared `IS NOT DISTINCT FROM`) oracle

#### Scenario: An interrupted backfill can be resumed by re-running it

- **WHEN** the backfill procedure is run, some rows are reset to an incorrect `is_latest` value
  (simulating an interruption), and the procedure is run again
- **THEN** every row converges to the correct value, with no distinction in outcome from an
  uninterrupted single run

#### Scenario: The batching loop covers every scan exactly once across multiple batches

- **WHEN** the backfill runs with a range width smaller than the total span of `scan_id` values in the
  table, forcing multiple loop iterations, including over a fixture with gaps in its `scan_id` sequence
- **THEN** every distinct `scan_id` is processed by exactly one batch — none skipped, none processed
  twice — regardless of where batch boundaries happen to fall
