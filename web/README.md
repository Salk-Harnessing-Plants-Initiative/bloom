# bloom/web

An HTTP API and web front-end for Bloom, written in Next.js.

## Mixpanel

Currently uses Mixpanel for logging user behavior. Note that this currently happens on the server side - this only works with Next.js >=13.

## Cylinder pipeline runs

`/app/cyl-pipeline-runs` lists every member's cylinder pipeline runs, `/app/cyl-pipeline-runs/[runId]` shows one run's scans, and each experiment page shows its 10 most recent runs (OpenSpec change `add-cyl-pipeline-ui`). They read `cyl_pipeline_runs`, `cyl_pipeline_run_scans` and the `cyl_pipeline_run_experiments` view through Supabase as the signed-in user.

- **Live, never polled.** Each view subscribes to Realtime `postgres_changes` on a channel of its own, refetches on every `SUBSCRIBED` (at most one extra refetch per 2 s window), buffers events during a fetch, and merges each event into the held row. Nothing polls: every read follows mount, a `SUBSCRIBED`, a user action, the panel's debounced membership check for an unknown run, a one-off experiment-names lookup for a run that arrives live, or a batched lookup for rows that turn failed. Nothing calls the workflows `GET /runs/{id}` route, which shares the trigger's rate limit. The sync logic is in `lib/cyl-pipeline/`: the pure `realtime-reducer.ts` and `resync-scheduler.ts`, and the `use-live-sync.ts` hook, all unit-tested; the fixtures there are real Realtime payloads.
- **State from counts.** A run's label comes from its done and failed counts first, then its `status` (`lib/cyl-pipeline/run-display.ts`), because a `complete` run can have failures and runs can freeze in `running` or `queued`.
- **Runs reach traits only through their requested scans.** Links come from `cyl_pipeline_run_scans`: a "Scan images" link per scan, and traits-page links at the run's most common wave and plant age. Only trait sources written since #976 carry their run (`cyl_trait_sources.cyl_pipeline_run_id`); older ones are never backfilled, and the provenance `pipeline_run_id` is still NULL (bloom#864). So `lib/cyl-pipeline/no-provenance-joins.test.ts` fails if these directories, or the `@/lib` modules they import, match traits by run id.

## Cylinder trait export

A trait export is one recipe's traits for an experiment (optionally one wave and/or plant age) or one scan, delivered as a zip of `<stem>.csv`, `<stem>.export.json` and `<stem>.excluded.csv` (OpenSpec change `add-cyl-trait-csv-export`; file conventions in `_WIKI/SUPABASE/trait-recipes.md`). The code is in `lib/cyl-trait-export/`.

Routes, all under `/api/cyl/trait-export/`:

| Route                                            | What it does                                                                                 |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| `GET recipes?experiment=&wave=&age=` or `?scan=` | The selection's recipes with their definitions, the default and `n_selected`, for the dialog |
| `POST jobs?…&recipe=&chosen=default\|user`       | Starts an export job; `202 {job_id}`                                                         |
| `GET jobs/{id}`                                  | `{status, phase, done, total, detail?, filename?}`                                           |
| `GET jobs/{id}/download`                         | The zip once `ready`; `409` before                                                           |
| `DELETE jobs/{id}`                               | Cancels a running job or drops a finished one; `204`                                         |

- **Checks, in order.** `403` for any `Sec-Fetch-Site` other than `same-origin`; `401` unless the cookie session's token verifies with GoTrue (`503` if GoTrue is unreachable or errors); for a job start, `401` "session expires too soon" with under `MIN_SESSION_SECONDS` left; `422` for bad parameters; `429` for the job limits (a user's own running job id is returned); `404` for an experiment the user cannot see, or an empty selection on a job start (the listing answers `200` with `n_selected` 0); `409` if the selection changes while it is read, or if a job was cancelled before its build started; `502` for a database error. Errors are `{ detail }` with fixed wording, never PostgREST's text. `HEAD` is refused.
- **Reads.** Every read uses the signed-in user's token. `cyl_scans_extended` does not apply RLS, so access is decided by the RLS-filtered `cyl_experiments` check that runs first. `lib/cyl-trait-export/db.ts` wraps every PostgREST request in one process-wide semaphore (`PG_CONCURRENCY`), turns off postgrest-js's own retries, never aborts a request once issued (a cancelled export stops issuing them and discards late results; one listing in flight per user), and reads in batches of `BATCH_SCANS` scans (recipe listings in batches of `LISTING_BATCH_SCANS`, one call for every current experiment), always with an explicit recipe key. Any read error or integrity failure fails the job; nothing partial is served.
- **Limits.** The constants are in `lib/cyl-trait-export/limits.ts`: `BATCH_SCANS`, `LISTING_BATCH_SCANS`, `PG_CONCURRENCY`, `MAX_RUNNING_JOBS`, `MAX_JOBS_PER_USER`, `MAX_HELD_BYTES`, `RUNNING_JOB_RESERVE_BYTES`, `MIN_SESSION_SECONDS`, `EXPORT_MAX_SECONDS`, `RETAIN_SECONDS`, `SELECTION_PAGE_SIZE` and `CSV_SLICE_CELLS`.
- **In-process jobs.** Jobs and finished zips live in memory in the one `next start` process, shared through a `globalThis` symbol. A restart or deploy loses running jobs and finished zips; the dialog reports that as an interrupted export. Running more than one `bloom-web` replica would need a durable job store instead.
