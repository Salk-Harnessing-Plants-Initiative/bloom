## Why

The cylinder pipeline backend is live on staging, but the web app has no way to start a run or watch one. bloom#15 has been narrowed to **design §10** of `sleap-roots-pipeline/docs/superpowers/specs/2026-07-06-a4-request-driven-pipeline-design.md`. §10 asks for:

- Run actions with multi-select;
- a params panel;
- a pre-check;
- a shared live "Pipeline runs" panel with a per-scan drill-down;
- results in the *existing* trait views.

Three §10 items can't be delivered honestly against today's backend: params (#897), skip prediction (#898) and status labels (#857/#706/#710). v1 ships a truthful subset of each; design.md Context has the evidence. Two further §10 items are deferred: blob links (#899) and requester display names.

## What Changes

- **New capability `cyl-pipeline-ui`** in `web/`:
  - **Trigger proxy.** `POST /api/cyl/pipeline`: CSRF-checked, forwards the user's JWT, errors made safe to show.
  - **Run actions + confirm dialog.** Actions on the scan, experiment/wave and accession pages, plus "re-run" in the drill-down. The dialog shows read-only resolved params, a trait pre-check and concurrent-run/large-run notices, and always sends `params: {}`.
  - **Live runs views.** A runs list, a per-run drill-down and an experiment-page panel, kept current by Realtime only. No polling.
  - **Results links.** Existing scan pages, and the existing traits page deep-linked to the run's wave and plant age. `TraitExplorer` gains optional `wave`/`age` search params.
- **`cyl-pipeline-trigger`, one ADDED and one MODIFIED requirement (bloom#901).** The trigger batches its id-list filters under the gateway's URL limit, and skips the dedup preview when `params` is `{}`, where it can only ever return 0. Without this, any run over about 1,300 scans, including a plain experiment run, fails with `414`.
- **`cyl-pipeline-runs`, one ADDED requirement.** A read-only `security_invoker` view `cyl_pipeline_run_experiments` and an index on `cyl_pipeline_run_scans(scan_id)`. No table, column, RLS or publication changes.
- **Docs.** The workflows README trigger example, `_WIKI/SUPABASE`, and `web/README.md`.

## Out of scope

- param overrides (#897);
- skip prediction (#898);
- blob links (#899);
- the prediction and analysis viewers (bloom#15 phases 3–4);
- notifications (sleap-roots-pipeline#18);
- trait export, including the per-param-set download goal (#865; forward compatibility in design D8);
- requester display names;
- cancelling runs;
- a "stuck run" threshold;
- any run-keyed trait or source listing (design D6);
- changes to the trigger route, the poller, RLS or the publication.

## Impact

- **Affected specs:** ADDED `cyl-pipeline-ui`; one ADDED requirement in `cyl-pipeline-runs`; one ADDED and one MODIFIED requirement in `cyl-pipeline-trigger`. No active change touches `cyl-pipeline-trigger`. `fix-cyl-pipeline-run-scan-status` modifies other capabilities' requirements, so there's no archive-ordering collision.
- **Affected code:**
  - **PR 1 (schema):**
    - `supabase/migrations/20260924120000_add_cyl_pipeline_run_experiments.sql` and its rollback;
    - `tests/integration/test_cyl_pipeline_run_experiments.py` and `test_cyl_scan_latest_source_precheck.py`;
    - the view entry in the four generated `database.types.ts` copies (`web/lib/`, `packages/bloom-js`, `packages/bloom-fs`, `packages/bloom-nextjs-auth`);
    - `_WIKI/SUPABASE/{erd,README}.md` and `services/workflows/README.md`.
  - **PR 2 (traits deep link):** `web/app/app/traits/[speciesId]/[experimentId]/{page.tsx,TraitExplorer.tsx}` plus two new helpers.
  - **PR 5 (trigger batching):** `services/workflows/pipeline.py`, a new `services/workflows/postgrest_batches.py`, their tests, and `services/workflows/README.md`.
  - **PR 3, 4, 6 (web):**
    - new: `web/lib/cyl-pipeline/**`, `web/components/cyl-pipeline/**`, `web/components/nav-sections.ts`, `web/app/app/cyl-pipeline-runs/**`, `web/app/api/cyl/pipeline/route.ts`;
    - edited: `LiveIndicator.tsx` (optional `state` prop), `web/app/app/layout.tsx` (imports `nav-sections`), the experiment, accession and scan pages, `caddy/Caddyfile` (comment only), `web/README.md`.
- **Users:** any signed-in member can trigger runs, as the backend already allows. The rate limit (5 requests per 60 s per user) is shared with video generation.
- **Issues:**
  - Refs bloom#15. Its §10 v1 ships; the issue stays open for phases 3–4.
  - Follow-ups: #897, #898, #899. Fixed here: #901 (batching and the `{}` skip; its non-empty-params row-volume half stays open).
  - Context: #584, #706, #710, #857, #864, #875, #895, #900, sleap-roots-pipeline#71.
