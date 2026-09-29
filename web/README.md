# bloom/web

An HTTP API and web front-end for Bloom, written in Next.js.

## Mixpanel

Currently uses Mixpanel for logging user behavior. Note that this currently happens on the server side - this only works with Next.js >=13.

## Pipeline runs

`/app/cyl-pipeline-runs` lists every member's cylinder pipeline runs, `/app/cyl-pipeline-runs/[runId]` shows one run's scans, and each experiment page shows its 10 most recent runs (OpenSpec change `add-cyl-pipeline-ui`). They read `cyl_pipeline_runs`, `cyl_pipeline_run_scans` and the `cyl_pipeline_run_experiments` view through Supabase as the signed-in user.

- **Live, never polled.** Each view subscribes to Realtime `postgres_changes` on a channel of its own, refetches on every `SUBSCRIBED` (at most one extra refetch per 2 s window), buffers events during a fetch, and merges each event into the held row. Nothing polls: every read follows mount, a `SUBSCRIBED`, a user action, the panel's debounced membership check for an unknown run, or a one-off lookup for a row that turns failed. Nothing calls the workflows `GET /runs/{id}` route, which shares the trigger's rate limit. The sync logic is pure and tested in `lib/cyl-pipeline/` (`realtime-reducer.ts`, `resync-scheduler.ts`, `use-live-sync.ts`); the fixtures there are real Realtime payloads.
- **State from counts.** A run's label comes from its done and failed counts first, then its `status` (`lib/cyl-pipeline/run-display.ts`), because a `complete` run can have failures and runs can freeze in `running` or `queued`.
- **Runs reach traits only through their requested scans.** Links come from `cyl_pipeline_run_scans`: a "Scan images" link per scan, and traits-page links at the run's most common wave and plant age. Nothing links a trait source to its run (bloom#864), so `lib/cyl-pipeline/no-provenance-joins.test.ts` fails if these directories, or the `@/lib` modules they import, match traits by run id.
