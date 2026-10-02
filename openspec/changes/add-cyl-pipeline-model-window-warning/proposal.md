# Warn in the pipeline confirm dialog when scans are past their models' validated age

## Why

bloom#971 phase 1 ([decisions](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/971#issuecomment-5937500723)) lets a scan older than its species' model window run with that species' highest-age window, instead of failing.

The cluster side has merged:
- talmolab/sleap-roots-predict#50: `choose_models` matches such a scan at the window maximum (`past_window_age`).
- talmolab/sleap-roots#272: `choose_pipeline` does the same for the traits pipeline.

Neither stamps anything in provenance. The user decided that results are told apart afterwards from the saved params and models plus the registry's windows.

What's missing is the **warning**. The user chose to run these scans by default, with a visible warning in the confirm dialog, rather than behind an opt-in. Today the dialog (bloom#15, `RunPipelineDialog`) shows each `(species, mode, age)` group with no hint that, for example, arabidopsis at day 28 is predicted by models validated up to day 14.

On staging, 21,902 cyl scans are older than their species' window. 92 experiments mix in-window and past-window scans, accounting for 12,336 of those scans.

Bloom has no copy of the model cards. They live only in the wandb model registry (`sleap-roots-models`, alias `production`), so the dialog needs a way to read their windows.

## What Changes

- **Workflows service: `GET /model-cards`** (`/workflows/model-cards` through Caddy).
  - It lists the production model cards with the `wandb` library and validates each as a sleap-roots-contracts `ModelCard`. This is the same read predict's `WandbRegistrySource` does.
  - It returns `root_type`, `registry_id`, `version` and `selectors` for each card.
  - It caches the list in-process for 5 minutes.
  - It requires a Supabase user JWT. It does **not** use the shared per-user rate limit (5 per 60 s), so opening the dialog never spends the budget `POST /pipeline` needs.
  - It returns 503 when the key is unset or the registry can't be read.
- **Web: `GET /api/cyl/pipeline/model-cards`.** A server-side proxy, next to the POST-only trigger proxy, behind the same `CYL_PIPELINE_TRIGGER_ENABLED` switch and session check.
- **Confirm dialog: a past-window warning.**
  - It fetches the cards alongside its existing checks. Confirm waits for that read, for at most 10 s.
  - Every parameter group whose species and mode have cards, and whose age is above the highest `age_max` among them, is listed in **one counted amber line** in the warnings area. For example: "*90 scans are past their models' validated age and are predicted with the nearest models:* arabidopsis · day 21 — models validated up to day 14 (60); …".
  - If the card read fails or times out, the dialog shows a muted "*Couldn't check the models' age ranges*" and **doesn't** block Confirm.
  - Scans younger than the window, and species with no cards, get no new warning (bloom#994, bloom#993).
- **Secret:** `WANDB_API_KEY` goes to the `workflows` service only, in prod and staging (`PROD_` / `STAGING_WANDB_API_KEY`) and in dev (optional). The video, RNA-seq and pipeline workers don't get it.
- **Dependencies:** `services/workflows` raises its `sleap-roots-contracts` floor from `>=0.1.0a5` to `>=0.1.0a9`, because the `selectors` card shape arrived in a8. It adds `wandb>=0.21.3`. The parameter hash the dedup preview uses is unchanged: contracts' `hashing.py` hasn't changed since its first release.

## Impact

- **Specs:** ADDED requirements only.
  - `cyl-pipeline-trigger`: the model-card endpoint.
  - `cyl-pipeline-ui`: the proxy route, and the dialog warning.
  - The existing requirement "Confirm dialog shows read-only resolved params and a pre-check, without predicting skips" is **not** MODIFIED, because the unarchived `fix-cyl-pipeline-runs-ui-955` MODIFIES it and two unarchived MODIFYs revert each other. See design D6.
- **Code:**
  - `services/workflows/{model_cards.py,main.py,pyproject.toml,uv.lock,README.md}`
  - `web/app/api/cyl/pipeline/model-cards/route.ts`
  - `web/lib/cyl-pipeline/{model-cards.ts,model-windows.ts}`
  - `web/components/cyl-pipeline/RunPipelineDialog.tsx`
  - `docker-compose.{prod,dev}.yml`, `.github/workflows/deploy.yml`, `.env.dev.example`
  - `tests/unit/{test_env_defaults.py,test_video_worker_containers.py}`
  - `contracts/README.md`, `PROD_SETUP.md`
- **Operations:**
  - The user adds the GitHub secrets `PROD_WANDB_API_KEY` and `STAGING_WANDB_API_KEY` **before merge**. `scripts/validate_env.sh` fails a deploy whose compose file references an unset variable.
  - Ideally the key belongs to a wandb service account with registry read access.
- **Ordering:** this PR merges only after predict #50 and traits #272 are re-pinned in sleap-roots-pipeline's cluster templates and `argo template update` has run. Until then the cluster still fails past-window scans, and the warning would describe behaviour it doesn't have.
- **Not changed:**
  - `build_workflow_body`, the vendored Workflow, Argo parameters, stage-in and provenance;
  - the trigger request (its `params` stay inert, per #971);
  - the dialog's "same models and code" copy, which stays accurate in phase 1.
- **Related:**
  - bloom#971 (decision), #897 (phase 2: choosing models), #993 (species with no cards), #994 (younger than the window), #898 (server-side preview), #965 (the dialog), #863 (independent);
  - sleap-roots-contracts#13 and #14 (shared matcher, phase 2).
