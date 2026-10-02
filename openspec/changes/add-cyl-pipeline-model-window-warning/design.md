# Design: add-cyl-pipeline-model-window-warning

## Context

bloom#971 phase 1 was decided with the user on 2026-10-01 ([decisions](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/971#issuecomment-5937500723), [update](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/971#issuecomment-5938030166)):

- A scan older than every model window for its species and mode runs with that species' highest-age window, for models and traits pipeline alike. There's no upper limit.
- These scans are included **by default**, with a warning in the confirm dialog, not behind an opt-in.
- Younger-than-window scans and species with no cards are unchanged (#994, #993).
- No new provenance marking.
- No user param overrides.

The cluster half has merged:

- **predict `choose_models`** (talmolab/sleap-roots-predict#50, `79939ee`). `past_window_age(params, cards, overrides)` returns the largest `age_max` among selectors whose species and mode match, taken across all cards. It returns that value only when the scan's age is above it and some non-overridden root type has a card for that species and mode; otherwise it returns `None`. Matching then uses that age, while the params keep the real age.
- **traits `choose_pipeline`** (talmolab/sleap-roots#272, `426ad4d`). The same rule, over `pipeline_selection.yaml`.

Both still need re-pinning in sleap-roots-pipeline's cluster templates, then `argo template update`. That's outside this change, but it gates this PR's merge (D8).

Live registry contents (read 2026-09-30): 8 production cards, all `v0`. The per-species windows are:

| Species | Window (days) |
|---|---|
| soybean | 2–8 |
| canola | 2–13 |
| pennycress | 2–14 |
| arabidopsis | 2–14 (also multiplant cylinder) |
| rice | 2–5 and 6–10 |

## Goals / Non-Goals

**Goals**
- The dialog names every parameter group the cluster will predict past its window, using the same card data and the same rule as predict.
- Defaults stay one click. The warning never blocks Confirm, and neither does failing to check.
- Bloom reads the cards the way predict does, so the two can't disagree about which cards are in production.

**Non-Goals**
- Choosing models, which is phase 2 (#897).
- Suggestions for species with no cards (#993).
- Younger-than-window scans (#994).
- A shared matcher in sleap-roots-contracts (#13, #14).
- Stamping out-of-range in provenance.
- A server-side "N will run" preview (#898).
- Changing the trigger request or Argo submission.

## Decisions

### D1. Read the registry with the `wandb` library (user decision)

`services/workflows/model_cards.py` reads the registry the way predict's `WandbRegistrySource.list_cards` does (sleap_roots_predict/model_registry.py):

- **Where:** the project `"{entity}-org/wandb-registry-{registry}"`, with entity `eberrigan-salk-institute-for-biological-studies`, registry `sleap-roots-models` and alias `production`. These are the same defaults predict uses, held as module constants with no environment override (YAGNI).
- **How:** `wandb.Api().artifact_collections(project_name=..., type_name="model")`, then `api.artifacts(type_name="model", name=f"{project}/{collection.name}")`. Keep only artifacts whose `aliases` include `production`.
- **Card build:** `ModelCard.model_validate({**artifact.metadata, "registry_id": qualified_name.split(":")[0], "version": artifact.version, "weights_checksum": artifact.digest})`.
- **One bad card:** an artifact that fails validation is skipped, with a logged warning.
- **Error:** if at least one alias-carrying artifact exists and none validates, it raises, and the route answers 503. This mirrors predict's `NoReadableModelCardsError`.
- **Empty is valid:** zero alias-carrying artifacts returns `[]`.
- **No network at import:** `wandb` is imported inside the function, so importing the module and running the unit tests never touch the network.

**Alternatives rejected (with the user):**
- Calling wandb's GraphQL API through `httpx`. It's an undocumented internal API, and we'd re-implement the alias filter and version pinning.
- Having the cluster publish the card list. That adds a new step to the pipeline repos.

**Accepted cost:** the `workflows` image grows. The video, RNA-seq, pipeline worker and status-poller containers share that image, so they carry `wandb` too, unused. It also widens the `pip-audit` surface that `pr-checks.yml` enforces with no ignores.

### D2. Response shape and contracts version

`GET /model-cards` returns:

```
{"cards": [{"root_type": "...", "registry_id": "...", "version": "...",
            "selectors": [{"species": "...", "mode": "...", "age_min": 2, "age_max": 14}, ...]}, ...],
 "fetched_at": "<ISO-8601 UTC>"}
```

- **Left out:** `weights_checksum`, `sleap_nn_version` and the other card fields. The dialog doesn't need them, and a smaller surface is easier to keep stable.
- **Contracts floor:** `services/workflows/pyproject.toml` raises `sleap-roots-contracts` from `>=0.1.0a5` to `>=0.1.0a9` and re-locks. `ModelCard.selectors` arrived in a8 (contracts/README.md), and bloomcli and `contracts/pin.json` are already on a9.
- **Why the bump is safe:** the service's one other contracts use, `compute_param_hash` in `pipeline.py`, comes from `hashing.py`, which hasn't changed since contracts' first commit. The dedup preview's hash is identical.
- **README:** `contracts/README.md`'s line "Bloom imports neither `ModelCard` nor `Selector`" becomes untrue and is corrected.

### D3. Cache, auth and rate limit

- **Cache:** a module-level `(fetched_monotonic, cards)` entry under a `threading.Lock`, with a TTL of 300 s.
  - The service runs one uvicorn worker (`tests/unit/test_workflows_single_worker.py`), so it's one cache per container.
  - The refresh happens under the lock, so concurrent cold requests make one registry read.
  - A failed refresh doesn't cache the failure; the next request retries.
  - A stale entry is **not** served after a failed refresh. The dialog fails soft (D5), and serving an expired catalog would hide a broken key until restart.
- **Auth:** `Depends(require_supabase_user)`, as on every route. Cards aren't sensitive, but the endpoint sits behind Caddy's public `/workflows/*`, and auth stops it becoming an anonymous way to make the server call wandb.
- **Rate limit:** not applied. The precedent is the plate-video progress route (`main.py:127-139`). The shared limiter is 5 calls per 60 s per user across every route (`auth.py:24-25`), and the dialog already tells users that limit "is shared with other workflow actions". Spending it on each dialog open would cause 429s on Confirm. The cache bounds the cost: at most one registry read per 5 minutes per container, whatever the request rate.
- **Errors:**
  - `WANDB_API_KEY` unset or blank → 503 "The model catalog isn't configured in this environment." No registry call is made.
  - Any registry or validation failure → 503 "Couldn't read the model catalog." The exception is logged; no detail goes to the caller.

### D4. Web proxy route

`web/app/api/cyl/pipeline/model-cards/route.ts` exports only `GET`, with `dynamic = "force-dynamic"` and `runtime = "nodejs"`.

- **Check order:**
  1. `isPipelineTriggerEnabled()` → 503 with the trigger route's text.
  2. `getSession()` without an access token → 401.
  3. `fetch(`${WORKFLOWS_URL ?? "http://workflows:5100"}/model-cards`)` with the bearer token, `redirect: "manual"` and a 10 s timeout.
- **Responses:** a 200 whose body passes a shape check is passed through. Everything else becomes 502 (timeout 504) with a fixed detail.
- **Helpers:** live in `web/lib/cyl-pipeline/model-cards.ts`, never in `route.ts` (the add-cyl-pipeline-ui rule).
- **No Origin check, unlike the trigger proxy.** Browsers don't send `Origin` on a same-origin GET, so `isSameOrigin` would reject every real request. The route only reads data, changes nothing, and returns nothing per user, so cross-site requests gain nothing (and CORS blocks reading the response). The Cell Ranger logs GET proxy is the precedent.
- **Why not a GET on the trigger route:** `web/app/api/cyl/pipeline/route.test.ts` pins that route to POST only. A sibling route keeps that guarantee.

### D5. Dialog behaviour

**Fetch.** `fetchModelCards()` (in `lib/cyl-pipeline/model-cards.ts`) calls the proxy with a 10 s `AbortSignal` timeout and returns either the cards or `null`. It never throws. Any non-200, bad shape, network error or timeout gives `null`.

The dialog starts it alongside its existing `Promise.all` and adds it to the settle set, so Confirm stays disabled until enumeration, the pre-check, the concurrent-run query **and** the card read have settled. The card read always settles within 10 s, and its failure never sets `state: "failed"`. That follows the existing `getSession().catch(() => null)` precedent for optional data.

The dialog's tests mock this module with `vi.mock`, so their `fetchSpy` assertions, which count exactly one `fetch` (the POST), stay valid.

**Rule.** `pastWindowGroups(groups, cards)` in `lib/cyl-pipeline/model-windows.ts` is a pure function. It's a separate module because `params-summary.ts` is pinned in shape by its test and documented as "throwaway … #897 must not extend it here". For each `ParamsGroup` `{species, mode, age, count}`:

- `max` = the largest `age_max` over every selector of every card with `selector.species === group.species && selector.mode === group.mode`;
- the group is past-window iff `max` exists and `group.age > max`;
- the result keeps `{species, age, count, max}`, in the groups' existing order.

This equals predict's `past_window_age` with no overrides; Bloom sends none in phase 1. The group's species and mode are already normalised the way `resolve_params` normalises them (trimmed, lowercased, `cylinder`). Scans excluded by the stage-in warning are already absent from `groups`.

**Display.** One block in the warnings area, right after the stage-in and no-images lines and before the concurrent runs, `data-testid="past-window"`:

> `<n> scans are past their models' validated age and are predicted with the nearest models:`
> `· <species> · day <age> — models validated up to day <max> (<count>)`, one line per group

Notes on the copy:
- It's singular at n = 1: "1 scan is past its models' validated age and is predicted with the nearest models:".
- Every group is listed, uncollapsed, so a warning can't hide in the collapsed parameter list.
- It uses `text-amber-800` like the neighbouring warnings.
- "Validated up to day N" names only the window's top, because that's all the rule uses, so it's always exact.
- It avoids the dialog's banned phrases (`/will run|will be skipped|reused/i`).

When `fetchModelCards()` returned `null`, a muted `text-stone-500` line shows instead: "*Couldn't check the models' age ranges.*" `data-testid="past-window-unknown"`. When the read succeeded and no group is past its window, nothing is shown.

**Relation to #898.** The parameter groups are client-side and documented as throwaway once a server-side preview exists. This warning is a function of those groups plus the card list, so it moves server-side with them when #898 lands. Until then it duplicates predict's rule in TypeScript. A cross-check test pins the five decision cases to predict's expected outputs (task 5.2). Phase 2 replaces both copies with contracts' shared matcher (#13).

### D6. Spec deltas are ADDED, not MODIFIED

The confirm-dialog content is specified by `cyl-pipeline-ui` "Confirm dialog shows read-only resolved params and a pre-check, without predicting skips". That requirement has a closed, numbered display list and a settle rule. The unarchived `fix-cyl-pipeline-runs-ui-955` MODIFIES that same requirement, and two unarchived changes MODIFYing one requirement silently revert each other at archive (`--strict` can't see it).

So this change ADDS a requirement, "Confirm dialog warns about scan groups past their models' validated age". It states where the block appears (after item 3's stage-in lines) and that the card read joins the settle set. The original list stays untouched.

Once both changes are archived, a later docs change may fold the two requirements into one. That's optional, not a task here.

The endpoint gets an ADDED requirement in `cyl-pipeline-trigger`, which no active change touches.

### D7. Secret wiring (workflows only)

| File | Change |
|---|---|
| `docker-compose.prod.yml` `workflows.environment` | `WANDB_API_KEY: ${WANDB_API_KEY}`. `validate_env.sh` then requires it at deploy. |
| `docker-compose.dev.yml` `workflows.environment` | `WANDB_API_KEY: ${WANDB_API_KEY:-}`. Optional in dev, where the dialog then shows the muted line. `.env.dev.example` gains a commented `WANDB_API_KEY=`. |
| `.github/workflows/deploy.yml` | Add `WANDB_API_KEY=${{ secrets.PROD_WANDB_API_KEY }}` to the prod heredoc and `…STAGING_WANDB_API_KEY` to the staging heredoc. `scripts/verify_env_parity.py` checks they match. |
| `tests/unit/test_env_defaults.py` | Add `WANDB_API_KEY` to `SENSITIVE_INVENTORY`. Never add it to `.env.*.defaults`. |
| `tests/unit/test_video_worker_containers.py` | The worker-parity test (`test_the_worker_carries_the_services_own_credentials`) drops `WANDB_API_KEY` from the `workflows` environment before comparing, as it already does for `WORKFLOWS_CORS_ORIGINS`. The video workers don't get the key (least privilege). |
| Docs | `services/workflows/README.md` (Provisioning and Configuration tables), `PROD_SETUP.md` (secrets table), `.env.prod.defaults` / `.env.staging.defaults` header comments, `scripts/setup-env-secrets.sh`. |

The two GitHub secrets are added by the user before merge (task 0.3).

### D8. Merge is gated on the cluster

This PR merges only after the sleap-roots-pipeline re-pin of the predictor and trait-extractor templates (predict `c25ef52` image, traits `sha-426ad4d`) has merged and `argo template update` has run. The PR body says so, and task 9.1 records the evidence.

Shipping the warning first would promise "predicted with the nearest models" while the cluster still fails those scans.

## Risks / Trade-offs

- **First read after a restart may be slow.** wandb listing took a few seconds when read from a workstation on 2026-09-30, and the dialog waits at most 10 s. A cold cache over 10 s would show the muted line once, until the next dialog open finds the cache warm.
  - Task 9.3 measures the cold read on staging.
  - If it's above about 5 s, warming the cache at startup becomes a follow-up. It isn't built now.
- **Image size and audit surface.** `wandb` adds a bundled Go binary plus its dependency tree. If `pip-audit` finds a CVE in wandb or its dependencies, CI fails until it's resolved. Accepted with the user (D1).
- **The Dockerfile resolves from `pyproject.toml`, not the lock.** So the image gets the newest `wandb` that satisfies `>=0.21.3`, which can differ from the CI lock. That's existing behaviour for every dependency here, and it isn't changed.
- **Traits windows may diverge from model windows.** The dialog reads model cards only. Today `pipeline_selection.yaml` has the same per-species windows. If they diverge, the warning still truthfully describes the models, but not necessarily the traits pipeline. Phase 2's shared catalog (#13, #14) removes that gap.
- **A wandb key in the API service.** The key is read-scoped to the registry if it belongs to a service account (the user provisions it), and it goes only to `workflows`.

## Migration

- No database change, no data backfill.
- Rollback: revert the PR. The endpoint and warning disappear, and the dialog is as before.
