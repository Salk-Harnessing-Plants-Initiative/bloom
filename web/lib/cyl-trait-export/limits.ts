/**
 * Trait-export limits (design D1, D2, D7). BATCH_SCANS is 100: on staging
 * (tasks.md 7.3, 2026-10-01) it is the largest batch whose p95 stays under 4 s for
 * every RPC against the 8 s statement timeout (get_experiment_traits p95 2.28 s at
 * 100 scans, 4.30 s at 200).
 */

/** Scan ids per RPC call. */
export const BATCH_SCANS = 100

/**
 * Scan ids per `list_trait_recipes` call (tasks.md 7.4). A listing is one HTTP
 * request, and Kong allows 60 s; at BATCH_SCANS, experiment 1's 185 listing calls
 * would take about 123 s with two jobs running. On staging one call over its 18,471
 * scans took 1.26 s, so every current selection lists in one call.
 */
export const LISTING_BATCH_SCANS = 20_000

/** PostgREST requests the feature may have in flight in this process (pool is 10). */
export const PG_CONCURRENCY = 3

/** Running export jobs, across all users. */
export const MAX_RUNNING_JOBS = 2

/** Running export jobs per user. */
export const MAX_JOBS_PER_USER = 1

/** Memory budget: unexpired ready zips plus one reserve per running job. */
export const MAX_HELD_BYTES = 768 * 1024 * 1024

/** Bytes reserved for each running job when checking MAX_HELD_BYTES. */
export const RUNNING_JOB_RESERVE_BYTES = 256 * 1024 * 1024

/** A job start needs at least this much life left on the verified token. */
export const MIN_SESSION_SECONDS = 1800

/** A running job fails after this long. */
export const EXPORT_MAX_SECONDS = 1500

/** A finished job (and its zip) is kept this long. */
export const RETAIN_SECONDS = 600

/** An aborted PostgREST call keeps its semaphore slot until this long after issue. */
export const ABORTED_CALL_HOLD_MS = 9000

/**
 * Rows per `cyl_scans_extended` keyset page (tasks.md 10a.4). Every page waits its
 * turn in the semaphore, so fewer pages keep a listing well under Kong's 60 s while
 * jobs run: experiment 1 is 4 pages and the empty one. No PostgREST `max-rows` is set.
 */
export const SELECTION_PAGE_SIZE = 5000

/** Cells per CSV slice handed to the zip deflater. */
export const CSV_SLICE_CELLS = 50_000
