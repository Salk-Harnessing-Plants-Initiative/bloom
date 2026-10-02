## Why

Researchers get cylinder traits only as a CSV passed by hand through Box (bloom#865). A file built from the traits page's newest-per-scan read (`get_scan_traits`) could silently mix results from different models and code. bloom#976 (staging `57242f36`) added recipe keys, recipe-aware reads and the export sidecar v1. This change is the web exporter for them, per #865's 2026-09-29 decision:
- one recipe per file, the default or one the user picks;
- every left-out scan listed with its reason;
- experiment and scan grains;
- one zip.

## What Changes

- **New capability `cyl-trait-export`** (web):
  - **An in-process export job.** One route starts it, one polls its progress, one downloads `<stem>.zip`, and one cancels it. The zip holds `<stem>.csv`, `<stem>.export.json` and `<stem>.excluded.csv`. Jobs live in the bloom-web process: no table, queue, worker or bucket.
  - **A recipe listing route** for the dialog.
  - **Batched reads.** Jobs read #976's RPCs in bounded scan batches, under one process-wide PostgREST limit, always with an explicit recipe key. Row counts are verified.
  - **Fails loudly.** Any read error or integrity failure fails the job; nothing partial is served.
  - **Verified identity.** Every job route checks the signed-in user against GoTrue, and job ids are random.
  - **Grains:**
    - an experiment, optionally narrowed by wave and/or plant age;
    - one scan, whose file has that scan's trait columns and agrees with the experiment file by column name.
  - **Buttons.** A "Download traits" dialog on the traits page (experiment grain) and on the scan page (scan grain).
- **Shared conventions.** The export conventions every exporter follows (design D4–D6) go into `_WIKI/SUPABASE/trait-recipes.md` §"Export sidecar v1":
  - they replace its stem line, and correct its `selection` notes;
  - they update the example sidecar;
  - they add a researcher section on what the zip holds and how it differs from the Box file.

  bloomctl (#481) follows the same page.

No migration and no new service. Every read runs as the signed-in user, through RLS.

## Impact

- **Specs:** new `cyl-trait-export`, with ADDED requirements only.
  - The new spec cites functions whose requirements live in #976's unarchived `add-cyl-trait-recipe-key`, so this change archives after it.
  - No other active change touches this capability.
- **Code (web only):**
  - new `web/lib/cyl-trait-export/`, `web/app/api/cyl/trait-export/` and `web/components/cyl-trait-export/`;
  - `TraitExplorer.tsx`, plus a new client component beside the scan page;
  - `fflate` in `web/package.json` and the root `package-lock.json`.
- **Tests:**
  - Vitest in `web/`;
  - `tests/integration/test_cyl_trait_export_batching.py`, which ties the web fixtures to #976's functions. It runs in CI's required compose job.
- **Repo config:** `.gitattributes`, `.prettierignore` and `.pre-commit-config.yaml` excludes, so the golden fixtures keep their bytes.
- **Docs:** `_WIKI/SUPABASE/trait-recipes.md`, `_WIKI/SUPABASE/trait-recipes.export.example.json` and `web/README.md`.
- **Landing:** two PRs (tasks.md). PR A's staging checks need #976 deployed.
- **Not touched:** bloomcli/ and bloommcp/. The sleap-roots-analyze and `qc_clean` acceptance is a recorded verification (tasks 1.4, 10.1), not a committed test.

## Out of scope

- A durable async job (table, pgmq, worker, bucket), which is a follow-up if in-process jobs prove fragile (design D1).
- Selections across experiments, arbitrary scan sets and search (#482).
- Downloads by pipeline run (add-cyl-pipeline-ui D8).
- bloomctl's trait download (#481), which shares the conventions above.
- bloommcp's reads (#936).
- Token-authenticated API access.
- Parameter-set filters (#897).
- Moving `TraitExplorer` itself to recipe reads (design Open Questions).
- Downloading the matching predictions (`.slp`). Each pipeline-recipe row's `(scan_id, source_id)` is already their key in `cyl_scan_intermediates` (design D12).
