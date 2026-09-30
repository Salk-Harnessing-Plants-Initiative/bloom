/**
 * Trait-export limits (design D1, D2, D7). BATCH_SCANS starts at 100; tasks.md 7.3
 * sets it from staging measurements so each RPC call's p95 stays under 4 s against
 * the 8 s statement timeout.
 */

/** Scan ids per RPC call. */
export const BATCH_SCANS = 100

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

/** Rows per `cyl_scans_extended` keyset page. */
export const SELECTION_PAGE_SIZE = 1000

/** Cells per CSV slice handed to the zip deflater. */
export const CSV_SLICE_CELLS = 50_000
