# Warn in the pipeline confirm dialog when scans are past their models' validated age

## Why

bloom#971 phase 1 ([decisions](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/971#issuecomment-5937500723)) runs a scan older than its species' model window with that species' highest-age window, instead of failing it.

The cluster side is live:
- predict, talmolab/sleap-roots-predict#50;
- traits, talmolab/sleap-roots#272;
- re-pinned in sleap-roots-pipeline #112 (traits) and #113 (predict), and deployed with `argo template update` per #115.

The user chose to run these scans by default, with a visible warning. Today, though, the confirm dialog (bloom#15) gives no sign that, say, day-28 arabidopsis is predicted by models validated up to day 14. Nor does it warn that scans of a species with no model (sorghum, alfalfa, …), or scans younger than every window, will fail at predict, where Bloom later records only "no result produced for this scan by write-back". Prod's Run button is on, so lab members meet both cases with no flag. The model cards live only in the wandb registry, so Bloom has to read them to warn.

## What Changes

- **New capability `cyl-model-catalog`.** The workflows route `GET /model-cards` lists the production model cards from the wandb registry with one direct GraphQL query over `httpx`: the same request and auth wandb's own client uses, bounded at 5 s per request and 15 s overall. It validates them as sleap-roots-contracts `ModelCard`s. The result is cached for 5 minutes and warmed at startup; the route is authenticated and outside the shared rate limit (design D1–D3).
- **Web proxy `GET /api/cyl/pipeline/model-cards`,** behind the trigger's switch and session check (D4).
- **Confirm dialog** (a MODIFIED of its requirement; D5, D6). Only scans with images count.
  - **Past-window warning:** groups above their species' highest model window.
  - **No-model warning:** groups whose species has no model, or that are younger than every window.
  - **Block:** Confirm is disabled only when **no** scan has a model.
  - **Failed check:** a failed, timed-out or empty card read shows a muted "Couldn't check the models' age ranges." and never blocks.
  - **Caption:** the Parameters caption now points at model choice (#897), not param overrides.
- **Secret.** `WANDB_API_KEY` goes to the `workflows` service only, and is required in prod and staging (D7).
- **Dependencies.** `services/workflows` raises `sleap-roots-contracts` to `>=0.1.0a9` and adds nothing else. The `wandb` library isn't used: the version the image would install routes its API through a bundled Go service (design D1).

## Impact

- **Specs:**
  - `cyl-model-catalog`: new, ADDED.
  - `cyl-pipeline-ui`: ADDED proxy requirement, and MODIFIED "Confirm dialog shows read-only resolved params and a pre-check, without predicting skips". `fix-cyl-pipeline-runs-ui-955` was archived on staging (#1023), and no other active change modifies it (D6).
- **Code, `services/workflows/`:**
  - `model_cards.py`, `main.py`
  - `pyproject.toml`, `uv.lock`, `README.md`
  - `tests/test_contracts_pin.py`, `tests/test_model_cards.py`, `tests/test_main.py`
- **Code, `web/`:**
  - `app/api/cyl/pipeline/model-cards/route.ts` and its test
  - `lib/cyl-pipeline/{model-windows.ts,model-cards.ts,model-cards-proxy.ts}` and their tests
  - `lib/cyl-pipeline/__fixtures__/model-cards.ts`
  - `components/cyl-pipeline/RunPipelineDialog.tsx` and its test
- **Code, deploy and tests:**
  - `docker-compose.{prod,dev}.yml`, `.env.dev.example`, `.github/workflows/deploy.yml`
  - `tests/unit/{test_env_defaults.py,test_video_worker_containers.py}`, plus a new `tests/unit/test_wandb_key_scope.py`
- **Docs:** `DEV_SETUP.md`, `PROD_SETUP.md`, `.env.prod.defaults` (header), `scripts/setup-env-secrets.sh` (comment), `contracts/README.md`.
- **Image:** unchanged apart from the contracts version. Of the 7 containers that share it, only `workflows` gets the key.
- **Operations:**
  - The GitHub secrets `PROD_WANDB_API_KEY` and `STAGING_WANDB_API_KEY` must exist before merge. Without them every staging deploy, and every later prod promotion, aborts at env validation (D7).
  - Outbound HTTPS from the staging `workflows` container to `api.wandb.ai` was checked on 2026-10-02 and works. Prod runs on the same host.
- **Ordering:** merge is gated on the cluster re-pin (D8, task 9.1), which is now satisfied, and on the secrets (task 9.2). The PR stays a draft until both are confirmed.
- **Related:**
  - bloom#971 (decision), #897 (phase 2: choosing models), #993 (species with no cards), #994 (younger than the window), #898 (server-side preview), #965 (the dialog);
  - sleap-roots-contracts#13 and #14 (shared matcher, phase 2).
